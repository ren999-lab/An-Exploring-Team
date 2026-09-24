"""第②③问 UI 入口的回归测试。

ui_logic.py 不 import streamlit，所以这些入口可以脱离浏览器直接测——
页面只是渲染层，真正的正确性在这里保证。

运行: python -m unittest discover -s tests -t . -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ui_logic import (analyze_netlist_text, build_search_space,  # noqa: E402
                      environment_report, run_optimization_demo,
                      variable_table)

SAMPLE = ROOT / "tests" / "sample_netlist.sp"


class TestAnalyzeNetlist(unittest.TestCase):
    def test_blank_input_is_rejected(self):
        res = analyze_netlist_text("   ")
        self.assertFalse(res["ok"])
        self.assertIn("为空", res["error"])

    def test_sample_netlist_is_analyzed(self):
        res = analyze_netlist_text(SAMPLE.read_text(encoding="utf-8"), "sample.sp")
        self.assertTrue(res["ok"], res["error"])
        r = res["result"]
        self.assertTrue(r["modules"])
        vr = r["variable_reduction"]
        self.assertGreater(vr["before"], 0)
        self.assertGreater(vr["after_free"], 0)
        self.assertLess(vr["after_free"], vr["before"])   # 必须真的约减掉了变量
        self.assertGreater(vr["reduction_ratio"], 0)

    def test_deliverable_texts_are_non_empty(self):
        res = analyze_netlist_text(SAMPLE.read_text(encoding="utf-8"), "sample.sp")
        self.assertIn("variable", res["csv_text"])        # variables.csv 有表头
        self.assertIn("M1_w", res["netlist_text"])        # 网表被变量化

    def test_invalid_netlist_does_not_crash(self):
        """坏输入要转成错误提示，不能把异常抛到页面。"""
        res = analyze_netlist_text("this is not a netlist at all")
        self.assertIn(res["ok"], (True, False))
        if not res["ok"]:
            self.assertTrue(res["error"])


class TestVariableTable(unittest.TestCase):
    def test_rows_cover_all_variables(self):
        res = analyze_netlist_text(SAMPLE.read_text(encoding="utf-8"), "sample.sp")
        rows = variable_table(res["result"])
        self.assertEqual(len(rows), len(res["result"]["variables"]))
        for row in rows:
            self.assertTrue(row["变量"])

    def test_free_variables_come_first(self):
        res = analyze_netlist_text(SAMPLE.read_text(encoding="utf-8"), "sample.sp")
        rows = variable_table(res["result"])
        flags = [r["约束"] == "free" for r in rows]
        self.assertEqual(flags, sorted(flags, reverse=True))


class TestSearchSpace(unittest.TestCase):
    def test_units_are_converted_to_si(self):
        """variables.csv 的 min/max 带 um 单位，必须换算到米。"""
        variables = [
            {"name": "M1_w", "type": "float", "unit": "um", "min": "0.13",
             "max": "20", "value": "2u", "constraint": "free"},
            {"name": "CC1_value", "type": "float", "unit": "F", "min": "",
             "max": "", "value": "1p", "constraint": "free"},
            {"name": "M1_m", "type": "int", "unit": "", "min": "1", "max": "20",
             "value": "1", "constraint": "free"},
            {"name": "M2_w", "type": "float", "unit": "um", "min": "0.13",
             "max": "20", "value": "2u", "constraint": "=M1_w"},
        ]
        names, lo, hi, is_int, x0 = build_search_space(variables)
        self.assertEqual(names, ["M1_w", "CC1_value", "M1_m"])   # 联动变量被排除
        self.assertAlmostEqual(lo[0], 0.13e-6)
        self.assertAlmostEqual(hi[0], 20e-6)
        self.assertAlmostEqual(x0[0], 2e-6)
        self.assertAlmostEqual(x0[1], 1e-12)                      # 1p -> 1e-12
        self.assertTrue(is_int[2])
        self.assertEqual(x0[2], 1)

    def test_bound_window_is_positive(self):
        variables = [{"name": "X", "type": "float", "unit": "", "value": "5"}]
        _, lo, hi, _, x0 = build_search_space(variables)
        self.assertGreater(lo[0], 0)
        self.assertGreater(hi[0], lo[0])
        self.assertAlmostEqual(x0[0], 5.0)

    def test_no_free_variables_returns_empty(self):
        self.assertEqual(build_search_space(
            [{"name": "A", "constraint": "=B"}])[0], [])


class TestOptimizationDemo(unittest.TestCase):
    def setUp(self):
        res = analyze_netlist_text(SAMPLE.read_text(encoding="utf-8"), "sample.sp")
        self.variables = res["result"]["variables"]

    def test_runs_and_is_feasible(self):
        out = run_optimization_demo(self.variables, generations=10, pop_size=8,
                                    budget_sec=20)
        self.assertTrue(out["ok"], out["error"])
        self.assertTrue(out["feasible"], f"最优解应可行: {out['best']}")
        best = out["best"]
        self.assertGreaterEqual(best["dc_gain_db"], 40.0)     # 赛题门槛
        self.assertGreaterEqual(best["pm_deg"], 50.0)
        self.assertLessEqual(best["gm_db"], -10.0)
        self.assertLessEqual(best["i_opa_a"], 3e-3)

    def test_eval_count_is_bounded_by_generations(self):
        """mock 仿真零耗时, 必须靠"代数 x 种群"封顶, 否则会空转到墙钟超时。"""
        out = run_optimization_demo(self.variables, generations=6, pop_size=8,
                                    budget_sec=30)
        self.assertLessEqual(out["n_evals"], 6 * 8 + 8)      # 含初始种群评估
        self.assertLessEqual(out["generations"], 6)

    def test_score_is_in_range_and_curve_populated(self):
        out = run_optimization_demo(self.variables, generations=8, pop_size=8,
                                    budget_sec=20)
        s = out["score"]
        self.assertLessEqual(s["total"], 80.0)
        self.assertGreater(s["total"], 0.0)
        self.assertEqual(s["constraint_score"], 30.0)        # 可行解约束项满分
        self.assertTrue(out["curve"])
        self.assertLessEqual(len(out["curve"]), 8)

    def test_no_free_variables_gives_friendly_error(self):
        out = run_optimization_demo([{"name": "A", "constraint": "=B"}])
        self.assertFalse(out["ok"])
        self.assertIn("自由变量", out["error"])


class TestEnvironmentReport(unittest.TestCase):
    def test_report_lists_items_with_status(self):
        report = environment_report(key_present=False)
        self.assertTrue(report)
        for item in report:
            self.assertIn(item["状态"], ("OK", "缺失"))
        names = [i["项目"] for i in report]
        self.assertIn("streamlit", names)
        self.assertIn("DASHSCOPE_API_KEY", names)
        self.assertIn("文件 optimization.py", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
