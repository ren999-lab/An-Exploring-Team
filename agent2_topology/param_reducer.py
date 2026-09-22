"""参数约减：按拓扑模块联动约束，剔除冗余优化变量。

约减策略（报告需说明"为何约减掉某参数 / 为何保留某变量"）：

1. **Dummy 器件**：直接剔除，不设优化变量（不参与信号路径）。
2. **电流镜**：参考臂保留 W/L/m 为自由变量；从臂按**初始比例**随参考臂缩放
   （W 与 m 分别保比例，L 强制跟随），既约减了变量，又**不改变原始设计点**。
3. **差分对 / 匹配器件**：各管强制同尺寸，合并为一份自由变量（版图匹配要求）。
4. **电阻/电容/电感**：按 PDK 参数化——有 W/L/Seg 就取这些，只有裸值时才把
   值本身作为变量；绝不凭空发明网表里不存在的参数。

相对原实现的修正：
* 镜像比原先只按 `m` 计算，`M4 w=4u / M5 w=12u` 这种"用 W 表达比例"的设计会被
  静默改成 1:1（等于改了电路）。现在 W 与 m 分别保比例。
* 原先比例 ≠1 时会拼出 `{M4_w*3_w}` 这种非法变量名；现在按结构化字段生成 `{M4_w*3}`。
* 原先 `m`、`seg` 根本不是变量，而 PDK 面积公式是 `f*w*L*m*1.5`，等于丢掉了最省
  面积的旋钮；现在 MOS 一律含 m，R 含 seg。
* 原先对电容只给一个 `c` 变量，但网表里是 `w=/l=`，导致 `variables.csv` 与约减网表对不上。
* 约减网表现在带 `.param` 块与联动关系注释，可直接交付/仿真。
"""

import csv
from pathlib import Path

from .netlist_parser import PDK_LIMITS, PDK_LIMITS_TEXT, PARAM_UNITS
from .topology_recognizer import recognize

_VALUE_UNITS = {"R": "ohm", "C": "F", "L": "H"}


# ---------------------------------------------------------------------------
# 变量模型
# ---------------------------------------------------------------------------
def default_params(dev):
    """器件在本工具变量模型下的参数集合（PDK 器件参数）。"""
    if dev.dtype == "M":
        return [p for p in ("w", "l", "m") if p in dev.params or p == "m"]
    if dev.dtype in ("R", "C"):
        params = [p for p in ("w", "l", "seg") if p in dev.params]
        if dev.dtype == "C":
            params = [p for p in ("w", "l", "m") if p in dev.params]
        if params:
            return params
        return ["value"] if dev.value else []
    if dev.dtype == "L":
        return ["value"] if dev.value else []
    return []


def count_raw_variables(netlist):
    """约减前的变量个数（= 各器件 PDK 参数的朴素笛卡尔积）。"""
    return sum(len(default_params(d)) for d in netlist.devices)


def _unit_for(dtype, param):
    if param == "value":
        return _VALUE_UNITS.get(dtype, "")
    if param in ("w", "l"):
        return "um"
    return PARAM_UNITS.get(param, "")


def _limit_for(dtype, param):
    """(min, max, type)。无 PDK 明确区间时返回 (None, None, 'float')。"""
    if param == "value":
        return None, None, "float"
    limits = PDK_LIMITS.get(dtype, {})
    if param in limits:
        lo, hi, _unit, typ = limits[param]
        return lo, hi, typ
    return None, None, "float"


def _qualifier(scope, multi):
    if multi and scope:
        return lambda n: f"{scope}.{n}"
    return lambda n: n


def _ratio_for(dev, ref, param):
    """从臂/参考臂的初始比例；无法求值时退化为 1（仅表示同尺寸）。"""
    if param in ("m", "seg"):
        a = dev.param_int(param, 1) or 1
        b = ref.param_int(param, 1) or 1
        return (a / b) if b else 1.0
    a = dev.param_si(param)
    b = ref.param_si(param)
    if a and b:
        return a / b
    return 1.0


def _mk_var(dev, param, constraint, ref, ratio, reason, scope, q):
    dname = q(dev.name)
    if param == "value":
        raw = dev.value
        si = dev.value_si
    else:
        raw = dev.params.get(param, "")
        si = dev.param_si(param)
    if param == "m" and raw in ("", None):
        raw, si = "1", 1.0
    lo, hi, typ = _limit_for(dev.dtype, param)
    name = f"{dname}_{param}"
    if constraint == "free":
        cons, ref_var = "free", None
    else:
        ref_var = f"{q(ref.name)}_{param}"
        cons = f"={ref_var}"
        if ratio is not None and abs(ratio - 1.0) > 1e-12:
            cons += f"*{ratio:g}"
    return {
        "name": name,
        "device": dname,
        "devices": [dname],
        "param": param,
        "constraint": cons,
        "ref_var": ref_var,
        "ratio": None if constraint == "free" else ratio,
        "value": raw,
        "value_si": si,
        "unit": _unit_for(dev.dtype, param),
        "min": lo,
        "max": hi,
        "type": typ,
        "derived_max": None,
        "reason": reason,
        "scope": scope,
        "dtype": dev.dtype,
    }


def _apply_linked_int_bounds(variables):
    """从臂 m/seg 若按 >1 的比例放大，参考变量的可用上界要相应收紧。

    例：M5_m = M4_m*3 且 PDK 规定 m<=20，则 M4_m 实际只能取到 floor(20/3)=6。
    """
    by_name = {v["name"]: v for v in variables}
    for v in variables:
        if v["constraint"] == "free" or v["param"] not in ("m", "seg"):
            continue
        ratio = v.get("ratio") or 1.0
        if ratio <= 1.0:
            continue
        ref = by_name.get(v.get("ref_var"))
        if not ref or ref.get("max") is None:
            continue
        cap = int(ref["max"] // ratio)
        cur = ref.get("derived_max")
        ref["derived_max"] = cap if cur is None else min(cur, cap)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def reduce_parameters(netlist):
    """返回 (variables, modules)。

    variables: [{name, device, devices, param, constraint, ref_var, ratio,
                 value, value_si, unit, min, max, type, derived_max,
                 reason, scope, dtype}]
    """
    modules = recognize(netlist)
    roots = netlist.analysis_scopes()
    multi = len(roots) > 1
    variables = []
    for scope, devs in roots:
        q = _qualifier(scope, multi)
        scope_mods = [m for m in modules if m.get("scope") == scope]
        variables.extend(_reduce_scope(scope, devs, scope_mods, q))
    _apply_linked_int_bounds(variables)
    return variables, modules


# 依赖型模块：会在器件之间建立"参考臂/首器件 -> 联动变量"的关系。
# 由于识别是多角色标注，同一批器件可能同时出现在"共源共栅/输出级"等模块里，
# 必须**先**由这些模块认领，否则匹配对（如输出级 M5/M6）会各自变成独立变量，
# 白白丢掉一半的约减收益。
MERGE_MODULES = ("电流镜", "输入对管（差分对）", "匹配器件")


def _reduce_scope(scope, devs, modules, q):
    """单作用域约减。

    注意：本函数内部一律用**器件原名**做查表/去重，只有生成变量名时才用 `q()`
    加作用域前缀。原因是 `recognize()` 返回的模块里器件名是不带前缀的，
    若这里用带前缀的名字查表，多作用域网表会全部查不到，导致约减率为 0。
    """
    variables = []
    by_name = {d.name: d for d in devs}
    covered = set()

    def free(dev, reason):
        for p in default_params(dev):
            variables.append(_mk_var(dev, p, "free", None, None, reason, scope, q))
        covered.add(dev.name)

    def dependent(dev, ref, reason, force_equal=False):
        for p in default_params(dev):
            ratio = 1.0 if force_equal else _ratio_for(dev, ref, p)
            variables.append(_mk_var(dev, p, "linked", ref, ratio, reason, scope, q))
        covered.add(dev.name)

    def handle(mod):
        mtype = mod["module_type"]
        group = [by_name[n] for n in mod["devices"] if n in by_name]
        group = [d for d in group if d.name not in covered]
        if not group:
            return

        if mtype == "Dummy器件":
            covered.update(d.name for d in group)     # Dummy 不设优化变量
            return

        if mtype == "电流镜":
            ref = group[0]
            free(ref, "电流镜参考臂，自由变量")
            for s in group[1:]:
                dependent(s, ref, "电流镜镜像联动，按初始比例随参考臂缩放（非独立变量）")
            return

        if mtype in ("输入对管（差分对）", "匹配器件"):
            head = group[0]
            free(head, f"{mtype}各管强制同尺寸，合并为单变量")
            for r in group[1:]:
                dependent(r, head, "匹配联动，强制与首器件同尺寸（非独立变量）",
                          force_equal=True)
            return

        for d in group:
            free(d, f"{mtype}成员，保留为独立变量")

    # 第一遍：依赖型模块（建立自由变量 + 联动约束）
    for mod in modules:
        if mod["module_type"] in MERGE_MODULES:
            handle(mod)
    # 第二遍：其余模块（含共源共栅/有源负载/输出级/无源网络/其他）
    for mod in modules:
        if mod["module_type"] not in MERGE_MODULES:
            handle(mod)

    # 未被任何模块认领的器件，保守保留
    for d in devs:
        if d.name in covered:
            continue
        if d.dtype == "M":
            free(d, "未识别模块成员，保守保留为独立变量")
        elif d.dtype in ("R", "C", "L"):
            free(d, f"{d.dtype} 器件参数（按 PDK W/L/Seg/值 参数化）")
    return variables


# ---------------------------------------------------------------------------
# 交付物 1：变量统计列表 CSV
# ---------------------------------------------------------------------------
CSV_FIELDS = ["variable", "value", "min", "max", "unit", "type",
              "devices", "param", "constraint", "reason"]


def _fmt(v):
    if v is None or v == "":
        return ""
    return v


def export_variables_csv(variables, path):
    """输出 CSV：`variable,value,...`，value 即初值（对应题面 `M1_w=36u` 形式）。"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_FIELDS)
        for v in variables:
            row = []
            for k in CSV_FIELDS:
                if k == "variable":
                    row.append(v["name"])
                elif k == "devices":
                    row.append("|".join(v["devices"]))
                else:
                    row.append(_fmt(v.get(k)))
            w.writerow(row)


# ---------------------------------------------------------------------------
# 交付物 2：约减后（变量化）网表
# ---------------------------------------------------------------------------
def _insert_at(raw_lines):
    """`.param` 块插入位置：优先放在第一个 .subckt 之前（全局作用域）。"""
    for i, line in enumerate(raw_lines):
        if line.strip().lower().startswith(".subckt"):
            return i
    for i, line in enumerate(raw_lines):
        s = line.strip()
        if s and not s.startswith(("*", "$", ".")):
            return i
    return len(raw_lines)


def _param_line(var):
    """`free` -> 直接给初值；联动变量 -> 用表达式引用参考变量。

    HSPICE 里含运算符的表达式需要加引号，故 `*ratio` 形式包一层单引号。
    这样每个器件参数都有独立变量名（与 CSV、与 PyAether `NM1_m` 命名一致），
    同时联动关系由 .param 表达式保证，约减网表可自洽仿真。
    """
    if var["constraint"] == "free":
        return f".param {var['name']}={var['value'] or '0'}"
    expr = var["constraint"][1:]          # 去掉前导 '='
    if "*" in expr:
        expr = f"'{expr}'"
    return f".param {var['name']}={expr}"


def export_reduced_netlist(netlist, variables, path):
    """在原始网表基础上做**逐 token 变量的替换**，并补上 `.param` 声明块。

    逐 token 替换（而不是按器件结构重建行）可以完整保留 `.include`、注释、
    续行以及 PDK 特有的额外参数（如 nf=/ad=/as=）。
    """
    # (首行下标) -> (作用域, 限定名, Device)
    line_map = {}
    multi = len(netlist.analysis_scopes()) > 1
    for scope, devs in netlist.analysis_scopes():
        q = _qualifier(scope, multi)
        for d in devs:
            if d.line_index >= 0:
                line_map[d.line_index] = (scope, q(d.name), d)

    var_map = {(v["scope"], v["device"], v["param"]): v for v in variables}
    free_vars = [v for v in variables if v["constraint"] == "free"]
    linked_vars = [v for v in variables if v["constraint"] != "free"]

    patched, unpatched = 0, []
    out = []
    consumed_cont = set()
    for idx, line in enumerate(netlist.raw_lines):
        if idx in consumed_cont:
            continue
        info = line_map.get(idx)
        if not info:
            out.append(line)
            continue
        scope, dname, dev = info
        toks = list(dev.raw_tokens)
        changed = False
        for param, tok_idx in dev.param_idx.items():
            v = var_map.get((scope, dname, param))
            if v and 0 <= tok_idx < len(toks):
                toks[tok_idx] = f"{param}={{{v['name']}}}"
                changed = True
        if dev.value_idx >= 0:
            v = var_map.get((scope, dname, "value"))
            if v and 0 <= dev.value_idx < len(toks):
                toks[dev.value_idx] = "{" + v["name"] + "}"
                changed = True
        if changed:
            out.append(" ".join(toks))
            consumed_cont.update(i for i in dev.cont_lines if i != idx)
            patched += 1
        else:
            out.append(line)

    # 层次展开出来的器件没有原始行可改，单独记录下来
    for v in variables:
        if v["scope"] and not any(k[0] == v["scope"] and k[1] == v["device"]
                                  for k in line_map.values()):
            unpatched.append(v["name"])

    header = _build_header(netlist, free_vars, linked_vars, unpatched)
    at = _insert_at(netlist.raw_lines)
    # 插入位置按"输出行"换算：被跳过的续行会让下标偏移，这里以原始行文本定位
    insert_out_idx = len(out)
    for i in range(min(at, len(netlist.raw_lines))):
        if i in consumed_cont:
            continue
    # 直接扫描 out，找到与 raw_lines[at] 相同文本的第一处
    if at < len(netlist.raw_lines):
        target = netlist.raw_lines[at]
        for i, line in enumerate(out):
            if line == target:
                insert_out_idx = i
                break
    final = out[:insert_out_idx] + header.splitlines() + out[insert_out_idx:]

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(final) + "\n", encoding="utf-8")
    return {"patched_lines": patched, "unpatched_variables": unpatched,
            "free": len(free_vars), "linked": len(linked_vars),
            "total": len(variables)}


def _build_header(netlist, free_vars, linked_vars, unpatched):
    lines = [
        "* " + "=" * 70,
        "* 本文件由 agent2_topology 自动生成：第②问 参数约减结果（变量化网表）",
        f"* 变量总数 {len(free_vars) + len(linked_vars)} = "
        f"自由变量 {len(free_vars)} + 联动变量 {len(linked_vars)}",
        "* PDK 取值约束：" + "；".join(
            f"{k}: {v}" for k, v in PDK_LIMITS_TEXT.items() if k in ("M", "R", "C")),
        "* 变量命名与第③问参数文件一致：<器件名>_<参数>（如 M7_w / M7_m / R1_seg）",
        "* 所有变量都在下方 .param 块中声明；联动变量以表达式引用参考变量，",
        "* 因此本网表可直接自洽仿真（变量名与 variables.csv 一一对应）。",
    ]
    if linked_vars:
        lines.append("* 联动关系（非独立变量，随参考变量按初始比例缩放）：")
        for v in linked_vars:
            lines.append(f"*   {v['name']} = {v['constraint'][1:]}    "
                         f"({v['reason']})")
    lines.append("* 自由变量（可交给第③问优化器）：")
    for v in free_vars:
        rng = ""
        if v.get("min") is not None:
            rng = f"  取值 {v['min']}~{v['max']}{v['unit']}"
        if v.get("derived_max") is not None:
            rng += f"  联动上界 {v['derived_max']}"
        # 裸数值才补单位；'4u'/'5k' 这类已带 SPICE 后缀的不重复标注
        disp = v["value"] or ""
        if disp and disp[-1].isdigit() and v["unit"]:
            disp += v["unit"]
        lines.append(f"*   {v['name']} = {disp}{rng}    ({v['reason']})")
    if unpatched:
        lines.append("* 注意：以下变量来自层次实例展开，无法在原始行内就地变量化：")
        lines.append("*   " + ", ".join(sorted(set(unpatched))))
    lines.append("* " + "=" * 70)
    lines.extend(_param_line(v) for v in free_vars)
    lines.extend(_param_line(v) for v in linked_vars)
    return "\n".join(lines)
