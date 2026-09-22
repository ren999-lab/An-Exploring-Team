#!/usr/bin/env python
"""第③问提交入口：基于约束的多目标尺寸优化。

命令行与赛题官方 `run_opt_command.sh` 完全对齐：

    python optimization.py --ae_lib <lib> --ae_cell <cell> --ae_view <view> \
        --mde_cell <tb> --mde_view <corner> --output_path ./results_case1 \
        --output_file output.log --param_file extractcdfVal_0.txt

    （参数含义见赛题 PDF 第 13–14 页：蓝色为赛题给定，绿色为输出路径，
      红色 --param_file 为待优化的电路尺寸参数文件）

本地无服务器时可用 `--mock` 跑通全流程（用解析式测试替身，仅验证算法与工程链路）：

    python optimization.py --param_file tests/data/param_init.txt \
        --variables-csv tests/data/variables.csv --mock --output_path ./_local_run

设计要点
--------
1. **可行性优先**：PM/GM/I_OPA 在任意 PVT 不满足即该项 0 分，因此选择算子
   宁可放弃目标收益也不接受不可行解（见 q3_optimizer/optimize.py）。
2. **预算倒推**：3 小时总时限，先跑一次真实仿真测出单次耗时，再反推可用评估次数，
   并留 20% 余量给反标与全 PVT 验证。
3. **可续跑**：每次迭代落盘 checkpoint，VPN 掉线后加 `--resume` 继续，不浪费已花的时间。
4. **过程留痕**：逐次评估写 jsonl，供技术报告"运行次数/运行时间/内存"一项取证。
"""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from q3_optimizer.optimize import (Constraints, ObjectiveWeights, ParamSpace,
                                   differential_evolution, plan_budget)
from q3_optimizer.paramfile import (SPICE_SUFFIX, ParamCodec, ParamFile,
                                    build_bounds, free_variables,
                                    load_variables_csv, parse_value)
from q3_optimizer.simulator import MockSimulator, CommandSimulator

# 单位列 -> SPICE 后缀（用于把 variables.csv 里的 um 边界换算到 SI）
UNIT_TO_SUFFIX = {"um": "u", "µm": "u", "u": "u", "nm": "n", "mm": "m",
                  "mf": "m", "pf": "p", "nf": "n", "ff": "f", "": ""}

DEFAULT_TIME_BUDGET = 3 * 3600.0     # 赛题：单个运放电路运行时间上限 3 小时


def build_parser():
    ap = argparse.ArgumentParser(
        description="赛题二第③问：基于约束的多目标尺寸优化")
    # ---- 赛题官方参数（保持同名，便于被 run_opt_command.sh 调用）----
    ap.add_argument("--ae_lib", default=None, help="电路 Lib 名（赛题给定）")
    ap.add_argument("--ae_cell", default=None, help="电路 Cell 名（赛题给定）")
    ap.add_argument("--ae_view", default=None, help="电路 View 名（赛题给定）")
    ap.add_argument("--mde_cell", default=None, help="TestBench Cell（赛题给定）")
    ap.add_argument("--mde_view", default=None, help="MDE L2 View（赛题给定）")
    ap.add_argument("--output_path", default="./results_case1")
    ap.add_argument("--output_file", default="output.log")
    ap.add_argument("--param_file", required=True,
                    help="初始电路尺寸参数文件（如 extractcdfVal_0.txt）")
    # ---- 本地/工程参数 ----
    ap.add_argument("--variables-csv", default=None,
                    help="第②问输出的 variables.csv，用于确定优化变量与边界")
    ap.add_argument("--sim-cmd", default=None,
                    help="真实仿真命令模板，含 {param_file} 与 {out} 占位符")
    ap.add_argument("--mock", action="store_true",
                    help="使用解析式测试替身跑通流程（本地开发用，不代表真实电路）")
    ap.add_argument("--time-budget", type=float, default=DEFAULT_TIME_BUDGET,
                    help="总时间预算（秒），默认 3 小时")
    ap.add_argument("--reserve", type=float, default=0.2,
                    help="预留比例（反标/全 PVT 验证/掉线重连）")
    ap.add_argument("--max-evals", type=int, default=None, help="直接指定评估次数上限")
    ap.add_argument("--pop-size", type=int, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--checkpoint", default=None)
    ap.add_argument("--resume", action="store_true", help="从 checkpoint 续跑")
    ap.add_argument("--no-write-back", action="store_true",
                    help="不把最优解写回 --param_file（默认写回，供反标脚本使用）")
    return ap


def _scale_for(key, row, codec):
    """把 variables.csv 的边界换算到该键在参数文件里的数值尺度。"""
    suf = codec.suffix.get(key, "")
    if suf and suf.lower() in SPICE_SUFFIX:
        return SPICE_SUFFIX[suf.lower()]
    unit = (row.get("unit") or "").strip().lower()
    u_suf = UNIT_TO_SUFFIX.get(unit, "")
    return SPICE_SUFFIX.get(u_suf, 1.0) if u_suf else 1.0


def resolve_space(pf, variables_csv, cli_pop):
    """决定优化变量与边界，返回 (keys, ParamSpace, notes, codec)。

    边界来源有两种，**单位尺度不同，不能混用**：
    * variables.csv 的 min/max：以 CSV 的 unit 列为准（如 um），需要换算到 SI；
    * CSV 里没有 min/max 时的回退窗口：直接由参数文件初值算出，本身已是 SI。
    早先版本对两者一律乘后缀系数，导致 Cc 的上下界被再乘 1e-12（约 6e-25），
    PM 直接掉到 0.07 度。这里明确区分。
    """
    codec = ParamCodec(pf, pf.keys())
    numeric = codec.numeric_keys()
    notes = []

    if variables_csv and Path(variables_csv).exists():
        rows = {v["name"]: v for v in load_variables_csv(variables_csv)}
        keys, lo, hi, is_int = [], [], [], []
        for name, row in rows.items():
            if (row.get("constraint") or "free") != "free":
                continue                      # 联动变量不是独立优化变量
            if not pf.has(name):
                continue                      # 参数文件里没有的键跳过
            si, _ = parse_value(pf.get(name))
            if si is None:
                continue
            is_i = (row.get("type") or "").strip().lower() == "int"
            lo_raw, hi_raw = row.get("min"), row.get("max")
            if (not is_i and lo_raw not in (None, "")
                    and hi_raw not in (None, "")):
                scale = _scale_for(name, row, codec)
                lo_v, hi_v = float(lo_raw) * scale, float(hi_raw) * scale
            else:
                # 无边界信息（或无单位量）：以初值为中心的保守窗口，已是 SI
                lo_v, hi_v = si * 0.2, si * 5.0
            if is_i:
                lo_v, hi_v = max(1, int(round(lo_v))), max(2, int(round(hi_v)))
            if hi_v <= lo_v:
                lo_v, hi_v = si * 0.2, si * 5.0
            keys.append(name)
            lo.append(lo_v)
            hi.append(hi_v)
            is_int.append(is_i)
        if keys:
            notes.append(f"变量来自 {Path(variables_csv).name}"
                         f"（自由变量 {len(keys)} 个）")
            return keys, ParamSpace(keys, lo, hi, is_int), notes, codec
        notes.append("variables.csv 中没有任何变量能在参数文件里找到，"
                     "回退为参数文件中的全部数值键")

    keys, lo, hi, is_int = [], [], [], []
    for k in numeric:
        si, _ = parse_value(pf.get(k))
        if si is None or si == 0:
            continue
        # 无 PDK 信息时的保守窗口：初值的 0.2x ~ 5x
        keys.append(k)
        lo.append(si * 0.2)
        hi.append(si * 5.0)
        is_int.append(float(si).is_integer() and 1 <= si <= 20)
    notes.append(f"变量来自参数文件全部数值键（{len(keys)} 个），"
                 f"边界暂用初值 0.2x~5x，拿到 PDK 信息后请改用 --variables-csv")
    return keys, ParamSpace(keys, lo, hi, is_int), notes, codec


def _f(s, fallback):
    try:
        if s is None or str(s).strip() == "":
            return float(fallback)
        return float(str(s).strip())
    except ValueError:
        return float(fallback)


def main(argv=None):
    args = build_parser().parse_args(argv)
    out_dir = Path(args.output_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    t_start = time.time()

    pf = ParamFile.read(args.param_file)
    if not args.no_write_back:
        backup = out_dir / (Path(args.param_file).name + ".orig")
        if not backup.exists():
            shutil.copyfile(args.param_file, backup)

    keys, space, notes, codec = resolve_space(pf, args.variables_csv, args.pop_size)
    if not keys:
        print("[opt] 没有解析到任何可优化变量，退出", file=sys.stderr)
        return 2
    x0 = [parse_value(pf.get(k))[0] for k in keys]
    x0 = space.clip([v if v is not None else (lo + hi) / 2
                     for v, lo, hi in zip(x0, space.lo, space.hi)])

    print(f"[opt] 优化变量 {len(keys)} 个: {', '.join(keys)}")
    for n in notes:
        print(f"[opt] 说明: {n}")

    log_path = out_dir / "optimization_log.jsonl"
    log_f = open(log_path, "a", encoding="utf-8")

    def logger(rec):
        log_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log_f.flush()

    # ---- 仿真器 ----
    if args.mock:
        simulate = MockSimulator()
        print("[opt] 使用 MockSimulator（测试替身，数值无物理意义，仅验证算法链路）")
        per_eval_hint = 0.0
    elif args.sim_cmd:
        def _writer(param_file, ks, values, od):
            tmp = ParamFile.read(args.param_file)
            c = ParamCodec(tmp, ks)
            c.apply(ks, values)
            (Path(od) / Path(param_file).name).write_text(c.pf.text(),
                                                          encoding="utf-8")
        simulate = CommandSimulator(args.sim_cmd, workdir=".",
                                    log_name=args.output_file,
                                    param_writer=_writer)
        per_eval_hint = None
    else:
        print("[opt] 未提供 --sim-cmd：真实仿真命令模板尚未确定。\n"
              "      请先按 docs/server_onboarding_plan.md 阶段 1 抓取官方脚本，\n"
              "      再用 --sim-cmd \"bash run_opt_command.sh {param_file}\" 运行；\n"
              "      本地自测可加 --mock。", file=sys.stderr)
        return 3

    # ---- 先跑一次，测出单次仿真耗时 ----
    t0 = time.time()
    first = simulate(x0, keys=keys, param_file=args.param_file, out_dir=out_dir)
    per_eval = time.time() - t0 if per_eval_hint is None else per_eval_hint
    cons = Constraints()
    print(f"[opt] 初始解: Gain={_g(first.dc_gain_db)} UGB={_g(first.ugb_hz)} "
          f"PM={_g(first.pm_deg)} GM={_g(first.gm_db)} I={_g(first.i_opa_a)}")
    print(f"[opt] 初始解违反项: {cons.check(first) or '无（可行）'}")
    print(f"[opt] 单次仿真耗时 ≈ {per_eval:.2f}s")

    # ---- 预算规划 ----
    if args.max_evals is not None:
        budget = {"max_evaluations": args.max_evals,
                  "pop_size": args.pop_size or 8,
                  "generations": args.max_evals // (args.pop_size or 8),
                  "usable_seconds": args.time_budget * (1 - args.reserve),
                  "per_eval_seconds": per_eval}
    else:
        if args.mock:
            budget = {"max_evaluations": 400, "pop_size": args.pop_size or 10,
                      "generations": 40, "usable_seconds": 0,
                      "per_eval_seconds": 0.0}
        else:
            budget = plan_budget(args.time_budget, per_eval,
                                 reserve=args.reserve, pop_size=args.pop_size)
    print(f"[opt] 预算: 可用 {budget['usable_seconds']:.0f}s -> "
          f"最多评估 {budget['max_evaluations']} 次，"
          f"种群 {budget['pop_size']}，约 {budget['generations']} 代")

    # ---- 优化 ----
    # 必须把 keys/param_file 绑定进 evaluate：DE 内部只调用 evaluate(vec)，
    # 不绑定的话 MockSimulator/CommandSimulator 拿不到变量名，会退化成默认参数。
    def evaluate(vec):
        return simulate(list(vec), keys=keys,
                        param_file=args.param_file, out_dir=out_dir)

    ckpt = args.checkpoint or str(out_dir / "checkpoint.json")
    result = differential_evolution(
        evaluate, space, x0, budget, cons=cons, weights=ObjectiveWeights(),
        seed=args.seed, logger=logger, checkpoint_path=ckpt,
        resume=args.resume,
        wall_clock_budget=(None if args.mock
                           else args.time_budget * (1 - args.reserve)))

    elapsed = time.time() - t_start
    best_x = result["best_x"]
    best = result["best_result"]
    feasible = cons.check(_res(best))
    summary = {
        "ae": {"lib": args.ae_lib, "cell": args.ae_cell, "view": args.ae_view,
               "mde_cell": args.mde_cell, "mde_view": args.mde_view},
        "param_file": str(args.param_file),
        "mode": "mock" if args.mock else "command",
        "variables": keys,
        "x0": x0,
        "best_x": best_x,
        "best_result": best,
        "best_violations": feasible,
        "feasible": not feasible,
        "constraints": {"pm_min": cons.pm_min, "gm_max": cons.gm_max,
                        "i_opa_max": cons.i_opa_max,
                        "dc_gain_min": cons.dc_gain_min},
        "budget": budget,
        "n_evals": result["n_evals"],
        "generations": result["generations"],
        "elapsed_sec": round(elapsed, 2),
        "pareto": result["pareto"],
        "checkpoint": ckpt,
    }
    (out_dir / "optimization_result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 写回最优尺寸 ----
    codec.apply(keys, best_x)
    best_path = out_dir / "best_param_file.txt"
    codec.pf.write(best_path)
    if not args.no_write_back:
        codec.pf.write(args.param_file)
        print(f"[opt] 最优尺寸已写回 {args.param_file}（原文件备份在 "
              f"{out_dir / (Path(args.param_file).name + '.orig')}）")
    print(f"[opt] 最优参数副本: {best_path}")

    # ---- 人类可读总结（对应 --output_file）----
    req_gain = f">= {cons.dc_gain_min} dB"
    req_pm = f">= {cons.pm_min} deg"
    req_gm = f"<= {cons.gm_max} dB"
    req_i = f"<= {cons.i_opa_max} A"
    lines = [
        "=" * 68,
        "赛题二 第③问 尺寸优化结果总结",
        "=" * 68,
        f"模式            : {summary['mode']}",
        f"优化变量 ({len(keys)})   : {', '.join(keys)}",
        f"评估次数        : {result['n_evals']}   （{result['generations']} 代）",
        f"总耗时          : {elapsed:.2f}s",
        f"约束满足        : {'是' if summary['feasible'] else '否 -> ' + str(feasible)}",
        "-" * 68,
        f"{'指标':<12}{'初始值':>16}{'最优值':>16}{'要求':>18}",
        f"{'DCGain':<12}{_g(first.dc_gain_db):>16}{_g(best.get('dc_gain_db')):>16}{req_gain:>18}",
        f"{'UGB':<12}{_g(first.ugb_hz):>16}{_g(best.get('ugb_hz')):>16}{'越大越好':>18}",
        f"{'PM':<12}{_g(first.pm_deg):>16}{_g(best.get('pm_deg')):>16}{req_pm:>18}",
        f"{'GM':<12}{_g(first.gm_db):>16}{_g(best.get('gm_db')):>16}{req_gm:>18}",
        f"{'I_OPA':<12}{_g(first.i_opa_a):>16}{_g(best.get('i_opa_a')):>16}{req_i:>18}",
        f"{'Area':<12}{_g(first.area_um2):>16}{_g(best.get('area_um2')):>16}{'越小越好':>18}",
        "=" * 68,
        f"Pareto 非支配解: {len(summary['pareto'])} 个（见 optimization_result.json）",
    ]
    log_f.write("\n".join(lines) + "\n")
    log_f.close()
    (out_dir / args.output_file).write_text("\n".join(lines) + "\n",
                                            encoding="utf-8")
    print("\n".join(lines))
    return 0


class _ResDict:
    def __init__(self, d):
        self.__dict__.update(d or {})
        self.ok = bool((d or {}).get("ok"))


def _res(d):
    return _ResDict(d)


def _g(v):
    if v is None:
        return "-"
    return f"{v:.6g}"


if __name__ == "__main__":
    sys.exit(main())
