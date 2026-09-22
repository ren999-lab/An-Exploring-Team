#!/usr/bin/env python
"""全链路演示入口：一句话进 → 结构化结果出。

为什么需要它
------------
三个 Agent 各自都能跑，但没有一个"直观演示"的入口：演示视频要一条命令跑完全链路，
答辩时评委问"怎么跑"也应该一条命令回答。本文件就是那个编排入口——
**不新增任何算法逻辑**，只负责按顺序调用已跑通的 Agent，并把 JSON 渲染成人能看的表格。

用法:
    # 只跑第①②问（第①问默认走纯规则，不需要 API Key；加 --llm 才调 Qwen）
    python demo.py "设计全差分运放，PM≥60°，工作电流3mA，增益尽可能大，面积尽可能小"

    # 指定网表
    python demo.py "..." --netlist tests/sample_netlist.sp

    # 全链路：再跑一遍第③问优化（mock 仿真器，本地可跑）
    python demo.py "..." --run-opt

    # 渲染已有的优化结果
    python demo.py "..." --opt results_case1/optimization_result.json

终端表格按东亚字符宽度对齐，中文不会错位；同时强制 stdout 走 UTF-8，
避免中文 Windows 控制台乱码（录演示视频时尤其重要）。
"""

import argparse
import csv
import json
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

WIDTH = 76


# ---------------------------------------------------------------------------
# 终端渲染（东亚宽度对齐）
# ---------------------------------------------------------------------------
def _dw(s):
    """字符串显示宽度：中文/全角算 2。"""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
               for c in str(s))


def _pad(s, width, align="left"):
    s = str(s)
    gap = max(0, width - _dw(s))
    return s + " " * gap if align == "left" else " " * gap + s


def _trunc(s, width):
    """按显示宽度截断并加省略号（避免表格里出现半截句子）。"""
    s = str(s)
    if _dw(s) <= width:
        return s
    out, w = "", 0
    for c in s:
        cw = 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
        if w + cw > width - 1:
            break
        out += c
        w += cw
    return out + "…"


def banner(title, ch="═"):
    print()
    print(ch * WIDTH)
    print(" " + title)
    print(ch * WIDTH)


def section(title):
    print()
    print(f"【{title}】")


def table(headers, rows, aligns=None):
    """等宽终端表格（按显示宽度对齐）。"""
    if not rows:
        print("  （无）")
        return
    aligns = aligns or ["left"] * len(headers)
    widths = [_dw(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], _dw(c))
    print("  " + "  ".join(_pad(h, widths[i]) for i, h in enumerate(headers)))
    print("  " + "  ".join("-" * widths[i] for i in range(len(headers))))
    for r in rows:
        print("  " + "  ".join(_pad(c, widths[i], aligns[i])
                               for i, c in enumerate(r)))


def kv(label, value):
    print(f"  {_pad(label, 14)}: {value}")


# ---------------------------------------------------------------------------
# 各阶段渲染
# ---------------------------------------------------------------------------
def render_spec(spec):
    section("第①问  自然语言 → 结构化 Spec")
    kv("来源", spec.get("source", "-"))
    kv("原始输入", spec.get("raw_text", "")[:60] + "…")

    print("\n  硬约束项")
    rows = []
    for c in spec.get("hard_constraints", []):
        src = ("赛题默认补齐" if c.get("assumed")
               else "比较词推断" if c.get("op_inferred") else "原文")
        rows.append([c.get("key", ""), c.get("op") or "-", _num(c.get("value")),
                     c.get("unit") or "-", c.get("cond") or "-", src])
    table(["指标", "比较", "数值", "单位", "条件", "来源"], rows)

    print("\n  优化目标")
    rows = []
    for t in spec.get("optimization_targets", []):
        src = ("赛题默认补齐" if t.get("assumed")
               else f"由 {t['derived_from']} 派生" if t.get("derived_from") else "原文")
        rows.append([t.get("priority"), t.get("key", ""), t.get("direction", ""),
                     t.get("unit", ""), src])
    table(["优先级", "指标", "方向", "单位", "来源"], rows)

    print("\n  指标单位表")
    units = spec.get("units") or {}
    print("  " + "  ".join(f"{k}={v}" for k, v in units.items()))

    if spec.get("assumptions"):
        print("\n  解析假设（可审计）")
        for a in spec["assumptions"]:
            print(f"    · {a}")

    cc = spec.get("cross_check") or {}
    if cc:
        print("\n  LLM × 规则 交叉校验")
        rows = [
            ["仅 LLM 给出的硬约束", _join(cc.get("llm_only_constraints"))],
            ["仅规则给出的硬约束", _join(cc.get("rule_only_constraints"))],
            ["仅 LLM 给出的目标", _join(cc.get("llm_only_targets"))],
            ["仅规则给出的目标", _join(cc.get("rule_only_targets"))],
            ["数值不一致", _join([f"{m['key']}: {m['llm']} vs {m['rule']}"
                                  for m in cc.get("value_mismatch") or []])],
        ]
        table(["差异类型", "内容"], rows)
        print("\n  → 规则引擎作为独立第二意见，补齐了 LLM 的遗漏项")


def render_topology(topo, topo_dir=None):
    section("第②问  拓扑模块识别与参数约减")
    nl = topo.get("netlist", {})
    kv("网表", topo.get("source_netlist", "-"))
    kv("器件", f"{nl.get('total_devices', '-')} 个  "
               f"({_kv(nl.get('device_counts', {}))})")
    kv("作用域", f"{len(nl.get('scopes', []))} 个")

    print(f"\n  识别到 {len(topo.get('modules', []))} 个模块")
    rows = []
    for m in topo.get("modules", []):
        roles = m.get("roles") or [m["module_type"]]
        extra = "+".join(r for r in roles if r != m["module_type"])
        rows.append([m.get("module_type", ""),
                     ", ".join(m.get("devices", [])),
                     extra or "-",
                     _trunc(m.get("evidence", ""), 30)])
    table(["模块类型", "器件", "附加角色", "识别依据"], rows)

    roles_map = topo.get("device_roles") or {}
    if roles_map:
        multi = {k: v for k, v in roles_map.items() if len(v) > 1}
        if multi:
            print("\n  多角色器件（同一器件承担多个功能角色）")
            rows = [[k, "、".join(v)] for k, v in multi.items()]
            table(["器件", "角色"], rows)

    vr = topo.get("variable_reduction", {})
    print()
    kv("变量约减", f"{vr.get('before')} → 自由变量 {vr.get('after_free')} 个"
                   f"（含联动共 {vr.get('after_total')} 个）")
    kv("约减率", f"{(vr.get('reduction_ratio') or 0) * 100:.0f}%")
    if topo_dir:
        kv("交付物", f"{topo_dir}/  (topology_result.json, variables.csv, "
                     f"netlist_reduced.sp)")


def render_variables(csv_rows, limit=12):
    section("第②问  变量统计列表（variables.csv 节选）")
    rows = []
    for r in csv_rows[:limit]:
        rows.append([r.get("variable", ""), r.get("value", ""),
                     r.get("min", "") or "-", r.get("max", "") or "-",
                     r.get("unit", "") or "-", r.get("type", "") or "-",
                     r.get("constraint", "")])
    table(["变量", "初值", "下界", "上界", "单位", "类型", "约束"], rows)
    if len(csv_rows) > limit:
        print(f"  … 共 {len(csv_rows)} 行，其余见 variables.csv")


def render_optimization(opt):
    section("第③问  基于约束的多目标尺寸优化")
    cons = opt.get("constraints", {})
    best = opt.get("best_result") or {}
    first = opt.get("first_result") or {}
    mode = opt.get("mode", "-")
    kv("运行模式", mode + ("   （mock 为本地测试替身，不代表真实电路）"
                           if mode == "mock" else ""))
    kv("优化变量", f"{len(opt.get('variables', []))} 个")
    kv("评估次数", f"{opt.get('n_evals')} 次 / {opt.get('generations')} 代")
    kv("总耗时", f"{opt.get('elapsed_sec')} s")
    kv("约束满足", "是" if opt.get("feasible")
       else f"否 → {opt.get('best_violations')}")

    print()
    rows = [
        ["DCGain", _num(first.get("dc_gain_db")), _num(best.get("dc_gain_db")),
         f"≥ {cons.get('dc_gain_min')} dB"],
        ["UGB", _num(first.get("ugb_hz")), _num(best.get("ugb_hz")), "越大越好"],
        ["PM", _num(first.get("pm_deg")), _num(best.get("pm_deg")),
         f"≥ {cons.get('pm_min')} deg"],
        ["GM", _num(first.get("gm_db")), _num(best.get("gm_db")),
         f"≤ {cons.get('gm_max')} dB"],
        ["I_OPA", _num(first.get("i_opa_a")), _num(best.get("i_opa_a")),
         f"≤ {cons.get('i_opa_max')} A"],
        ["Area", _num(first.get("area_um2")), _num(best.get("area_um2")),
         "越小越好"],
    ]
    table(["指标", "初始值", "最优值", "要求"], rows)

    sa = opt.get("self_assessment") or {}
    if sa:
        print(f"\n  赛题 6.3 评分口径自评")
        cd = sa.get("constraint_detail") or {}
        crows = [[k, "达标" if (cd.get(k) or {}).get("passed") else "未达标",
                  f"{(cd.get(k) or {}).get('points')}/10"] for k in ("PM", "GM", "I_OPA")]
        table(["约束项", "判定", "得分"], crows)
        kv("约束项自评", f"{sa.get('constraint_score')} / 30 分（客观口径）")
        rr = sa.get("ratios_vs_baseline") or {}
        kv("相对初始解", f"DCGain {_num(rr.get('dc_gain'))}x   "
                        f"UGB {_num(rr.get('ugb'))}x   Area {_num(rr.get('area'))}x")
        print("    （目标项 40 分按与冠军成绩的比例给分，"
              "无冠军成绩故只报改进倍数）")

    if opt.get("pareto"):
        print(f"\n  Pareto 非支配解 {len(opt['pareto'])} 个"
              f"（完整数据见 optimization_result.json）")


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _num(v):
    if v is None:
        return "-"
    return f"{v:.6g}" if isinstance(v, float) else str(v)


def _join(xs):
    return "、".join(str(x) for x in xs) if xs else "无"


def _kv(d):
    return "、".join(f"{k}={v}" for k, v in d.items()) if d else "-"


def load_csv_rows(path):
    p = Path(path)
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
DEFAULT_TEXT = ("请设计一个带有共模反馈的全差分运放，要求：DC增益不低于95dB，"
                "单位增益带宽至少60MHz，相位裕度大于55度，所有PVT下工作电流"
                "不超过3mA，并尽量减小面积。")


def main(argv=None):
    # 中文 Windows 控制台默认 GBK，会把中文输出打成乱码
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="赛题二 全链路演示入口")
    ap.add_argument("text", nargs="*", help="自然语言性能描述")
    ap.add_argument("--netlist", default="tests/sample_netlist.sp")
    ap.add_argument("--llm", action="store_true",
                    help="第①问走 Qwen3.8-Max（默认纯规则，不需要 API Key）")
    ap.add_argument("--strict", action="store_true", help="不补赛题默认约束")
    ap.add_argument("--spec-out", default="agent1_spec_parser/output/spec.json")
    ap.add_argument("--topo-out", default="agent2_topology/output")
    ap.add_argument("--opt", default=None, help="渲染已有的 optimization_result.json")
    ap.add_argument("--run-opt", action="store_true",
                    help="现场跑一遍第③问优化（mock 仿真器）")
    ap.add_argument("--opt-out", default="demo_output")
    ap.add_argument("--verbose", action="store_true",
                    help="显示子步骤（agent1/agent2）的进度日志；默认只显示渲染后的结果")
    args = ap.parse_args(argv)

    text = " ".join(args.text).strip() or DEFAULT_TEXT

    banner("模拟电路 AI 智能化设计  全链路演示")
    print(f"  输入：{text}")

    # ---- 第①问 ----
    # 子 Agent 的进度日志默认静音，让演示输出保持整洁；--verbose 才显示
    child_quiet = [] if args.verbose else ["--quiet"]
    import agent1_spec_parser.main as a1
    a1_argv = []
    if not args.llm:
        a1_argv.append("--no-llm")
    if args.strict:
        a1_argv.append("--strict")
    a1_argv += child_quiet + ["-o", args.spec_out, text]
    rc = a1.main(a1_argv)
    spec = json.loads(Path(args.spec_out).read_text(encoding="utf-8"))
    render_spec(spec)

    # ---- 第②问 ----
    nl_path = Path(args.netlist)
    if not nl_path.exists():
        print(f"\n[跳过第②问] 找不到网表 {nl_path}")
        return 1 if rc else 0
    import agent2_topology.main as a2
    a2.main([str(nl_path), "-o", args.topo_out] + child_quiet)
    topo = json.loads((Path(args.topo_out) / "topology_result.json")
                      .read_text(encoding="utf-8"))
    var_csv = Path(args.topo_out) / "variables.csv"
    render_topology(topo, args.topo_out)
    render_variables(load_csv_rows(var_csv))

    # ---- 第③问 ----
    opt = None
    if args.opt:
        opt = json.loads(Path(args.opt).read_text(encoding="utf-8"))
    elif args.run_opt:
        import shutil
        import optimization
        opt_dir = Path(args.opt_out)
        opt_dir.mkdir(parents=True, exist_ok=True)
        # 用参数文件副本，绝不改写仓库里的测试 fixture
        # （optimization.py 默认会把最优解写回 --param_file）
        src_param = Path("tests/data/param_init.txt")
        param_copy = opt_dir / "param_init.txt"
        shutil.copyfile(src_param, param_copy)
        optimization.main([
            "--param_file", str(param_copy),
            "--variables-csv", str(var_csv) if var_csv.exists()
            else "tests/data/variables.csv",
            "--mock", "--max-evals", "80", "--pop-size", "8",
            "--output_path", str(opt_dir), "--output_file", "output.log",
        ])
        opt = json.loads((opt_dir / "optimization_result.json")
                         .read_text(encoding="utf-8"))
    if opt:
        render_optimization(opt)

    # ---- 产物清单 ----
    section("输出文件")
    outs = [args.spec_out,
            str(Path(args.topo_out) / "topology_result.json"),
            str(Path(args.topo_out) / "variables.csv"),
            str(Path(args.topo_out) / "netlist_reduced.sp")]
    if opt:
        outs += [str(Path(args.opt_out) / "optimization_result.json"),
                 str(Path(args.opt_out) / "best_param_file.txt"),
                 str(Path(args.opt_out) / "optimization_log.jsonl")]
    for o in outs:
        mark = "✓" if Path(o).exists() else "×"
        print(f"  {mark} {o}")

    print()
    print("═" * WIDTH)
    print("  完成。完整技术报告：python tools/make_report.py --run-local")
    print("═" * WIDTH)
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
