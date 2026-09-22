"""第③问优化器回归测试：参数文件保真、指标解析、约束优先、预算规划、端到端。

运行: python -m unittest discover -s tests -t . -v
全部用例都不需要服务器，也不需要真实仿真器。
"""

import json
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from q3_optimizer.optimize import (Constraints, ObjectiveWeights,  # noqa: E402
                                   ParamSpace, differential_evolution,
                                   plan_budget, score)
from q3_optimizer.paramfile import (ParamCodec, ParamFile,       # noqa: E402
                                    build_bounds, format_value,
                                    load_variables_csv, parse_value)
from q3_optimizer.simulator import (MockSimulator, SimResult,     # noqa: E402
                                    parse_metrics)

DATA = ROOT / "tests" / "data"
SCRATCH_ROOT = ROOT / "tests" / "_scratch"


class ScratchCase(unittest.TestCase):
    """用工作区内的临时目录，避免依赖系统 temp 目录的权限。"""

    def scratch(self, name):
        d = SCRATCH_ROOT / name
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(SCRATCH_ROOT, ignore_errors=True)

MDE_LOG = """
* MDE simulation summary
Corner: TT   Temp=27   Vdd=1.20
  DC Gain        = 82.35 dB
  Unity Gain Bandwidth = 145.2 MHz
  Phase Margin   = 61.5 deg
  Gain Margin    = -13.2 dB
  I_OPA          = 2.15 mA
Corner: SS   Temp=125  Vdd=1.08
  DC Gain        = 76.10 dB
  Unity Gain Bandwidth = 98.7 MHz
  Phase Margin   = 52.3 deg
  Gain Margin    = -10.4 dB
  I_OPA          = 2.85 mA
"""


class TestParamFileFidelity(unittest.TestCase):
    """参数文件格式未知，保真回写是第一原则。"""

    def test_roundtrip_is_byte_identical(self):
        src = DATA / "param_init.txt"
        pf = ParamFile.read(src)
        original = src.read_text(encoding="utf-8")
        self.assertEqual(pf.text(), original)

    def test_set_preserves_prefix_sep_and_comment(self):
        pf = ParamFile.read(DATA / "param_init.txt")
        pf.set("M5_w", "12u")
        line = pf.lines[pf.entries["M5_w"].line_index]
        self.assertEqual(line, "M5_w=12u      # 输出级宽度")

    def test_comments_and_blank_lines_survive(self):
        pf = ParamFile.read(DATA / "param_init.txt")
        pf.set("M1_w", "8u")
        self.assertIn("# extractcdfVal_0.txt 示意样例", pf.text())

    def test_missing_key_is_appended(self):
        pf = ParamFile.read(DATA / "param_init.txt")
        pf.set("M9_w", "4u")
        self.assertIn("M9_w=4u", pf.lines)

    def test_colon_and_spaced_separators(self):
        pf = ParamFile.from_text("A_w : 3u\nB_l = 0.5u\nC_m=2\n")
        self.assertEqual(pf.get("A_w"), "3u")
        pf.set("A_w", "4u")
        self.assertIn("A_w : 4u", pf.text())
        pf.set("B_l", "0.6u")
        self.assertIn("B_l = 0.6u", pf.text())


class TestValueCodec(unittest.TestCase):
    def test_parse_value_suffixes(self):
        self.assertAlmostEqual(parse_value("36u")[0], 36e-6)
        self.assertEqual(parse_value("36u")[1], "u")
        self.assertAlmostEqual(parse_value("3p")[0], 3e-12)
        self.assertEqual(parse_value("12")[0], 12.0)
        self.assertEqual(parse_value("2.5meg")[0], 2.5e6)
        self.assertIsNone(parse_value("W1*2")[0])

    def test_format_value_keeps_suffix_style(self):
        self.assertEqual(format_value(36e-6, "u"), "36u")
        self.assertEqual(format_value(3e-12, "p"), "3p")
        self.assertEqual(format_value(12, ""), "12")

    def test_codec_applies_only_numeric_keys(self):
        pf = ParamFile.read(DATA / "param_init.txt")
        codec = ParamCodec(pf, pf.keys())
        keys = codec.numeric_keys()
        self.assertIn("M1_w", keys)
        vec = codec.to_vector()
        self.assertEqual(len(vec), len(keys))
        codec.apply(["M1_w"], [20e-6])
        self.assertEqual(codec.pf.get("M1_w"), "20u")


class TestMetricParsing(unittest.TestCase):
    def test_worst_case_over_corners(self):
        """赛题口径是"所有 PVT 的最坏情况"，同一指标多次出现要取最坏值。"""
        m = parse_metrics(MDE_LOG)
        self.assertAlmostEqual(m["dc_gain_db"], 76.10)
        self.assertAlmostEqual(m["pm_deg"], 52.3)
        self.assertAlmostEqual(m["gm_db"], -10.4)
        self.assertAlmostEqual(m["i_opa_a"], 2.85e-3)
        self.assertAlmostEqual(m["ugb_hz"], 98.7e6)

    def test_ok_flag_requires_core_metrics(self):
        self.assertTrue(parse_metrics(MDE_LOG)["ok"])
        self.assertFalse(parse_metrics("nothing here")["ok"])

    def test_alternate_unit_spellings(self):
        m = parse_metrics("PM: 60 deg\nDC gain 80 dB\nUGB = 1.2e8 Hz\n"
                          "I_OPA = 1.5 mA\n")
        self.assertAlmostEqual(m["pm_deg"], 60)
        self.assertAlmostEqual(m["ugb_hz"], 1.2e8)
        self.assertAlmostEqual(m["i_opa_a"], 1.5e-3)


class TestConstraints(unittest.TestCase):
    def test_feasible_case(self):
        cons = Constraints()
        r = SimResult(dc_gain_db=80, ugb_hz=1e8, pm_deg=60, gm_db=-12,
                      i_opa_a=2e-3, ok=True)
        self.assertEqual(cons.check(r), [])
        self.assertTrue(cons.feasible(r))
        self.assertEqual(cons.violation(r), 0.0)

    def test_each_violation_reported(self):
        cons = Constraints()
        r = SimResult(dc_gain_db=35, ugb_hz=1e8, pm_deg=45, gm_db=-8,
                      i_opa_a=4e-3, ok=True)
        bad = " ".join(cons.check(r))
        for token in ("PM", "GM", "I_OPA", "DCGain"):
            self.assertIn(token, bad)
        self.assertGreater(cons.violation(r), 0)

    def test_failed_simulation_is_infeasible(self):
        cons = Constraints()
        self.assertFalse(cons.feasible(SimResult(ok=False, error="timeout")))


class TestFeasibilityFirstSelection(unittest.TestCase):
    """可行性优先：这是 30 分全有或全无项的安全阀。"""

    def test_optimizer_never_trades_away_feasibility(self):
        cons = Constraints()
        calls = {"n": 0}

        def evaluate(vec):
            calls["n"] += 1
            w = vec[0]
            # 大 W 目标更好，但 PM 会跌破 50 -> 必须被拒绝
            return SimResult(dc_gain_db=60 + w * 5,
                             ugb_hz=1e8, pm_deg=70 - w * 20,
                             gm_db=-13, i_opa_a=1e-3, area_um2=100 + w,
                             ok=True)

        space = ParamSpace(["w"], [0.5], [5.0], [False])
        budget = {"max_evaluations": 60, "pop_size": 6, "generations": 10,
                  "usable_seconds": 0, "per_eval_seconds": 0.0}
        result = differential_evolution(evaluate, space, [1.0], budget, cons=cons,
                                        seed=1)
        best = result["best_result"]
        self.assertLessEqual(best["pm_deg"], 70)     # 不会拿到 PM 崩掉的解
        self.assertGreaterEqual(best["pm_deg"], cons.pm_min - 1e-9)
        self.assertEqual(cons.check(_Res(best)), [])

    def test_mock_converges_and_improves(self):
        sim = MockSimulator()
        cons = Constraints()
        keys = ["M1_w", "M1_l", "M5_w", "M5_l", "M0_w", "CC1_value"]
        x0 = [10e-6, 1e-6, 15e-6, 1e-6, 5e-6, 3e-12]
        space = ParamSpace(keys, [2e-6, 0.13e-6, 5e-6, 0.13e-6, 1e-6, 0.5e-12],
                           [60e-6, 3e-6, 60e-6, 3e-6, 30e-6, 20e-12],
                           [False] * 6)
        budget = {"max_evaluations": 300, "pop_size": 10, "generations": 30,
                  "usable_seconds": 0, "per_eval_seconds": 0.0}
        # DE 只调用 evaluate(vec)，必须在这里绑定变量名
        evaluate = lambda vec: sim(list(vec), keys=keys)   # noqa: E731
        start = evaluate(x0)
        result = differential_evolution(evaluate, space, x0, budget,
                                        cons=cons, seed=3)
        end = _Res(result["best_result"])
        self.assertTrue(cons.feasible(end), cons.check(end))
        self.assertGreater(score(end, ObjectiveWeights()),
                           score(start, ObjectiveWeights()))

    def test_no_duplicate_target_evaluation(self):
        """目标个体不应被重复仿真——每次仿真都很贵。"""
        sim = MockSimulator()
        keys = ["M1_w"]
        space = ParamSpace(keys, [2e-6], [20e-6], [False])
        budget = {"max_evaluations": 40, "pop_size": 5, "generations": 8,
                  "usable_seconds": 0, "per_eval_seconds": 0.0}
        result = differential_evolution(sim, space, [5e-6], budget, seed=0)
        self.assertEqual(sim.n_evals, result["n_evals"])


class TestBudgetPlanning(unittest.TestCase):
    def test_budget_from_measured_eval_time(self):
        b = plan_budget(3 * 3600, per_eval_seconds=60, reserve=0.2)
        self.assertAlmostEqual(b["usable_seconds"], 3 * 3600 * 0.8)
        self.assertEqual(b["max_evaluations"], int(3 * 3600 * 0.8 // 60))
        self.assertGreaterEqual(b["pop_size"], 6)
        self.assertGreaterEqual(b["generations"], 1)

    def test_rejects_zero_eval_time(self):
        with self.assertRaises(ValueError):
            plan_budget(3600, per_eval_seconds=0)


class TestParetoAndDomination(unittest.TestCase):
    def test_pareto_archive_kept_for_feasible_only(self):
        cons = Constraints()

        def evaluate(vec):
            a = vec[0]
            return SimResult(dc_gain_db=60 + a * 10, ugb_hz=1e8,
                             pm_deg=60, gm_db=-13, i_opa_a=1e-3,
                             area_um2=100 / max(a, 0.1), ok=True)

        space = ParamSpace(["a"], [0.5], [3.0], [False])
        budget = {"max_evaluations": 40, "pop_size": 6, "generations": 6,
                  "usable_seconds": 0, "per_eval_seconds": 0.0}
        r = differential_evolution(evaluate, space, [1.0], budget, cons=cons, seed=2)
        self.assertTrue(r["pareto"])
        for e in r["pareto"]:
            self.assertTrue(e["result"]["ok"])


class TestCheckpointResume(ScratchCase):
    def test_resume_does_not_restart_from_scratch(self):
        ckpt = str(self.scratch("ckpt") / "ckpt.json")
        sim = MockSimulator()
        space = ParamSpace(["w"], [0.5e-6], [20e-6], [False])
        budget = {"max_evaluations": 30, "pop_size": 5, "generations": 6,
                  "usable_seconds": 0, "per_eval_seconds": 0.0}
        r1 = differential_evolution(sim, space, [5e-6], budget, seed=0,
                                    checkpoint_path=ckpt)
        self.assertTrue(Path(ckpt).exists())
        r2 = differential_evolution(sim, space, [5e-6], budget, seed=0,
                                    checkpoint_path=ckpt, resume=True)
        self.assertGreaterEqual(r2["n_evals"], r1["n_evals"])
        self.assertIsNotNone(r2["best_x"])


class TestEndToEnd(ScratchCase):
    """用 --mock 跑通完整 CLI：参数文件 -> 优化 -> 写回 -> 总结文件。"""

    def test_cli_mock_run(self):
        import optimization
        td = self.scratch("cli")
        pf = td / "param_init.txt"
        pf.write_text((DATA / "param_init.txt").read_text(encoding="utf-8"),
                      encoding="utf-8")
        out = td / "results"
        rc = optimization.main([
            "--param_file", str(pf),
            "--variables-csv", str(DATA / "variables.csv"),
            "--mock", "--max-evals", "60", "--pop-size", "6",
            "--output_path", str(out), "--output_file", "output.log",
        ])
        self.assertEqual(rc, 0)
        self.assertTrue((out / "optimization_result.json").exists())
        self.assertTrue((out / "output.log").exists())
        self.assertTrue((out / "best_param_file.txt").exists())
        self.assertTrue((out / "optimization_log.jsonl").exists())
        summary = json.loads((out / "optimization_result.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(summary["mode"], "mock")
        self.assertGreater(summary["n_evals"], 0)
        self.assertEqual(len(summary["best_x"]), len(summary["variables"]))
        # 备份了原始参数文件
        self.assertTrue((out / "param_init.txt.orig").exists())
        # 写回后的参数文件仍然可解析，且格式未被破坏
        pf2 = ParamFile.read(pf)
        self.assertEqual(pf2.keys()[0], "M0_w")
        self.assertIn("# extractcdfVal_0.txt 示意样例", pf2.text())
        # 变量数不能超过实际评估次数（预算被尊重）
        self.assertLessEqual(summary["n_evals"], 60)

    def test_only_free_variables_are_optimized(self):
        """联动变量（如 M2_w==M1_w）不能作为独立优化变量。"""
        import optimization
        pf = ParamFile.read(DATA / "param_init.txt")
        keys, space, notes, codec = optimization.resolve_space(
            pf, str(DATA / "variables.csv"), None)
        self.assertIn("M1_w", keys)
        self.assertNotIn("M2_w", keys)
        self.assertNotIn("M6_w", keys)


class _Res:
    def __init__(self, d):
        self.__dict__.update(d or {})
        self.ok = bool((d or {}).get("ok"))


if __name__ == "__main__":
    unittest.main()
