"""Small, testable adapter shared by the Streamlit demonstration page.

本模块**只放纯逻辑，不 import streamlit**：Streamlit 页面负责渲染，
这里负责"能被测到的行为"。新增的第②③问入口同理——先在这里做成纯函数，
页面只是调用它们，这样没有浏览器也能跑回归测试。
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from agent1_spec_parser.main import parse_spec
from agent1_spec_parser.schema import validate_spec

ROOT = Path(__file__).resolve().parent


def parse_for_ui(
    text: str,
    *,
    use_llm: bool,
    api_key: str | None = None,
    parser: Callable[..., dict] = parse_spec,
) -> tuple[dict | None, list[str]]:
    """Parse user input and return a display-ready Spec plus validation errors."""
    if not text or not text.strip():
        return None, ["请输入一段电路性能需求。"]

    previous_key = os.environ.get("DASHSCOPE_API_KEY")
    if api_key is not None:
        os.environ["DASHSCOPE_API_KEY"] = api_key
    try:
        spec = parser(text.strip(), use_llm=use_llm)
    finally:
        if api_key is not None:
            if previous_key is None:
                os.environ.pop("DASHSCOPE_API_KEY", None)
            else:
                os.environ["DASHSCOPE_API_KEY"] = previous_key
    return spec, validate_spec(spec)


def format_api_error(error: Exception) -> str:
    """Convert API errors to a short UI message without exposing request details."""
    detail = str(error)
    if "HTTP 401" in detail or "invalid_api_key" in detail:
        return "Qwen API Key 无效或已失效，请从百炼控制台重新复制有效 Key。"
    if "HTTP 403" in detail:
        return "当前账号没有 Qwen3.8-Max 的调用权限，请检查百炼模型授权。"
    return "Qwen 调用失败，请检查网络、模型权限和账户余额后重试。"


def llm_failure_message(spec: dict) -> str | None:
    """Return a safe message when Agent 1 had to fall back after an LLM error."""
    if spec.get("source") != "rule(LLM failed)":
        return None
    return format_api_error(RuntimeError(str(spec.get("llm_error", ""))))


# ---------------------------------------------------------------------------
# 第②问：拓扑模块识别 + 参数约减
# ---------------------------------------------------------------------------
def analyze_netlist_text(text: str, source_name: str = "<pasted>") -> dict:
    """对网表文本跑第②问，返回可直接渲染的结果。

    返回 dict：
        ok       —— 是否成功
        error    —— 失败原因（ok=False 时）
        result   —— agent2 的 topology_result.json 内容
        csv_text / netlist_text —— 两个交付物的文本（供页面下载）
    """
    if not text or not text.strip():
        return {"ok": False, "error": "网表内容为空。", "result": None,
                "csv_text": "", "netlist_text": ""}
    try:
        from agent2_topology.main import build_result
        from agent2_topology.netlist_parser import parse_netlist
        from agent2_topology.param_reducer import (export_reduced_netlist,
                                                   export_variables_csv,
                                                   reduce_parameters)
        nl = parse_netlist(text)
        variables, modules = reduce_parameters(nl)
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            reduced_stats = export_reduced_netlist(nl, variables, tmp / "netlist_reduced.sp")
            export_variables_csv(variables, tmp / "variables.csv")
            csv_text = (tmp / "variables.csv").read_text(encoding="utf-8")
            netlist_text = (tmp / "netlist_reduced.sp").read_text(encoding="utf-8")
        result = build_result(nl, variables, modules, source_name, reduced_stats)
        return {"ok": True, "error": "", "result": result,
                "csv_text": csv_text, "netlist_text": netlist_text}
    except Exception as exc:                       # 页面层统一转成提示，不抛栈
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "result": None,
                "csv_text": "", "netlist_text": ""}


def variable_table(result: dict) -> list[dict]:
    """把约减结果里的变量整理成表格行（自由变量在前）。"""
    rows = []
    for v in result.get("variables", []):
        devices = v.get("devices") or []
        if isinstance(devices, str):
            devices = devices.split("|")
        rows.append({
            "变量": v.get("name", ""),
            "器件": ", ".join(devices),
            "参数": v.get("param", ""),
            "初值": v.get("value", ""),
            "下界": v.get("min", ""),
            "上界": v.get("max", ""),
            "类型": v.get("type", ""),
            "约束": v.get("constraint", ""),
            "依据": v.get("reason", ""),
        })
    rows.sort(key=lambda r: r["约束"] != "free")
    return rows


# ---------------------------------------------------------------------------
# 第③问：尺寸优化（本地 mock 仿真器）
# ---------------------------------------------------------------------------
_UM = 1e-6


def build_search_space(variables: list[dict]) -> tuple[list[str], list[float],
                                                       list[float], list[bool],
                                                       list[float]]:
    """由 variables.csv 的行构造搜索空间，返回 (names, lo, hi, is_int, x0)。

    单位口径：variables.csv 的 min/max 带 unit（如 um），要换算成 SI（米）；
    MockSimulator 内部按 SI 处理（它在内部再乘 1e6 转 µm），两边不能混。
    """
    names, lo, hi, is_int, x0 = [], [], [], [], []
    for v in variables:
        if (v.get("constraint") or "free") != "free":
            continue                                     # 联动变量不是独立变量
        kind = (v.get("type") or "float").strip().lower()
        unit = (v.get("unit") or "").strip().lower()
        scale = _UM if unit in ("um", "u") else 1.0

        def num(field, default=None):
            try:
                return float(str(v.get(field, "")).strip())
            except (TypeError, ValueError):
                return default

        lo_v, hi_v = num("min"), num("max")
        if lo_v is not None and hi_v is not None:
            lo_v, hi_v = lo_v * scale, hi_v * scale
        else:
            base = _spice_value(v.get("value")) or 1.0
            lo_v, hi_v = base / 3.0, base * 10.0
        x0_v = _spice_value(v.get("value"))
        if x0_v is None:
            x0_v = (lo_v + hi_v) / 2.0
        if kind == "int":
            lo_v, hi_v = max(1, int(round(lo_v))), max(2, int(round(hi_v)))
            x0_v = int(round(x0_v))
        if hi_v <= lo_v:
            lo_v, hi_v = x0_v * 0.2, x0_v * 5.0
        names.append(v.get("name"))
        lo.append(lo_v)
        hi.append(hi_v)
        is_int.append(kind == "int")
        x0.append(min(max(x0_v, lo_v), hi_v))
    return names, lo, hi, is_int, x0


_SUFFIX = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3,
           "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15}


def _spice_value(text):
    """'2u' -> 2e-6，'1p' -> 1e-12，'1' -> 1.0，无法解析返回 None。"""
    if text is None:
        return None
    import re
    s = str(text).strip().replace("μ", "u").replace("µ", "u")
    m = re.match(r"^([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*([a-zA-Z]*)$", s)
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    suf = m.group(2).lower()
    return val * (_SUFFIX.get("meg", 1e6) if suf == "meg" else _SUFFIX.get(suf, 1.0))


def run_optimization_demo(variables: list[dict], generations: int = 20,
                          pop_size: int = 12, budget_sec: float = 60.0,
                          seed: int = 0, noise: float = 0.0) -> dict:
    """用 MockSimulator 跑一遍第③问，返回渲染所需的数据。

    说明：仿真器是**测试替身**，数值只用于验证工程链路（约束处理、预算、
    Pareto、评分口径），不能当设计依据。真实结果必须走服务器 PyAether。
    """
    from q3_optimizer.optimize import (Constraints, ParamSpace,
                                       differential_evolution, plan_budget)
    from q3_optimizer.scoring import refs_from_results, total_score
    from q3_optimizer.simulator import MockSimulator

    names, lo, hi, is_int, x0 = build_search_space(variables)
    if not names:
        return {"ok": False, "error": "没有可优化的自由变量（variables.csv 里 free 项为空）。"}

    space = ParamSpace(names, lo, hi, is_int)
    sim = MockSimulator(seed=seed, noise=noise)
    cons = Constraints()

    def evaluate(vec):
        return sim(list(vec), keys=names)

    # 先跑一次测耗时，再由预算倒推可用评估次数（与服务器上的做法一致）
    t0 = time.time()
    sim(x0, keys=names)
    per_eval = max(time.time() - t0, 1e-6)
    budget = plan_budget(max(budget_sec, per_eval * 8), per_eval,
                         reserve=0.15, pop_size=pop_size)
    # 关键：DE 的终止条件是"评估次数用尽"，而 mock 仿真几乎零耗时，
    # 按时间倒推会得到上千万次预算，页面会一直空转到墙钟超时。
    # 因此额外用「代数 × 种群」封顶：真实仿真时由 plan_budget 的时间预算封顶，
    # mock 演示时由代数封顶，两者取小。
    budget["max_evaluations"] = int(min(
        budget["max_evaluations"], max(int(pop_size) * int(generations), int(pop_size))))
    budget["pop_size"] = min(int(pop_size), budget["pop_size"])

    history = []
    t_start = time.time()
    out = differential_evolution(
        evaluate, space, space.clip(list(x0)), budget, cons=cons, seed=seed,
        wall_clock_budget=budget_sec,
        logger=lambda e: history.append(e),
    )
    elapsed = time.time() - t_start

    best = out["best_result"] or {}
    pareto = [p["result"] for p in out.get("pareto", [])]
    refs = refs_from_results([best] + pareto) or {
        "dc_gain_db": best.get("dc_gain_db"),
        "ugb_hz": best.get("ugb_hz"),
        "area_um2": best.get("area_um2"),
    }
    sb = total_score(best, refs, elapsed_s=elapsed, fastest_s=elapsed)

    curve = []
    for h in out.get("history", []):
        b = h.get("best") or {}
        curve.append({
            "gen": h.get("gen", 0),
            "evals": h.get("n_evals", 0),
            "dc_gain_db": b.get("dc_gain_db"),
            "ugb_mhz": (b.get("ugb_hz") or 0) / 1e6,
            "area_um2": b.get("area_um2"),
            "violation": h.get("best_vio"),
        })
    return {
        "ok": True,
        "error": "",
        "names": names,
        "best_x": out["best_x"],
        "best": best,
        "violation": out.get("best_violation"),
        "feasible": cons.feasible(_dict_result(best)) if best else False,
        "pareto": pareto,
        "curve": curve,
        "n_evals": out.get("n_evals", 0),
        "generations": out.get("generations", 0),
        "elapsed_sec": round(elapsed, 2),
        "per_eval_sec": round(per_eval, 4),
        "budget": budget,
        "score": sb.as_dict(),
        "sim_calls": sim.n_evals,
    }


class _dict_result:
    """把 dict 还原成 Constraints 能消费的轻量对象。"""

    def __init__(self, d):
        self.__dict__.update(d or {})
        self.ok = bool((d or {}).get("ok", True))


# ---------------------------------------------------------------------------
# 环境自检
# ---------------------------------------------------------------------------
def environment_report(key_present: bool = False) -> list[dict]:
    """列出页面依赖的运行条件，帮队友一眼定位"为什么起不来"。"""
    import sys
    checks = []

    def add(name, ok, hint):
        checks.append({"项目": name, "状态": "OK" if ok else "缺失",
                       "说明": "" if ok else hint})

    add("Python 版本", sys.version_info >= (3, 9),
        f"当前 {sys.version.split()[0]}，建议 3.9+")
    try:
        import streamlit as _st                      # noqa: F401
        add("streamlit", True, "")
    except ImportError:
        add("streamlit", False, "pip install -r requirements.txt")
    add("DASHSCOPE_API_KEY", key_present,
        "未设置环境变量；也可在页面里临时填写（不落盘）")
    for rel in ("optimization.py", "demo.py", "tests/sample_netlist.sp",
                "docs/server_onboarding_plan.md"):
        add(f"文件 {rel}", (ROOT / rel).exists(), f"仓库中缺少 {rel}")
    return checks
