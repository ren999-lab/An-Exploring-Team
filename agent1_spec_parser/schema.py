"""Spec JSON Schema 定义与校验。

评分要求（10 分）：JSON 必须包含 硬约束项、优化目标、指标单位、目标优先级，
每缺一项扣 1 分。此处定义唯一的输出结构并做程序化校验。
"""

# 指标合法键名（Spec 中使用的规范化字段）
HARD_CONSTRAINT_KEYS = {
    "DCGain",        # 直流增益, dB
    "UGB",           # 单位增益带宽, Hz/MHz
    "PM",            # 相位裕度, deg
    "GM",            # 增益裕度, dB
    "I_OPA",         # 工作电流, A/mA
    "Area",          # 面积, um^2
    "CMFB",          # 是否需要共模反馈, bool
}

OPTIMIZATION_DIRECTION = {
    "DCGain": "max",   # 越大越好 (不低于40dB)
    "UGB": "max",      # 越大越好
    "Area": "min",     # 越小越好
}

UNITS = {
    "DCGain": "dB",
    "UGB": "MHz",
    "PM": "deg",
    "GM": "dB",
    "I_OPA": "mA",
    "Area": "um^2",
    "CMFB": "bool",
}


def empty_spec() -> dict:
    """标准 Spec 模板 —— 四大必备板块齐全。"""
    return {
        "hard_constraints": [],   # [{key, op, value, unit}]  硬约束项
        "optimization_targets": [],  # [{key, direction, unit, priority}] 优化目标
        "units": {},              # 指标单位表
        "priorities": {},         # 目标优先级: 1 最高
        "raw_text": "",           # 原始自然语言输入（留档）
    }


def validate_spec(spec: dict) -> list:
    """校验 Spec 完整性，返回缺失项警告列表（空列表=通过）。"""
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
        if "priority" not in t:
            problems.append(f"优化目标 {t.get('key')} 缺少 priority")
    return problems


def normalize_spec(spec: dict, raw_text: str = "") -> dict:
    """补全单位、规范方向字段，确保评分四要素齐全。"""
    out = empty_spec()
    out["raw_text"] = raw_text or spec.get("raw_text", "")
    out["hard_constraints"] = spec.get("hard_constraints", [])
    for c in out["hard_constraints"]:
        c.setdefault("unit", UNITS.get(c.get("key", ""), ""))
    targets = spec.get("optimization_targets", [])
    for i, t in enumerate(targets, start=1):
        t.setdefault("direction", OPTIMIZATION_DIRECTION.get(t.get("key", ""), "max"))
        t.setdefault("unit", UNITS.get(t.get("key", ""), ""))
        t.setdefault("priority", i)
    out["optimization_targets"] = targets
    out["units"] = spec.get("units") or {k: UNITS[k] for k in
                                         HARD_CONSTRAINT_KEYS | set(OPTIMIZATION_DIRECTION)
                                         if any(x.get("key") == k for x in
                                                out["hard_constraints"] + targets)}
    out["priorities"] = spec.get("priorities") or {
        t["key"]: t["priority"] for t in targets}
    return out
