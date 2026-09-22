#!/usr/bin/env python
"""变量表 ↔ 参数文件 转换与接口校验（对应服务器计划阶段 2）。

真实风险：第②问产出的变量名（如 `M7_w`）与官方 `param_file` 里的键名
（可能是 `NM1_m` 这类 PyAether `_cdf` 命名）如果不一致，仿真会静默用错尺寸。
所以本工具的第一职责不是转换，而是**校验并报告差集**。

用法:
    # 1) 接口校验（最常用，拿到真实 param_file 后第一件事）
    python tools/convert_params.py check \
        --variables agent2_topology/output/variables.csv \
        --param-file extractcdfVal_0.txt

    # 2) 把变量表写进参数文件（生成候选尺寸文件）
    python tools/convert_params.py apply \
        --variables agent2_topology/output/variables.csv \
        --param-file extractcdfVal_0.txt --out candidate.txt

    # 3) 从参数文件反向生成变量表（参数文件是唯一真值时）
    python tools/convert_params.py extract \
        --param-file extractcdfVal_0.txt --out variables.csv

退出码：check 发现缺键时返回 1（便于 CI / 脚本卡口）。
"""

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from q3_optimizer.paramfile import (ParamCodec, ParamFile,  # noqa: E402
                                    format_value, load_variables_csv,
                                    parse_value)

CSV_FIELDS = ["variable", "value", "min", "max", "unit", "type",
              "devices", "param", "constraint", "reason"]


def _norm(name):
    """归一化键名，用于跨命名风格的宽松匹配（大小写、点、横线）。"""
    return str(name).strip().lower().replace(".", "_").replace("-", "_")


def cmd_check(args):
    rows = load_variables_csv(args.variables)
    free = [r for r in rows if (r.get("constraint") or "free") == "free"]
    pf = ParamFile.read(args.param_file)
    keys = {_norm(k): k for k in pf.keys()}

    missing, nonnumeric, ok = [], [], []
    for r in free:
        name = r["name"]
        if _norm(name) not in keys:
            missing.append(name)
            continue
        real = keys[_norm(name)]
        si, _ = parse_value(pf.get(real))
        if si is None:
            nonnumeric.append((name, pf.get(real)))
        else:
            ok.append((name, pf.get(real)))

    print(f"参数文件键 {len(pf.keys())} 个；变量表自由变量 {len(free)} 个")
    print(f"  可对齐      : {len(ok)}")
    print(f"  键名缺失    : {len(missing)}")
    print(f"  值不可数值化: {len(nonnumeric)}")
    if ok:
        print("\n对齐示例:")
        for n, v in ok[:8]:
            print(f"    {n:16s} <- {v}")
    if missing:
        print("\n[缺失] 下列变量在参数文件里找不到对应键 —— 仿真会用到默认尺寸：")
        for n in missing:
            print(f"    {n}")
        print("  处理建议：确认 PyAether 的命名规则（如 NM1_m / M1_w），"
              "必要时在 variables.csv 或参数文件之间做一次重命名映射。")
    if nonnumeric:
        print("\n[注意] 下列键的值不是数值（可能是表达式），无法参与数值优化：")
        for n, v in nonnumeric:
            print(f"    {n} = {v}")

    if missing or nonnumeric:
        return 1
    print("\n接口校验: 通过")
    return 0


def cmd_apply(args):
    rows = {_norm(r["name"]): r for r in load_variables_csv(args.variables)}
    pf = ParamFile.read(args.param_file)
    codec = ParamCodec(pf, pf.keys())
    applied, skipped = [], []
    for key in pf.keys():
        r = rows.get(_norm(key))
        if not r:
            continue
        si, suf = parse_value(pf.get(key))
        new_si, new_suf = parse_value(r.get("value"))
        if si is None or new_si is None:
            skipped.append(key)
            continue
        pf.set(key, format_value(new_si, suf or new_suf))
        applied.append((key, pf.get(key)))
    out = Path(args.out)
    pf.write(out)
    print(f"已写出 {out}（应用 {len(applied)} 个变量，跳过 {len(skipped)} 个非数值键）")
    for k, v in applied[:10]:
        print(f"    {k} = {v}")
    return 0


def cmd_extract(args):
    pf = ParamFile.read(args.param_file)
    codec = ParamCodec(pf, pf.keys())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_FIELDS)
        for key in codec.numeric_keys():
            raw = pf.get(key)
            si, suf = parse_value(raw)
            w.writerow([key, raw, "", "", "", "float", key,
                        key.rsplit("_", 1)[-1], "free",
                        f"从 {Path(args.param_file).name} 提取（边界待补）"])
            n += 1
    print(f"已写出 {out}（{n} 个数值变量；min/max/type 需按 PDK 补齐）")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="变量表 ↔ 参数文件 转换与接口校验")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="校验变量表与参数文件的键名/数值对齐情况")
    c.add_argument("--variables", required=True)
    c.add_argument("--param-file", required=True)
    c.set_defaults(func=cmd_check)

    a = sub.add_parser("apply", help="把变量表的初值写进参数文件")
    a.add_argument("--variables", required=True)
    a.add_argument("--param-file", required=True)
    a.add_argument("--out", required=True)
    a.set_defaults(func=cmd_apply)

    e = sub.add_parser("extract", help="从参数文件反向生成变量表")
    e.add_argument("--param-file", required=True)
    e.add_argument("--out", required=True)
    e.set_defaults(func=cmd_extract)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
