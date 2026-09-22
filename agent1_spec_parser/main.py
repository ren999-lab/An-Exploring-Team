"""Agent1 入口：自然语言 Spec -> 结构化 JSON。

用法:
    python -m agent1_spec_parser.main "设计全差分运放，PM>=60度，电流3mA..."
    python -m agent1_spec_parser.main --input spec.txt -o output/spec.json
    python -m agent1_spec_parser.main --no-llm "..."     # 纯规则（无 Key 也能跑）
    python -m agent1_spec_parser.main --strict "..."     # 不补赛题默认约束

架构：**规则解析是主干，LLM 是增强层**。
* 规则解析（本文件）确定性地覆盖中英文指标词、比较词、单位换算，
  并保证"硬约束/优化目标/单位/优先级"四要素永不为空；
* LLM（Qwen3.8-Max）负责自由表述的兜底；
* 两者结果做**交叉校验**（cross_check），差异写进 Spec 供技术报告说明。

原实现的问题（实测）：
* 只认"不低于/至少"等少数词，题面表格里的示例"增益尽可能大，单位增益带宽尽可能大，
  面积尽可能小"只解出 Area 一个目标；
* **完全没有 GM 解析**——而 GM<=-10dB 是赛题 30 分项里单独占 10 分的硬约束；
* 任何不含"面积"字样的规格都会让 optimization_targets 为空 -> 校验失败；
* 英文输入、GHz/uA 等单位一律不认。
"""

import argparse
import json
import re
import sys
from pathlib import Path

from .prompt import FEW_SHOT, SYSTEM_PROMPT
from .schema import (COMPETITION_HARD_DEFAULTS, METRICS, UNITS,
                     ensure_complete, normalize_spec, validate_spec)

# ---------------------------------------------------------------------------
# 词法：比较词 / 方向词
# ---------------------------------------------------------------------------
_OP_PATTERNS = [
    (r"不低于|不小于|不少于|至少|大于等于|>=|≥|⩾|no less than|at least|"
     r"minimum of|no smaller than", ">="),
    (r"不超过|不大于|不多于|至多|小于等于|<=|≤|⩽|no more than|at most|"
     r"maximum of|no greater than", "<="),
    (r"大于|高于|>|greater than|more than|above", ">"),
    (r"小于|低于|<|less than|below", "<"),
    (r"等于|＝|==|(?<![<>=])=(?![=])|equal to", "="),
]

_DIR_MAX = (r"尽可能大|尽量大|越大越好|越来越大|最大化|最大|尽量提高|尽可能提高|"
            r"maximi[sz]e|as large as possible|as high as possible|"
            r"larger is better|higher is better")
_DIR_MIN = (r"尽可能小|尽量小|越小越好|越来越小|最小化|最小|尽量减|尽可能减|"
            r"minimi[sz]e|as small as possible|as low as possible|"
            r"smaller is better|lower is better")

_UNIT_RE = re.compile(
    r"([-+]?\d+(?:\.\d+)?)\s*"
    r"(db|度|deg|°|mhz|ghz|khz|hz|ma|ua|µa|μa|a|mm2|um\^?2|µm²|v/us|v/µs|"
    r"ns|us|µs|ps|pf|nf|uf|µf|mv|uv|µv|mw|uw|v|%)?",
    re.IGNORECASE)

_PUNCT = "，,；;。.、！!？?（）()【】[]"

# 单位 -> 目标单位换算系数
_UNIT_SCALE = {
    "MHz": {"ghz": 1e3, "khz": 1e-3, "hz": 1e-6},
    "mA": {"ua": 1e-3, "µa": 1e-3, "μa": 1e-3, "a": 1e3},
    "um^2": {"mm2": 1e6},
    "ns": {"us": 1e3, "µs": 1e3, "ps": 1e-3},
    "mV": {"v": 1e3, "uv": 1e-3, "µv": 1e-3},
    "mW": {"w": 1e3, "uw": 1e-3},
    "nV/sqrt(Hz)": {"uv": 1e3, "µv": 1e3},
}

# 未写比较词时，各类指标的默认比较方向
_DEFAULT_OP = {"I_OPA": "<=", "Area": "<=", "Power": "<=", "SettlingTime": "<=",
               "CMRR": ">=", "PSRR": ">=", "OutputSwing": ">="}


def _clip_prefix(text, start, width=12):
    """取指标名之前的窗口，并在标点处截断（避免把上一条指标的语义词带进来）。"""
    seg = text[max(0, start - width):start]
    cut = max((seg.rfind(p) for p in _PUNCT), default=-1)
    return seg[cut + 1:]


def _clip_suffix(text, end, stop, width=32):
    """取指标名之后的窗口，在标点或下一条指标处截断。"""
    seg = text[end:min(len(text), end + width, stop)]
    cut = len(seg)
    for p in _PUNCT:
        i = seg.find(p)
        if i >= 0:
            cut = min(cut, i)
    return seg[:cut]


def find_metric_mentions(text):
    """返回按出现位置排序、互不重叠的指标提及 [(start, end, key), ...]。

    长别名优先，保证"单位增益带宽"不会被"增益"抢先匹配。
    """
    low = text.lower()
    cands = []
    for key, _unit, aliases in METRICS:
        for a in aliases:
            al = a.lower()
            start = 0
            while True:
                i = low.find(al, start)
                if i < 0:
                    break
                cands.append((i, i + len(al), key, len(al)))
                start = i + 1
    cands.sort(key=lambda c: (c[0], -c[3]))
    picked = []
    for c in cands:
        if any(not (c[1] <= p[0] or c[0] >= p[1]) for p in picked):
            continue
        picked.append(c)
    picked.sort(key=lambda c: c[0])
    return [(s, e, k) for s, e, k, _ in picked]


def _find_op(window):
    low = window.lower()
    for pat, op in _OP_PATTERNS:
        if re.search(pat, low, re.IGNORECASE):
            return op
    return None


def _find_direction(window):
    if re.search(_DIR_MAX, window, re.IGNORECASE):
        return "max"
    if re.search(_DIR_MIN, window, re.IGNORECASE):
        return "min"
    return None


def _convert(value, unit_token, target_unit):
    if not unit_token:
        return value
    scale = _UNIT_SCALE.get(target_unit, {})
    return value * scale.get(unit_token.lower(), 1.0)


def _round(v):
    if v is None:
        return None
    if abs(v - round(v)) < 1e-9:
        return int(round(v))
    return round(v, 6)


def rule_fallback(text: str, apply_competition_defaults: bool = True) -> dict:
    """纯规则解析：指标词典 + 比较词 + 方向词 + 单位换算。"""
    mentions = find_metric_mentions(text)
    constraints, targets, unparsed, notes = [], [], [], []

    for idx, (start, end, key) in enumerate(mentions):
        nxt = mentions[idx + 1][0] if idx + 1 < len(mentions) else len(text)
        prefix = _clip_prefix(text, start)
        suffix = _clip_suffix(text, end, nxt)
        window = f"{prefix} {suffix}"
        cond = "所有PVT" if re.search(r"pvt|工艺角|corner", window, re.IGNORECASE) else None

        # 共模反馈：存在即约束，无数值
        if key == "CMFB":
            constraints.append({"key": "CMFB", "op": ">", "value": 0,
                                "unit": "bool", "derived_from": "关键词提及"})
            continue

        m = _UNIT_RE.search(suffix)
        direction = _find_direction(window)

        if m:
            raw_val = float(m.group(1))
            unit_tok = m.group(2) or ""
            value = _round(_convert(raw_val, unit_tok, UNITS.get(key)))
            op = _find_op(window)
            item = {"key": key, "op": op or _DEFAULT_OP.get(key, ">="),
                    "value": value, "unit": UNITS.get(key, "")}
            if op is None:
                item["op_inferred"] = True
                notes.append(f"{key} 未写比较词，按 {item['op']} 处理")
            if cond:
                item["cond"] = cond
            constraints.append(item)
            # 赛题明确 DCGain/UGB "越大越好"：有下限时同时登记为优化目标
            if key in ("DCGain", "UGB") and op in (">=", ">", None):
                targets.append({"key": key, "direction": "max",
                                "unit": UNITS.get(key, ""),
                                "derived_from": f"{item['op']} {value}"})
        elif direction:
            targets.append({"key": key, "direction": direction,
                            "unit": UNITS.get(key, "")})
        else:
            # 规则 4：识别到指标但没数值也没方向，不丢弃
            item = {"key": key, "op": None, "value": None,
                    "unit": UNITS.get(key, "")}
            if cond:
                item["cond"] = cond
            constraints.append(item)
            unparsed.append(f"{key}：提到该指标但未给出数值或优化方向")

    spec = {
        "source": "rule",
        "raw_text": text,
        "hard_constraints": _dedupe_constraints(constraints),
        "optimization_targets": _dedupe_targets(targets),
        "assumptions": notes,
        "unparsed_requirements": unparsed,
    }
    spec = normalize_spec(spec, raw_text=text)
    spec["source"] = "rule"
    spec["assumptions"] = notes
    spec["unparsed_requirements"] = unparsed
    return ensure_complete(spec, apply_defaults=apply_competition_defaults)


def _dedupe_constraints(items):
    out, seen = [], set()
    for c in items:
        k = (c.get("key"), c.get("op"))
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def _dedupe_targets(items):
    out, seen = [], set()
    for t in items:
        if t["key"] in seen:
            continue
        seen.add(t["key"])
        out.append(t)
    return out


# ---------------------------------------------------------------------------
# LLM 路径：LLM 解析 + 规则交叉校验
# ---------------------------------------------------------------------------
def _cross_check(llm_spec, rule_spec):
    """对比 LLM 与规则的解析结果，差异写入 Spec 供报告说明。"""
    def cmap(s):
        return {c["key"]: c for c in s.get("hard_constraints", [])}

    def tmap(s):
        return {t["key"]: t for t in s.get("optimization_targets", [])}

    lc, rc = cmap(llm_spec), cmap(rule_spec)
    lt, rt = tmap(llm_spec), tmap(rule_spec)
    diff = {
        "llm_only_constraints": sorted(set(lc) - set(rc)),
        "rule_only_constraints": sorted(set(rc) - set(lc)),
        "llm_only_targets": sorted(set(lt) - set(rt)),
        "rule_only_targets": sorted(set(rt) - set(lt)),
        "value_mismatch": [],
    }
    for k in sorted(set(lc) & set(rc)):
        a, b = lc[k].get("value"), rc[k].get("value")
        if a is not None and b is not None and abs(float(a) - float(b)) > 1e-9:
            diff["value_mismatch"].append({"key": k, "llm": a, "rule": b})
    return diff


def _merge_constraints(llm_items, rule_items):
    """以 LLM 结果为准，缺失的指标用规则结果补齐。"""
    out, seen = [], set()
    for c in list(llm_items) + list(rule_items):
        if c.get("key") in seen:
            continue
        seen.add(c.get("key"))
        out.append(c)
    return out


def _merge_targets(llm_items, rule_items):
    out, seen = [], set()
    for t in list(llm_items) + list(rule_items):
        if t.get("key") in seen:
            continue
        seen.add(t.get("key"))
        out.append(t)
    return out


def parse_spec(text: str, use_llm: bool = True,
               apply_competition_defaults: bool = True,
               max_tokens: int = 2048) -> dict:
    """主入口：自然语言 -> Spec dict。LLM 优先，规则负责校验与兜底。"""
    rule_spec = rule_fallback(text, apply_competition_defaults)
    if not use_llm:
        return rule_spec

    try:
        from .llm_client import chat, extract_json
        resp = chat("spec_parse", SYSTEM_PROMPT + "\n" + FEW_SHOT, text,
                    max_tokens=max_tokens)
        raw = extract_json(resp)
        llm_spec = normalize_spec(raw, raw_text=text)
        llm_spec["source"] = "llm"
        merged = {
            "source": "llm+rule",
            "raw_text": text,
            "hard_constraints": _merge_constraints(
                llm_spec.get("hard_constraints", []),
                rule_spec.get("hard_constraints", [])),
            "optimization_targets": _merge_targets(
                llm_spec.get("optimization_targets", []),
                rule_spec.get("optimization_targets", [])),
            "assumptions": rule_spec.get("assumptions", []),
            "unparsed_requirements": rule_spec.get("unparsed_requirements", []),
            "cross_check": _cross_check(llm_spec, rule_spec),
        }
        merged = normalize_spec(merged, raw_text=text)
        merged["source"] = "llm+rule"
        merged["assumptions"] = rule_spec.get("assumptions", [])
        merged["unparsed_requirements"] = rule_spec.get("unparsed_requirements", [])
        merged["cross_check"] = _cross_check(llm_spec, rule_spec)
        merged = ensure_complete(merged, apply_defaults=apply_competition_defaults)
        merged["source"] = "llm+rule"
        merged["cross_check"] = _cross_check(llm_spec, rule_spec)
        return merged
    except Exception as e:
        print(f"[agent1] LLM 调用失败（{e}），降级为纯规则解析")
        rule_spec["source"] = "rule(LLM failed)"
        rule_spec["llm_error"] = str(e)
        return rule_spec


DEFAULT_TEXT = ("请设计一个带有共模反馈的全差分运放，要求：DC增益不低于95dB，"
                "单位增益带宽至少60MHz，相位裕度大于55度，所有PVT下工作电流不超过3mA，"
                "并尽量减小面积。")


def main(argv=None):
    ap = argparse.ArgumentParser(description="自然语言 Spec 解析 Agent")
    ap.add_argument("text", nargs="*", help="自然语言性能描述")
    ap.add_argument("--input", "-i", help="从文件读取自然语言描述")
    ap.add_argument("-o", "--output", default="agent1_spec_parser/output/spec.json")
    ap.add_argument("--no-llm", action="store_true", help="跳过 LLM，纯规则解析")
    ap.add_argument("--strict", action="store_true",
                    help="不补赛题默认约束（只输出原文明确写到的内容）")
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--quiet", action="store_true", help="只输出结果，不打印进度（供编排入口调用）")
    args = ap.parse_args(argv)

    text = Path(args.input).read_text(encoding="utf-8") if args.input \
        else " ".join(args.text)
    say = (lambda *a, **k: None) if args.quiet else print
    if not text.strip():
        text = DEFAULT_TEXT
        say("[agent1] 未提供输入，使用赛题示例文本")

    spec = parse_spec(text, use_llm=not args.no_llm,
                      apply_competition_defaults=not args.strict,
                      max_tokens=args.max_tokens)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    say(f"[agent1] Spec 已输出: {out}  (source={spec.get('source')})")

    problems = validate_spec(spec)
    say("[agent1] 四要素校验:", "通过" if not problems else problems)
    say(f"[agent1] 硬约束 {len(spec['hard_constraints'])} 项 / "
        f"优化目标 {len(spec['optimization_targets'])} 项 / "
        f"假设 {len(spec.get('assumptions', []))} 条")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
