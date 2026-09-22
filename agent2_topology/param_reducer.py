"""参数约减：按拓扑模块联动约束，剔除冗余优化变量。

约减策略（报告需说明"为何约减掉某参数"）：
1. Dummy 器件    → 直接剔除，不设优化变量（不参与信号路径）
2. 电流镜        → L 与参考臂强制相等，W 按镜像比例 m 联动，
                   只保留参考臂 W/L 为自由变量
3. 匹配器件/对管 → 同尺寸合并为一份变量（版图匹配要求）
4. 电容/独立电阻 → 各自保留 W/L（或 C 值）
输出：约减后变量列表 + 约减后网表文本 + 变量 CSV
"""

import csv
from pathlib import Path

from .topology_recognizer import recognize


def reduce_parameters(netlist):
    """返回 (variables, module_map, evidence_log)

    variables: [{name, devices, param, constraint, reason}]
    """
    modules = recognize(netlist)
    mos = netlist.mos_list()
    variables = []
    covered = set()

    def add_var(devs, param, constraint, reason):
        variables.append({
            "name": "_".join(d.name for d in devs[:1]) + f"_{param}",
            "devices": [d.name for d in devs],
            "param": param,
            "constraint": constraint,
            "reason": reason,
        })

    mos_by_name = {d.name: d for d in mos}

    for mod in modules:
        mtype = mod["module_type"]
        # 保持识别结果给出的顺序（电流镜首器件=参考臂）
        devs = [mos_by_name[n] for n in mod["devices"] if n in mos_by_name]
        # 只处理尚未被覆盖的器件，避免跨模块重复计数
        devs = [d for d in devs if d.name not in covered]
        if not devs:
            continue
        if mtype == "Dummy器件":
            covered.update(d.name for d in devs)
            continue
        if mtype == "电流镜":
            ref = devs[0]
            slaves = [d for d in devs[1:] if d.name not in covered]
            for p in ("w", "l"):
                add_var([ref], p, "free", "电流镜参考臂，自由变量")
            covered.add(ref.name)
            for s in slaves:
                ref_m = float(ref.params.get("m", 1) or 1)
                s_m = float(s.params.get("m", 1) or 1)
                ratio = s_m / ref_m if ref_m else 1
                for p in ("w", "l"):
                    add_var([s], p, f"={ref.name}_{p}" + (f"*{ratio:g}" if ratio != 1 else ""),
                            "电流镜镜像联动，非独立变量")
                covered.add(s.name)
            covered.add(ref.name)
        elif mtype in ("输入对管（差分对）", "匹配器件"):
            head, rest = devs[0], devs[1:]
            for p in ("w", "l"):
                add_var([head], p, "free", f"{mtype}各管强制同尺寸，合并为单变量")
            for r in rest:
                for p in ("w", "l"):
                    add_var([r], p, f"={head.name}_{p}", "匹配联动，非独立变量")
                covered.add(r.name)
            covered.add(head.name)
        else:
            for d in devs:
                if d.name not in covered:
                    for p in ("w", "l"):
                        add_var([d], p, "free", f"{mtype}成员，保留为独立变量")
                    covered.add(d.name)

    # 未被识别覆盖的 MOS 与 R/C
    for d in netlist.devices:
        if d.name in covered:
            continue
        if d.dtype == "M":
            for p in ("w", "l"):
                add_var([d], p, "free", "未识别模块成员，保守保留为独立变量")
        elif d.dtype == "R":
            add_var([d], "w", "free", "电阻 W")
            add_var([d], "l", "free", "电阻 L (seg 另定)")
        elif d.dtype == "C":
            add_var([d], "c", "free", "电容值")
    return variables, modules


def export_variables_csv(variables, path):
    """输出 CSV：M1_w=36u,M1_L=2u 风格，供第三问优化使用。"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["variable", "devices", "param", "constraint", "reason"])
        for v in variables:
            w.writerow([f"{v['devices'][0]}_{v['param']}",
                        "|".join(v["devices"]), v["param"], v["constraint"], v["reason"]])


def export_reduced_netlist(netlist, variables, path):
    """在原网表基础上，把自由变量器件的尺寸改写为变量名（如 M1_w）。"""
    var_map = {}   # (device, param) -> 变量名（自由变量或其联动参考变量）
    for v in variables:
        target = v["devices"][0] + "_" + v["param"]
        if v["constraint"] == "free":
            var_map[(v["devices"][0], v["param"])] = target
        elif v["constraint"].startswith("="):
            ref = v["constraint"][1:]
            ref = ref[:-2] if ref.endswith(("_w", "_l")) else ref
            base = var_map.get((ref, v["param"]), ref + "_" + v["param"])
            var_map[(v["devices"][0], v["param"])] = base

    def rewrite(line):
        toks = line.split()
        if not toks or toks[0][0].upper() not in "MRC" or toks[0].startswith("."):
            return line
        for i, t in enumerate(toks):
            if "=" not in t:
                continue
            k, val = t.split("=", 1)
            kl = k.lower()
            key = (toks[0], "c" if (kl in ("c", "cap") and toks[0][0].upper() == "C")
                   else ("w" if kl == "w" else "l" if kl == "l" else kl))
            if key in var_map:
                toks[i] = f"{k}={{{var_map[key]}}}"
        return " ".join(toks)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    lines = [rewrite(l) for l in netlist.source_lines]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
