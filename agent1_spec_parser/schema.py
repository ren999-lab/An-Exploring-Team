"""Spec JSON Schema 定义、指标词典与校验。

评分要求（10 分）：JSON 必须包含 硬约束项、优化目标、指标单位、目标优先级，
每缺一项扣 1 分。本模块定义唯一的输出结构，并提供三层保障：

1. **指标词典**：中英文别名 -> 规范指标名（含单位），供规则解析与 LLM 结果规范化共用。
   LLM 常返回 `"key":"相位裕度","unit":"度"` 这类自由表述，必须映射成 `PM` / `deg`，
   否则四要素里的"指标单位"名不副实。
2. **规范化**：统一 op 写法（`≥`->`>=`）、单位、方向、优先级。
3. **完整性补齐**：硬约束/优化目标/单位/优先级四要素永不为空；缺失项用赛题
   第 6.3 节明写的强制约束补上，并逐条记录在 `assumptions` 里（可审计、可关闭）。

赛题强制约束（PDF 6.3 分值表）：PM>=50deg、GM<=-10dB、I_OPA<=3mA（所有 PVT）、
DCGain>=40dB；优化目标：DCGain 越大越好、UGB 越大越好、Area 越小越好。
"""

SCHEMA_VERSION = "1.1"

# (规范名, 单位, [别名...])。别名匹配时按长度降序，保证"单位增益带宽"先于"增益"命中。
METRICS = [
    ("UGB", "MHz", ["单位增益带宽", "增益带宽积", "unity gain bandwidth",
                    "gain bandwidth product", "带宽", "ugb", "gbw",
                    "bandwidth", "bw"]),
    ("DCGain", "dB", ["直流开环增益", "直流增益", "开环增益", "低频增益",
                      "dc gain", "open loop gain", "open-loop gain",
                      "增益", "gain"]),
    ("PM", "deg", ["相位裕度", "相位余量", "相位余度", "phase margin", "pm"]),
    ("GM", "dB", ["增益裕度", "增益余量", "gain margin", "gm"]),
    ("I_OPA", "mA", ["工作电流", "静态电流", "电源电流", "总电流", "功耗电流",
                     "supply current", "quiescent current", "i_opa",
                     "电流", "current"]),
    ("Area", "um^2", ["芯片面积", "版图面积", "面积", "area"]),
    ("SlewRate", "V/us", ["压摆率", "转换速率", "slew rate", "sr"]),
    ("SettlingTime", "ns", ["建立时间", "settling time"]),
    ("Noise", "nV/sqrt(Hz)", ["输入等效噪声", "等效输入噪声", "输入参考噪声",
                              "噪声", "noise"]),
    ("PSRR", "dB", ["电源抑制比", "psrr"]),
    ("CMRR", "dB", ["共模抑制比", "cmrr"]),
    ("OutputSwing", "V", ["输出摆幅", "输出动态范围", "output swing"]),
    ("Power", "mW", ["功耗", "power"]),
    ("Offset", "mV", ["失调电压", "输入失调", "offset"]),
    ("CMFB", "bool", ["共模反馈", "cmfb", "common mode feedback"]),
]

UNITS = {key: unit for key, unit, _ in METRICS}

# 优化方向默认值（赛题 6.3：DCGain/UGB 越大越好，Area 越小越好）
OPTIMIZATION_DIRECTION = {
    "DCGain": "max",
    "UGB": "max",
    "Area": "min",
    "PSRR": "max",
    "CMRR": "max",
    "OutputSwing": "max",
}

# 赛题评分权重 -> 默认优先级（1 最高）。UGB/Area 各 15 分，DCGain 10 分。
DEFAULT_PRIORITY = {"UGB": 1, "Area": 2, "DCGain": 3, "PSRR": 4, "CMRR": 4}

# 赛题第 6.3 节强制约束（缺失时用于补齐，标记 assumed=True）
COMPETITION_HARD_DEFAULTS = [
    {"key": "PM", "op": ">=", "value": 50, "unit": "deg", "cond": "所有PVT",
     "assumed": True, "note": "赛题6.3：相位裕度PM>=50度，所有PVT"},
    {"key": "GM", "op": "<=", "value": -10, "unit": "dB", "cond": "所有PVT",
     "assumed": True, "note": "赛题6.3：增益裕度GM<=-10dB，所有PVT"},
    {"key": "I_OPA", "op": "<=", "value": 3, "unit": "mA", "cond": "所有PVT",
     "assumed": True, "note": "赛题6.3：工作电流I_OPA<=3mA，所有PVT"},
    {"key": "DCGain", "op": ">=", "value": 40, "unit": "dB",
     "assumed": True, "note": "赛题6.3：DC增益不得低于40dB"},
]

COMPETITION_OBJECTIVES = [("DCGain", "max"), ("UGB", "max"), ("Area", "min")]

OP_ALIASES = {
    "≥": ">=", "⩾": ">=", ">=": ">=", "不小于": ">=", "不低于": ">=", "至少": ">=",
    "≤": "<=", "⩽": "<=", "<=": "<=", "不大于": "<=", "不超过": "<=", "至多": "<=",
    ">": ">", "<": "<", "=": "=", "＝": "=", "==": "=",
}

# 别名 -> 规范名（供 LLM 结果规范化使用）
KEY_ALIASES = {}
for _key, _unit, _aliases in METRICS:
    KEY_ALIASES[_key.lower()] = _key
    for _a in _aliases:
        KEY_ALIASES[_a.lower()] = _key


# 旧接口保留
HARD_CONSTRAINT_KEYS = set(UNITS)


def canon_key(name) -> str:
    """任意指标写法 -> 规范指标名；无法识别时原样返回。"""
    if name is None:
        return ""
    s = str(name).strip()
    return KEY_ALIASES.get(s.lower(), s)


def canon_op(op) -> str:
    if op is None:
        return ">="
    return OP_ALIASES.get(str(op).strip(), str(op).strip())


def empty_spec() -> dict:
    """标准 Spec 模板 —— 四大必备板块齐全。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "rule",            # rule | llm | llm+rule
        "hard_constraints": [],      # [{key, op, value, unit, cond?, assumed?}]
        "optimization_targets": [],  # [{key, direction, unit, priority}]
        "units": {},                 # 指标单位表
        "priorities": {},            # 目标优先级: 1 最高
        "assumptions": [],           # 补齐/推断的说明，便于报告审计
        "unparsed_requirements": [], # 识别到指标但没解析出数值/方向的要求，不丢弃
        "cross_check": {},           # LLM 与规则解析的交叉校验结果（报告素材）
        "raw_text": "",
    }


def normalize_spec(spec: dict, raw_text: str = "") -> dict:
    """规范化：键名/单位/方向/优先级全部对齐到本模块的词典。"""
    out = empty_spec()
    out["raw_text"] = raw_text or spec.get("raw_text", "")
    out["source"] = spec.get("source", "rule")
    for extra in ("assumptions", "unparsed_requirements", "cross_check"):
        if spec.get(extra):
            out[extra] = spec[extra]

    constraints = []
    for c in spec.get("hard_constraints") or []:
        if not isinstance(c, dict):
            continue
        key = canon_key(c.get("key"))
        if not key:
            continue
        item = dict(c)
        item["key"] = key
        item["op"] = canon_op(c.get("op"))
        item["unit"] = UNITS.get(key, c.get("unit", "") or "")
        constraints.append(item)
    out["hard_constraints"] = constraints

    targets = []
    for t in spec.get("optimization_targets") or []:
        if not isinstance(t, dict):
            continue
        key = canon_key(t.get("key"))
        if not key:
            continue
        item = dict(t)
        item["key"] = key
        item["direction"] = t.get("direction") or OPTIMIZATION_DIRECTION.get(key, "max")
        item["unit"] = UNITS.get(key, t.get("unit", "") or "")
        targets.append(item)
    for i, t in enumerate(targets, start=1):
        t.setdefault("priority", DEFAULT_PRIORITY.get(t["key"], i))
    # 按优先级排序后再重排为 1..n（保持"优先级与列表顺序一致，1 在最前"）。
    # 次键用赛题评分权重，避免后追加的目标与前一个同号时被稳定排序挤到后面。
    targets.sort(key=lambda t: (t.get("priority", 99),
                                DEFAULT_PRIORITY.get(t["key"], 99)))
    for i, t in enumerate(targets, start=1):
        t["priority"] = i
    out["optimization_targets"] = targets

    keys = [c["key"] for c in constraints] + [t["key"] for t in targets]
    out["units"] = spec.get("units") or {k: UNITS[k] for k in keys if k in UNITS}
    out["priorities"] = spec.get("priorities") or {t["key"]: t["priority"] for t in targets}
    return out


def ensure_complete(spec: dict, apply_defaults: bool = True) -> dict:
    """保证四要素非空；缺失项用赛题强制约束补齐，并记录 assumptions。"""
    out = normalize_spec(spec, spec.get("raw_text", ""))
    notes = list(out.get("assumptions") or [])

    present = {c["key"] for c in out["hard_constraints"]}
    # CMFB 这类"存在即约束"的项不影响数值指标补齐
    if apply_defaults:
        for d in COMPETITION_HARD_DEFAULTS:
            if d["key"] in present:
                continue
            out["hard_constraints"].append(dict(d))
            present.add(d["key"])
            notes.append(f"补入赛题强制约束 {d['key']} {d['op']} {d['value']} "
                         f"{d['unit']}（原文未明确给出）")

    target_keys = {t["key"] for t in out["optimization_targets"]}
    if apply_defaults and not out["optimization_targets"]:
        for key, direction in COMPETITION_OBJECTIVES:
            out["optimization_targets"].append(
                {"key": key, "direction": direction, "unit": UNITS[key],
                 "assumed": True})
            notes.append(f"补入赛题优化目标 {key} {direction}（原文未给出优化目标）")
    elif apply_defaults:
        # 原文给了目标但漏了赛题明写的"越大越好/越小越好"项时，逐项补齐
        for key, direction in COMPETITION_OBJECTIVES:
            if key in target_keys:
                continue
            out["optimization_targets"].append(
                {"key": key, "direction": direction, "unit": UNITS[key],
                 "assumed": True})
            notes.append(f"补入赛题优化目标 {key} {direction}（原文未作为优化目标）")

    out = normalize_spec(out, out["raw_text"])
    # normalize 会重置 priorities，这里重新按最终目标表写入
    out["priorities"] = {t["key"]: t["priority"] for t in out["optimization_targets"]}
    out["assumptions"] = notes
    return out


def validate_spec(spec: dict) -> list:
    """校验 Spec 完整性，返回缺失项列表（空列表 = 通过）。"""
    problems = []
    if not spec.get("hard_constraints"):
        problems.append("缺少硬约束项 hard_constraints")
    if not spec.get("optimization_targets"):
        problems.append("缺少优化目标 optimization_targets")
    if not spec.get("units"):
        problems.append("缺少指标单位 units")
    if not spec.get("priorities"):
        problems.append("缺少目标优先级 priorities")
    for t in spec.get("optimization_targets", []):
        if t.get("priority") in (None, ""):
            problems.append(f"优化目标 {t.get('key')} 缺少 priority")
        if not t.get("unit"):
            problems.append(f"优化目标 {t.get('key')} 缺少 unit")
    for c in spec.get("hard_constraints", []):
        if not c.get("unit"):
            problems.append(f"硬约束 {c.get('key')} 缺少 unit")
    return problems
