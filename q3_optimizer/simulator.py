"""仿真适配层：把"一组尺寸参数"变成"一组性能指标"。

三个角色：

* `SimResult`        —— 统一的性能指标容器（内部一律用 SI：Hz / A / dB / deg）
* `parse_metrics()`  —— 从 MDE/ALPS 的日志文本里稳健提取指标
* `MockSimulator`    —— **本地自测用的解析模型**，不是真实仿真器；用于在没有
                        服务器/账号时验证优化器收敛性、约束处理和预算逻辑
* `CommandSimulator` —— 真实路径：写候选参数文件 -> 调官方脚本仿真 -> 读日志

⚠️ `MockSimulator` 只是测试替身，其数值没有任何物理意义，**绝不能**当作设计依据。
它的目的是让优化器、约束守卫、断点续跑、预算规划这些工程逻辑在本地就能被测试。
"""

import math
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

# 内部统一单位：增益 dB、UGB Hz、PM/GM deg、电流 A、面积 um^2
DB = "dB"


@dataclass
class SimResult:
    dc_gain_db: float = None
    ugb_hz: float = None
    pm_deg: float = None
    gm_db: float = None
    i_opa_a: float = None
    area_um2: float = None
    corners: dict = field(default_factory=dict)   # corner名 -> {指标}
    ok: bool = False
    error: str = ""
    elapsed_s: float = 0.0
    raw: str = ""

    def metric(self, name):
        return getattr(self, name, None)

    def as_dict(self):
        return {"dc_gain_db": self.dc_gain_db, "ugb_hz": self.ugb_hz,
                "pm_deg": self.pm_deg, "gm_db": self.gm_db,
                "i_opa_a": self.i_opa_a, "area_um2": self.area_um2,
                "ok": self.ok, "error": self.error}


# ---------------------------------------------------------------------------
# 日志指标解析
# ---------------------------------------------------------------------------
# 每条 (正则, 目标字段, 单位换算)。宽松匹配，兼容 MDE / ALPS / Spectre 常见写法。
_PATTERNS = [
    (r"(?:unity\s*gain\s*band\s*width|ugb|gbw|gb\s*w|f-?ugb|fu)\s*[:=]?\s*"
     r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*([kKmMgG]?)(?:hz)?", "ugb_hz", "hz"),
    (r"(?:phase\s*margin|pm)\s*[:=]?\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*(?:deg|degree|°)",
     "pm_deg", None),
    (r"(?:gain\s*margin|gm)\s*[:=]?\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*(?:db)?",
     "gm_db", None),
    (r"(?:dc\s*gain|open[\s-]*loop\s*gain|low[\s-]*frequency\s*gain|a0|\bgain\b)"
     r"\s*[:=]?\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*(db)?", "dc_gain_db", "db"),
    (r"(?:i[_\s-]?opa|itotal|i[\s-]*total|supply\s*current|iq|i[\s-]*supply)\s*[:=]?\s*"
     r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*([uµnm]?)(?:a)", "i_opa_a", "a"),
    (r"(?:area)\s*[:=]?\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", "area_um2", None),
]

_SCALE = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3,
          "u": 1e-6, "µ": 1e-6, "n": 1e-9, "p": 1e-12}


def _apply_scale(value, suffix, kind):
    if kind == "hz":
        return value * {"k": 1e3, "m": 1e6, "g": 1e9}.get(suffix.lower(), 1.0)
    if kind == "a":
        return value * _SCALE.get(suffix.lower(), 1.0)
    return value


def parse_metrics(text: str) -> dict:
    """从仿真日志里提取指标，返回 {'dc_gain_db':..., 'ok': bool}。

    同一指标出现多次时取**最坏情况**：增益/UGB 取最小、PM 取最小、GM 取最大、
    电流取最大。这正是赛题"所有 PVT 条件下最坏情况"的口径。
    """
    out = {}
    worst = {"dc_gain_db": min, "ugb_hz": min, "pm_deg": min,
             "gm_db": max, "i_opa_a": max, "area_um2": max}
    for pat, fieldname, kind in _PATTERNS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            try:
                val = float(m.group(1))
            except (TypeError, ValueError):
                continue
            if kind in ("hz", "a"):
                suffix = m.group(2) if m.lastindex and m.lastindex >= 2 else ""
                val = _apply_scale(val, suffix or "", kind)
            if fieldname not in out:
                out[fieldname] = val
            else:
                out[fieldname] = worst[fieldname](out[fieldname], val)
    out["ok"] = all(out.get(k) is not None
                    for k in ("dc_gain_db", "ugb_hz", "pm_deg", "i_opa_a"))
    return out


# ---------------------------------------------------------------------------
# 真实仿真器
# ---------------------------------------------------------------------------
class CommandSimulator:
    """调用官方 `run_opt_command.sh` 一类命令完成一次仿真。

    真实命令形态拿到后，通过 `cmd_template` 适配，例如：
        "python optimization_runner.py --param_file {param_file} --output_path {out}"
    或
        "bash run_opt_command.sh {param_file} {out}"
    """

    def __init__(self, cmd_template, workdir=".", log_name="output.log",
                 param_writer=None, timeout=1800):
        self.cmd_template = cmd_template
        self.workdir = Path(workdir)
        self.log_name = log_name
        self.param_writer = param_writer
        self.timeout = timeout
        self.n_evals = 0

    def __call__(self, values, param_file, keys, out_dir):
        self.n_evals += 1
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        if self.param_writer:
            self.param_writer(param_file, keys, values, out_dir)
        cmd = self.cmd_template.format(param_file=str(param_file), out=str(out_dir))
        t0 = time.time()
        try:
            subprocess.run(cmd, shell=True, cwd=str(self.workdir),
                           capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return SimResult(ok=False, error="timeout",
                             elapsed_s=time.time() - t0)
        log = out_dir / self.log_name
        text = log.read_text(errors="ignore") if log.exists() else ""
        res = SimResult(**{k: v for k, v in parse_metrics(text).items()
                           if k in SimResult.__dataclass_fields__})
        res.elapsed_s = time.time() - t0
        res.raw = text
        return res


# ---------------------------------------------------------------------------
# 本地测试替身
# ---------------------------------------------------------------------------
class MockSimulator:
    """解析式测试替身：用于在本地验证优化器，**不代表真实电路**。

    刻意构造成有明确权衡的响应面：加大输入对/输出级尺寸能提高增益与 UGB，
    但同时增加面积与电流；补偿电容加大能提高 PM 但降低 UGB。
    这样 DE、约束守卫、多目标权衡都能被真实地驱动起来。
    """

    def __init__(self, seed=0, noise=0.0, cost_s=0.0):
        self.seed = seed
        self.noise = noise
        self.cost_s = cost_s
        self.n_evals = 0

    def __call__(self, values, param_file=None, keys=None, out_dir=None):
        self.n_evals += 1
        if self.cost_s:
            time.sleep(self.cost_s)
        p = dict(zip(keys or [], values))
        # 入参是 SI（m / F）；测试替身内部一律按 µm / pF 计算面积与电流，
        # 否则会把"米"当成"微米"，面积量级差 1e12，分数被彻底带偏。
        UM = 1e6
        PF = 1e12
        w_in = self._get(p, ("M1_w", "NM1_w", "w_in"), 10e-6) * UM
        l_in = self._get(p, ("M1_l", "NM1_l", "l_in"), 1e-6) * UM
        w_out = self._get(p, ("M5_w", "NM4_w", "w_out"), 30e-6) * UM
        l_out = self._get(p, ("M5_l", "NM4_l", "l_out"), 1e-6) * UM
        w_tail = self._get(p, ("M0_w", "NM3_w", "w_tail"), 5e-6) * UM
        m_out = self._get(p, ("M5_m", "m_out"), 2.0)
        m_in = self._get(p, ("M1_m", "m_in"), 1.0)
        cc = self._get(p, ("CC1_value", "Cc", "cc"), 3e-12) * PF      # pF

        # 平方律近似：gm ∝ sqrt(W/L * I)
        gm1 = 0.5 * math.sqrt(max(w_in, 1e-3) / max(l_in, 0.13) * 100e-6)
        gm2 = 0.5 * math.sqrt(max(w_out, 1e-3) / max(l_out, 0.13) * m_out * 200e-6)
        gain = 20 * math.log10(max(gm1 * 1e4, 1e-9) * max(gm2 * 2e3, 1e-9))
        ugb = gm1 / max(cc * 1e-12, 1e-15) / (2 * math.pi)
        p2 = gm2 / max(2e-12, 1e-15) / (2 * math.pi)
        pm = 90.0 - math.degrees(math.atan(ugb / max(p2, 1.0)))
        gm_db = -10.0 - 12.0 * (pm / 90.0)
        i_opa = ((w_tail / 1.0) * 20e-6
                 + (w_out / max(l_out, 0.13)) * m_out * 30e-6)
        area = (w_in * l_in * m_in * 1.5 + w_out * l_out * m_out * 1.5
                + w_tail * 1.0 * 1.5 + cc * 3.0)
        if self.noise:
            rnd = __import__("random").Random(self.seed + self.n_evals)
            gain += rnd.uniform(-self.noise, self.noise)
            ugb *= (1 + rnd.uniform(-self.noise / 100, self.noise / 100))
        return SimResult(dc_gain_db=gain, ugb_hz=ugb, pm_deg=pm, gm_db=gm_db,
                         i_opa_a=i_opa, area_um2=area, ok=True)

    @staticmethod
    def _get(p, names, default):
        for n in names:
            if n in p:
                try:
                    return float(p[n])
                except (TypeError, ValueError):
                    continue
        return default
