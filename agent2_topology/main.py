"""Agent2 入口：电路网表 -> 拓扑模块识别 + 参数约减 -> 交付物。

用法:
    python -m agent2_topology.main path/to/netlist.sp -o agent2_topology/output

交付物（评分点）:
  1. topology_result.json  拓扑模块识别结果 + 识别依据 + 变量个数统计
  2. variables.csv         约减后变量列表（含初值/PDK 上下界/类型）
  3. netlist_reduced.sp    约减后（变量化）网表，含 .param 块，可直接交付/仿真
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .netlist_parser import PDK_LIMITS, PDK_LIMITS_TEXT, parse_netlist
from .param_reducer import (CSV_FIELDS, count_raw_variables,
                            export_reduced_netlist, export_variables_csv,
                            reduce_parameters)
from .topology_recognizer import device_role_map, recognize


def build_result(nl, variables, modules, source, reduced_stats):
    counts = Counter(d.dtype for d in nl.devices)
    scopes = []
    for name, devs in nl.analysis_scopes():
        scopes.append({
            "name": name or "(顶层)",
            "devices": len(devs),
            "mos": sum(1 for d in devs if d.dtype == "M"),
            "modules": sum(1 for m in modules if m.get("scope") == name),
        })
    free_vars = [v for v in variables if v["constraint"] == "free"]
    linked_vars = [v for v in variables if v["constraint"] != "free"]
    before = count_raw_variables(nl)
    return {
        "schema_version": "1.1",
        "source_netlist": str(source),
        "netlist": {
            "total_devices": len(nl.devices),
            "device_counts": {k: counts.get(k, 0) for k in ("M", "R", "C", "L")},
            "scopes": scopes,
            "other_devices": sorted({d.dtype for d in nl.others}),
        },
        "modules": modules,
        "module_summary": dict(Counter(m["module_type"] for m in modules)),
        "device_roles": device_role_map(modules),
        "role_summary": dict(Counter(
            r for m in modules for r in (m.get("roles") or [m["module_type"]]))),
        "variable_reduction": {
            "before": before,
            "after_free": len(free_vars),
            "after_total": len(variables),
            "reduction_ratio": round(1 - len(free_vars) / before, 4) if before else 0,
            "free_variables": [v["name"] for v in free_vars],
            "linked_variables": [
                {"name": v["name"], "constraint": v["constraint"]}
                for v in linked_vars],
        },
        "pdk_limits": {
            "table": {k: {p: list(v) for p, v in d.items()}
                      for k, d in PDK_LIMITS.items()},
            "text": PDK_LIMITS_TEXT,
            "area_formula": {
                "transistor": "f*w*L*m*1.5",
                "capacitor": "W*L",
                "resistor": "W*L*2",
            },
        },
        "netlist_reduced": reduced_stats,
        "variables": variables,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="拓扑模块识别与参数约减 Agent")
    ap.add_argument("netlist", help="SPICE 网表路径")
    ap.add_argument("-o", "--output", default="agent2_topology/output")
    ap.add_argument("--quiet", action="store_true", help="只输出结果，不打印进度（供编排入口调用）")
    args = ap.parse_args(argv)

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    say = (lambda *a, **k: None) if args.quiet else print

    nl = parse_netlist(args.netlist)
    counts = Counter(d.dtype for d in nl.devices)
    say(f"[agent2] 解析到 {len(nl.devices)} 个器件 "
        f"(M:{counts.get('M', 0)} R:{counts.get('R', 0)} "
        f"C:{counts.get('C', 0)} L:{counts.get('L', 0)})，"
        f"{len(nl.analysis_scopes())} 个作用域")
    if nl.others:
        say(f"[agent2] 另有 {len(nl.others)} 个不做参数化的器件类型: "
            f"{sorted({d.dtype for d in nl.others})}")

    variables, modules = reduce_parameters(nl)
    reduced_stats = export_reduced_netlist(
        nl, variables, out / "netlist_reduced.sp")
    export_variables_csv(variables, out / "variables.csv")

    result = build_result(nl, variables, modules, args.netlist, reduced_stats)
    (out / "topology_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    say(f"[agent2] 模块识别: {len(modules)} 组"
        f"（多角色标注：{len(result['device_roles'])} 个器件参与识别）")
    for m in modules:
        scope = m.get("scope") or "顶层"
        roles = m.get("roles") or [m["module_type"]]
        extra = f" [角色: {'+'.join(roles)}]" if len(roles) > 1 else ""
        say(f"   - [{scope}] {m['module_type']}: "
            f"{', '.join(m['devices'])}{extra}")
    vr = result["variable_reduction"]
    say(f"[agent2] 变量约减: {vr['before']} -> 自由变量 {vr['after_free']} 个"
        f"（含联动共 {vr['after_total']} 个，约减率 {vr['reduction_ratio']:.0%}）")
    if reduced_stats.get("unpatched_variables"):
        say(f"[agent2] 提示: {len(reduced_stats['unpatched_variables'])} 个变量来自"
            f"层次实例展开，无法在原网表行内就地变量化")
    say(f"[agent2] 交付物已写入 {out}/ "
        f"(topology_result.json, variables.csv, netlist_reduced.sp)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
