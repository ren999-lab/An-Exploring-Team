"""Agent2 入口：电路网表 → 拓扑模块识别 + 参数约减 → 交付物。

用法:
    python -m agent2_topology.main path/to/netlist.sp -o agent2_topology/output

交付物（评分点）:
  1. topology_result.json  拓扑模块识别结果 + 识别依据
  2. variables.csv         约减后变量列表
  3. netlist_reduced.sp    约减后（变量化）网表
"""

import argparse
import json
import sys
from pathlib import Path

from .netlist_parser import parse_netlist
from .param_reducer import reduce_parameters, export_variables_csv, export_reduced_netlist


def main():
    ap = argparse.ArgumentParser(description="拓扑模块识别与参数约减 Agent")
    ap.add_argument("netlist", help="SPICE 网表路径")
    ap.add_argument("-o", "--output", default="agent2_topology/output")
    args = ap.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    nl = parse_netlist(args.netlist)
    print(f"[agent2] 解析到 {len(nl.devices)} 个器件 "
          f"(M:{len(nl.mos_list())} R:{sum(1 for d in nl.devices if d.dtype=='R')} "
          f"C:{sum(1 for d in nl.devices if d.dtype=='C')})")

    variables, modules = reduce_parameters(nl)

    # 1) 识别结果
    result = {
        "modules": modules,
        "total_devices": len(nl.devices),
        "variables_before": sum(2 if d.dtype == "M" else 1 for d in nl.devices),
        "variables_after_free": sum(1 for v in variables if v["constraint"] == "free"),
        "variables": variables,
    }
    (out / "topology_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2) 变量 CSV
    export_variables_csv(variables, out / "variables.csv")

    # 3) 约减后网表
    export_reduced_netlist(nl, variables, out / "netlist_reduced.sp")

    print(f"[agent2] 模块识别: {len(modules)} 组")
    for m in modules:
        print(f"   - {m['module_type']}: {', '.join(m['devices'])}")
    n_free = result["variables_after_free"]
    print(f"[agent2] 变量约减: {result['variables_before']} → 自由变量 {n_free} 个")
    print(f"[agent2] 交付物已写入 {out}/ (topology_result.json, variables.csv, netlist_reduced.sp)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
