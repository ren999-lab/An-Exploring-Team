"""Agent1 入口：自然语言 Spec → 结构化 JSON。

用法:
    python -m agent1_spec_parser.main "设计全差分运放，PM>=60度，电流3mA..."
    python -m agent1_spec_parser.main --input spec.txt -o output/spec.json

无 API Key 时的降级路径：--fallback 触发规则解析（正则+词典），保证演示可跑通。
"""

import argparse
import json
import re
import sys
from pathlib import Path

from .prompt import SYSTEM_PROMPT, FEW_SHOT
from .schema import empty_spec, normalize_spec, validate_spec

# 规则解析兜底词典（LLM 不可用时使用）
_RULES = [
    (r"(共模反馈|CMFB)", lambda m: {"key": "CMFB", "op": ">", "value": 0, "unit": "bool"}),
    (r"(增益|gain)[^0-9]{0,8}(\d+(?:\.\d+)?)\s*(db)?", None),  # 特殊处理见 _rule_parse
    (r"(带宽|UGB)[^0-9]{0,8}(\d+(?:\.\d+)?)\s*(mhz|hz)?", None),
    (r"(相位裕度|PM)[^0-9]{0,8}(\d+(?:\.\d+)?)\s*(度|deg)?", None),
    (r"(电流|I_?OPA)[^0-9]{0,8}(\d+(?:\.\d+)?)\s*(ma|a)?", None),
]
_NUM = r"(\d+(?:\.\d+)?)"


def rule_fallback(text: str) -> dict:
    """纯规则解析（LLM 失败兜底）。识别常见指标 + 数值 + 方向词。"""
    spec = empty_spec()
    spec["raw_text"] = text
    tl = text.lower()

    def add_constraint(key, op, value, unit, cond=None):
        c = {"key": key, "op": op, "value": value, "unit": unit}
        if cond:
            c["cond"] = cond
        spec["hard_constraints"].append(c)

    if re.search(r"共模反馈|cmfb", tl):
        add_constraint("CMFB", ">", 0, "bool")
    m = re.search(r"(?:增益|gain)[^\d]{0,10}(" + _NUM + r")\s*(db|dB)", text)
    if m:
        add_constraint("DCGain", ">=", float(m.group(1)), "dB")
    m = re.search(r"(?:带宽|ugb|GBW)[^\d]{0,10}(" + _NUM + r")\s*(mhz|MHz|hz|Hz)", text)
    if m:
        v = float(m.group(1))
        add_constraint("UGB", ">=", v * 1e-3 if "hz" in m.group(2).lower() and "mhz" not in m.group(2).lower() else v, "MHz")
    m = re.search(r"(?:相位裕度|PM)[^\d]{0,10}(" + _NUM + r")\s*(?:度|deg|°)", text)
    if m:
        add_constraint("PM", ">=", float(m.group(1)), "deg")
    m = re.search(r"(?:电流|i_?opa)[^\d]{0,10}(" + _NUM + r")\s*(ma|mA)", text)
    if m:
        add_constraint("I_OPA", "<=", float(m.group(1)), "mA", cond="所有PVT" if "pvt" in tl else None)

    # 优化目标方向词
    if re.search(r"面积.{0,6}(小|min)|尽量减.{0,3}面积", tl):
        spec["optimization_targets"].append(
            {"key": "Area", "direction": "min", "unit": "um^2", "priority": 1})
    if re.search(r"(增益|gain).{0,8}(大)|增益尽可能", tl) and "不低于" not in tl:
        pass  # 已作为硬约束
    return normalize_spec(spec)


def parse_spec(text: str, use_llm: bool = True) -> dict:
    """主入口：自然语言 → Spec dict。优先 LLM，失败降级规则。"""
    if use_llm:
        try:
            from .llm_client import chat, extract_json
            resp = chat("spec_parse", SYSTEM_PROMPT + "\n" + FEW_SHOT, text)
            raw = extract_json(resp)
            spec = normalize_spec(raw, raw_text=text)
            problems = validate_spec(spec)
            if not problems:
                return spec
            print("[agent1] LLM 输出缺项，尝试规则补全:", problems)
            fb = rule_fallback(text)
            for f in ("hard_constraints", "optimization_targets", "units", "priorities"):
                if not spec.get(f):
                    spec[f] = fb[f]
            return spec
        except Exception as e:
            print(f"[agent1] LLM 调用失败（{e}），降级为规则解析")
    return rule_fallback(text)


def main():
    ap = argparse.ArgumentParser(description="自然语言 Spec 解析 Agent")
    ap.add_argument("text", nargs="*", help="自然语言性能描述")
    ap.add_argument("--input", "-i", help="从文件读取自然语言描述")
    ap.add_argument("-o", "--output", default="agent1_spec_parser/output/spec.json")
    ap.add_argument("--no-llm", action="store_true", help="跳过 LLM，纯规则解析")
    args = ap.parse_args()

    text = Path(args.input).read_text(encoding="utf-8") if args.input else " ".join(args.text)
    if not text.strip():
        text = ("请设计一个带有共模反馈的全差分运放，要求：DC增益不低于95dB，单位增益带宽至少60MHz，"
                "相位裕度大于55度，所有PVT下工作电流不超过3mA，并尽量减小面积。")
        print("[agent1] 未提供输入，使用赛题示例文本")

    spec = parse_spec(text, use_llm=not args.no_llm)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[agent1] Spec 已输出: {out}")
    problems = validate_spec(spec)
    print("[agent1] 校验:", "通过" if not problems else problems)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
