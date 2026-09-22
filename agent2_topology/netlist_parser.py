"""SPICE 网表解析器：提取器件、连接关系与尺寸参数。

第②问的两个致命前提（原实现都踩了，实测会直接丢分）：

1. **器件类型不能只看首字母**。真实电路网表常把 MOS 命名为 `NM1/PM1/nmos 实例`，
   原实现 `dtype = toks[0][0].upper()` 会把整张网表的 MOS 全部丢掉（实测 0 器件）。
   现在的判定顺序：标准 SPICE 首字母 → 模型名(nmos/pmos/nch/pch/nfet/pfet) → 记为未识别。
2. **必须保留 .subckt 作用域**。把所有 subckt 的器件扁平合并，会把偏置支路和放大支路
   "缝合"成并不存在的电流镜，进而生成错误约束、算错变量个数。

顺带修掉的数据丢失：

3. `R1 a b 5k` 的阻值、`CC1 outn comp1 vss 1p` 的第三节点与容值，原实现全部丢弃。
4. 注释行原样保留，约减网表回写时不再丢注释。
5. `.param` 表达式的值（如 `w=W1`）按原文本保留，不误当成数字。
6. 支持 X 层次实例在本地 `.subckt` 定义存在时展开（带实例前缀）；未展开的实例
   按 MOS 模型名兜底识别，仍无法识别的保留下来供报告说明。
"""

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# 赛题 PDK 器件取值约束（PDF 第 8 页）
#   晶体管: W 0.13um~20um, L 0.13um~10um, m 1..20(整数)
#   电阻:   Seg 1..20(整数)
#   电容:   L 0.13um~20um
#   面积:   晶体管 f*w*L*m*1.5 / 电容 W*L / 电阻 W*L*2
# 每项为 (min, max, unit, type)；min/max 单位见 unit。
# ---------------------------------------------------------------------------
PDK_LIMITS = {
    "M": {
        "w": (0.13, 20.0, "um", "float"),
        "l": (0.13, 10.0, "um", "float"),
        "m": (1, 20, "", "int"),
    },
    "C": {
        # PDF 仅明确给出电容 L 的区间，W 暂沿用同一区间并在报告中说明
        "w": (0.13, 20.0, "um", "float"),
        "l": (0.13, 20.0, "um", "float"),
    },
    "R": {
        "seg": (1, 20, "", "int"),
    },
    "L": {},
}

PDK_LIMITS_TEXT = {
    "M": "W 0.13u~20u, L 0.13u~10u, m 1~20(整数)",
    "R": "Seg 1~20(整数)",
    "C": "L 0.13u~20u (W 沿用同区间)",
    "L": "以 PDK 器件为准",
}

# 无源器件参数 -> 变量单位（用于 CSV 的 unit 列）
PARAM_UNITS = {
    "w": "um",
    "l": "um",
    "m": "",
    "seg": "",
    "value": "",
}

# SPICE 单位后缀 -> 倍数
SPICE_MULT = {
    "t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3,
    "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15, "mil": 25.4e-6,
}

_NUM_RE = re.compile(r"^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)([a-zA-Z]*)$")

_MOS_MODEL_HINTS = ("nmos", "pmos", "nch", "pch", "nfet", "pfet")
_NMOS_HINTS = ("nmos", "nch", "nfet")
_PMOS_HINTS = ("pmos", "pch", "pfet")

RAIL_HINTS = ("vdd", "vssa", "gnd", "vss", "avdd", "avss", "vcc", "vee")

# 标准 SPICE 器件首字母（除 M / R / C / L / X 外，本工具只做记录不做参数化）
_STD_LETTERS = set("DQVIEFGHKTSWBJZ")
_PASSIVE = ("R", "C", "L")


def is_rail(node: str) -> bool:
    """节点名是否像电源/地轨。"""
    return any(h in str(node).lower() for h in RAIL_HINTS)


def _scale(v):
    """SPICE 数值后缀 -> SI 数值。'2u'->2e-6, '4'->4.0, "'W1'"->None。"""
    if v is None:
        return None
    s = str(v).strip().strip("'\"")
    m = _NUM_RE.match(s)
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    return val * SPICE_MULT.get(m.group(2).lower(), 1.0)


def _looks_like_mos_model(tok) -> bool:
    if not tok:
        return False
    t = str(tok).lower()
    if t in ("n", "p"):
        return True
    return any(h in t for h in _MOS_MODEL_HINTS)


def _mos_polarity(dev) -> str:
    """返回 'n' / 'p' / ''（未知）。不依赖器件名首字母。"""
    m = (dev.model or "").lower()
    if any(h in m for h in _NMOS_HINTS):
        return "n"
    if any(h in m for h in _PMOS_HINTS):
        return "p"
    if m[:1] == "n":
        return "n"
    if m[:1] == "p":
        return "p"
    n = (dev.name or "").lower()
    if n[:2] in ("pm", "mp"):
        return "p"
    if n[:2] in ("nm", "mn"):
        return "n"
    if n[:1] == "p":
        return "p"
    if n[:1] == "n":
        return "n"
    # 最后兜底：衬底接的电源轨（仅在模型缺失时使用）
    if dev.dtype == "M" and len(dev.nodes) >= 4:
        b = str(dev.nodes[3]).lower()
        if any(h in b for h in ("vdd", "vcc", "avdd")):
            return "p"
        if any(h in b for h in ("vss", "gnd", "vee", "avss")):
            return "n"
    return ""


# eq=False -> 按对象身份比较，保证可哈希（识别流程里全程用 is/集合判同一器件，
# 两个字段相同的 Device 不应被视为同一个器件）
@dataclass(eq=False)
class Device:
    name: str
    dtype: str                      # 'M' / 'R' / 'C' / 'L' / 'X' / '?' / 其他首字母
    nodes: list = field(default_factory=list)
    model: str = ""                 # MOS: 模型名（NM/PM/nmos/nch...）；X: 子电路名
    params: dict = field(default_factory=dict)   # 小写键 -> 原始值文本
    subckt: str = ""                # 所属 .subckt（顶层为 ""）
    line_index: int = -1            # 在 raw_lines 中的首行下标
    cont_lines: list = field(default_factory=list)  # 该逻辑行占用的物理行下标
    value: str = ""                 # R/C/L 的裸值原文（如 '5k' / '2p'）
    value_si: float = None
    value_idx: int = -1             # value 在 raw_tokens 中的下标
    param_idx: dict = field(default_factory=dict)  # 参数名 -> raw_tokens 下标
    raw_tokens: list = field(default_factory=list)
    origin: str = "netlist"         # netlist | expand

    # ---- MOS 极性 ----------------------------------------------------------
    @property
    def polarity(self):
        return _mos_polarity(self)

    @property
    def is_nmos(self):
        return self.dtype == "M" and self.polarity == "n"

    @property
    def is_pmos(self):
        return self.dtype == "M" and self.polarity == "p"

    # ---- 参数取值 ----------------------------------------------------------
    def param_si(self, key, default=None):
        """参数按 SPICE 后缀换算成 SI 数值；非数值（表达式）返回 default。"""
        if key not in self.params:
            return default
        si = _scale(self.params[key])
        return default if si is None else si

    def param_int(self, key, default=None):
        si = self.param_si(key)
        if si is None:
            return default
        return int(round(si))

    def __str__(self):
        return (f"{self.name}({' '.join(self.nodes)}) {self.model} "
                f"{self.params} value={self.value}")


def _clone(dev: Device) -> Device:
    out = copy.copy(dev)
    out.nodes = list(dev.nodes)
    out.params = dict(dev.params)
    out.param_idx = dict(dev.param_idx)
    out.cont_lines = list(dev.cont_lines)
    out.raw_tokens = list(dev.raw_tokens)
    return out


class Netlist:
    """解析结果。

    scopes:      {scope名: [Device]}，scope 为 "" 表示顶层，其余为 .subckt 名
    defs:        {小写subckt名: {"ports": [...], "devices": [Device]}}
    others:      [Device]，D/Q/V/I 等本工具不做参数化的器件（仅记录）
    raw_lines:   原始行（含注释），供约减网表原样回写
    """

    def __init__(self, scopes, defs, others, raw_lines, sub_order, instantiated):
        self.raw_lines = raw_lines
        self.defs = defs
        self.others = others
        self.sub_order = sub_order
        self.instantiated = set(instantiated)
        # 展开层次实例（X）
        self.scopes = {name: _expand_devices(devs, self.defs, name)
                       for name, devs in scopes.items()}
        self._roots = self._resolve_roots()
        self.devices = [d for _, ds in self._roots for d in ds]

    # ---- 作用域 ------------------------------------------------------------
    def _resolve_roots(self):
        """确定用于识别/约减的顶层作用域。

        顶层有器件 -> 用顶层；否则取"没有被任何 X 实例化"的 subckt 作为顶层单元。
        """
        if self.scopes.get(""):
            return [("", self.scopes[""])]
        top = [s for s in self.sub_order if s not in self.instantiated]
        if not top:
            top = list(self.sub_order)
        if not top:
            return [("", [])]
        return [(s, self.scopes.get(s, [])) for s in top]

    def analysis_scopes(self):
        return list(self._roots)

    # ---- 兼容旧接口 --------------------------------------------------------
    @property
    def source_lines(self):
        return self.raw_lines

    @property
    def subckts(self):
        return {name: {"ports": d["ports"], "devices": d["devices"]}
                for name, d in self.defs.items()}

    def mos_list(self, scope=None):
        if scope is None:
            return [d for d in self.devices if d.dtype == "M"]
        return [d for d in self.scopes.get(scope, []) if d.dtype == "M"]

    def by_node(self, node, scope=None):
        devs = self.devices if scope is None else self.scopes.get(scope, [])
        return [d for d in devs if node in d.nodes]


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def _read_text(path_or_text) -> str:
    s = str(path_or_text)
    if "\n" in s:
        return s
    p = Path(s)
    if p.is_file():
        for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
            try:
                return p.read_text(encoding=enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return p.read_text(encoding="utf-8", errors="ignore")
    return s


def _logical_lines(text):
    """合并续行，返回 (raw_lines, [(首行下标, 合并文本, [占用行下标])])。

    注释行与空行不进 items（但要保留在 raw_lines 里），续行并入上一条逻辑行。
    """
    raw = text.splitlines()
    items = []
    for i, line in enumerate(raw):
        s = line.strip()
        if not s or s.startswith("*") or s.startswith("$"):
            continue
        if s.startswith("+"):
            if items:
                first, merged, idxs = items[-1]
                items[-1] = (first, merged + " " + s[1:].strip(), idxs + [i])
            continue
        items.append((i, s, [i]))
    return raw, items


def _classify(name, positional, params):
    """器件类型判定：不依赖首字母是否恰好为 M。"""
    first = name[0].upper()
    if first == "X":
        return "X"
    if first == "M":
        return "M"
    if first in _PASSIVE:
        return first
    if first in _STD_LETTERS:
        return first
    # NM1 / PM1 / mn1 / 自定义实例名 -> 看模型名
    model = positional[-1] if positional and _scale(positional[-1]) is None else ""
    if _looks_like_mos_model(model) and len(positional) >= 5:
        return "M"
    return "?"


def _parse_device(merged, scope, first_idx, idxs):
    toks = merged.split()
    if not toks:
        return None
    name = toks[0]
    params, param_idx, positional, pos_idx = {}, {}, [], []
    for i, t in enumerate(toks[1:], start=1):
        if "=" in t:
            k, v = t.split("=", 1)
            params[k.lower()] = v
            param_idx[k.lower()] = i
        else:
            positional.append(t)
            pos_idx.append(i)

    dtype = _classify(name, positional, params)
    dev = Device(name=name, dtype=dtype, params=params, param_idx=param_idx,
                 subckt=scope, line_index=first_idx, cont_lines=list(idxs),
                 raw_tokens=toks)

    if dtype == "M":
        model = positional[-1] if positional and _scale(positional[-1]) is None else ""
        dev.model = model
        dev.nodes = list(positional[:-1]) if model else list(positional)
    elif dtype == "X":
        dev.model = positional[-1] if positional else ""
        dev.nodes = list(positional[:-1]) if positional else []
    elif dtype in _PASSIVE:
        value, value_idx, nodes = "", -1, []
        for p, tok in zip(pos_idx, positional):
            if value_idx < 0 and _scale(tok) is not None:
                value, value_idx = tok, p
                continue
            nodes.append(tok)
        dev.value = value
        dev.value_si = _scale(value) if value else None
        dev.value_idx = value_idx
        dev.nodes = nodes
    else:
        dev.nodes = list(positional)
    return dev


def _resolve_subckt(dev, defs):
    """X 实例指向的 .subckt 名（大小写不敏感）。"""
    cand = (dev.model or "").lower()
    if cand and cand in defs:
        return cand
    for tok in reversed(dev.raw_tokens[1:]):
        t = str(tok).lower()
        if t in defs:
            return t
    return None


def _instantiate(sub, node_map, prefix, defs, scope, depth, max_depth):
    out = []
    for tpl in defs[sub]["devices"]:
        nd = _clone(tpl)
        nd.name = f"{prefix}.{tpl.name}"
        nodes = []
        for n in tpl.nodes:
            mapped = node_map.get(n)
            if mapped is None:
                # 端口之外的内部节点加实例前缀，避免不同实例间串接
                mapped = n if is_rail(n) else f"{prefix}.{n}"
            nodes.append(mapped)
        nd.nodes = nodes
        nd.subckt = scope
        nd.line_index = -1
        nd.cont_lines = []
        nd.origin = "expand"
        if nd.dtype == "X":
            sub2 = _resolve_subckt(nd, defs)
            if sub2 and depth < max_depth:
                nm2 = {p: n for p, n in zip(defs[sub2]["ports"], nd.nodes)}
                out.extend(_instantiate(sub2, nm2, nd.name, defs, scope,
                                        depth + 1, max_depth))
                continue
            if _looks_like_mos_model(nd.model):
                nd.dtype = "M"
        out.append(nd)
    return out


def _expand_devices(devs, defs, scope, depth=0, max_depth=8):
    out = []
    for d in devs:
        if d.dtype != "X":
            out.append(d)
            continue
        sub = _resolve_subckt(d, defs)
        if sub and depth < max_depth:
            node_map = {p: n for p, n in zip(defs[sub]["ports"], d.nodes)}
            out.extend(_instantiate(sub, node_map, d.name, defs, scope,
                                    depth + 1, max_depth))
            continue
        # 未定义的层次实例：模型名像 MOS 就按 MOS 兜底识别，否则原样保留供报告说明
        if _looks_like_mos_model(d.model):
            nd = _clone(d)
            nd.dtype = "M"
            out.append(nd)
            continue
        out.append(d)
    return out


def parse_netlist(path_or_text) -> Netlist:
    text = _read_text(path_or_text)
    raw, items = _logical_lines(text)

    scopes = {"": []}
    defs = {}
    others = []
    sub_order = []
    cur_sub = None

    for first_idx, merged, idxs in items:
        toks = merged.split()
        low = toks[0].lower()
        if low.startswith(".subckt"):
            cur_sub = toks[1].lower() if len(toks) > 1 else ""
            ports = []
            for t in toks[2:]:
                if "=" in t or t.lower().startswith("params:"):
                    break
                ports.append(t)
            defs[cur_sub] = {"ports": ports, "devices": [],
                             "line_index": first_idx, "name": toks[1]}
            scopes.setdefault(cur_sub, [])
            sub_order.append(cur_sub)
            continue
        if low.startswith(".ends"):
            cur_sub = None
            continue
        if low.startswith("."):
            continue

        dev = _parse_device(merged, cur_sub or "", first_idx, idxs)
        if dev is None:
            continue
        if dev.dtype in ("M", "R", "C", "L", "X"):
            scope = cur_sub if cur_sub else ""
            scopes.setdefault(scope, []).append(dev)
            if cur_sub:
                defs[cur_sub]["devices"].append(dev)
        else:
            others.append(dev)

    # 第二遍：X 实例可能引用文件后面才定义的 .subckt
    instantiated = set()
    for devs in scopes.values():
        for d in devs:
            if d.dtype == "X":
                sub = _resolve_subckt(d, defs)
                if sub:
                    instantiated.add(sub)

    return Netlist(scopes, defs, others, raw, sub_order, instantiated)
