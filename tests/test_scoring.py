"""赛题评分口径复现的回归测试（面积公式 + 6.3 打分规则）。

不需要服务器、不需要真实仿真器。
运行: python -m unittest discover -s tests -t . -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from q3_optimizer.scoring import (AREA_FACTOR, compute_area,          # noqa: E402
                                  constraint_score, objective_score,
                                  rank_candidates, refs_from_results,
                                  runtime_score, total_score)
from q3_optimizer.simulator import SimResult                          # noqa: E402


def res(**kw):
    kw.setdefault("ok", True)
    return SimResult(**kw)


class TestAreaModel(unittest.TestCase):
    def test_transistor_formula(self):
        """晶体管：f*W*L*m*1.5（单位 um）。"""
        area, notes = compute_area({"M1": {"w": 2.0, "l": 0.5, "m": 2}})
        self.assertAlmostEqual(area, 2.0 * 0.5 * 2 * AREA_FACTOR["M"])
        self.assertEqual(notes, [])

    def test_resistor_and_capacitor_with_geometry(self):
        """有几何尺寸时：电阻 W*L*seg*2，电容 W*L*1。"""
        area, _ = compute_area({
            "R1": {"w": 1.0, "l": 10.0, "seg": 3},
            "C1": {"w": 5.0, "l": 4.0},
        })
        self.assertAlmostEqual(area, 1.0 * 10.0 * 3 * 2 + 5.0 * 4.0)

    def test_value_only_devices_are_estimated_and_flagged(self):
        """只有数值的电容/电阻按等效面积估算，且必须留下 notes 提示。"""
        area, notes = compute_area({"CC1": {"value": 1e-12}})   # 1pF
        self.assertGreater(area, 0.0)
        self.assertTrue(any("CC1" in n for n in notes))
        area_r, notes_r = compute_area({"RC1": {"value": 1000.0}})  # 1kohm
        self.assertGreater(area_r, 0.0)
        self.assertTrue(any("RC1" in n for n in notes_r))

    def test_missing_geometry_is_reported_not_silently_dropped(self):
        area, notes = compute_area({"M9": {"m": 1}})
        self.assertEqual(area, 0.0)
        self.assertTrue(any("M9" in n for n in notes))

    def test_multiple_devices_sum(self):
        area, _ = compute_area({
            "M1": {"w": 2.0, "l": 0.5, "m": 1},
            "M2": {"w": 2.0, "l": 0.5, "m": 1},
        })
        self.assertAlmostEqual(area, 2 * (2.0 * 0.5 * 1 * 1.5))

    def test_empty_input(self):
        self.assertEqual(compute_area({}), (0.0, []))


class TestConstraintScore(unittest.TestCase):
    def test_all_pass_is_full_marks(self):
        s, d = constraint_score(res(pm_deg=60, gm_db=-15, i_opa_a=2e-3))
        self.assertEqual(s, 30.0)
        self.assertTrue(all(v["passed"] for v in d.values()))

    def test_each_violation_costs_ten(self):
        s, _ = constraint_score(res(pm_deg=45, gm_db=-15, i_opa_a=2e-3))
        self.assertEqual(s, 20.0)
        s, _ = constraint_score(res(pm_deg=60, gm_db=-8, i_opa_a=2e-3))
        self.assertEqual(s, 20.0)
        s, _ = constraint_score(res(pm_deg=60, gm_db=-15, i_opa_a=5e-3))
        self.assertEqual(s, 20.0)
        s, _ = constraint_score(res(pm_deg=45, gm_db=-8, i_opa_a=5e-3))
        self.assertEqual(s, 0.0)

    def test_missing_metric_counts_as_violation(self):
        s, d = constraint_score(res(pm_deg=None, gm_db=-15, i_opa_a=2e-3))
        self.assertEqual(s, 20.0)
        self.assertFalse(d["PM"]["passed"])

    def test_any_bad_corner_kills_the_item(self):
        """赛题要求所有 PVT 均满足：任一 corner 不过，该项归零。"""
        main = res(pm_deg=60, gm_db=-15, i_opa_a=2e-3)
        corners = {"TT": {"pm_deg": 60, "gm_db": -15, "i_opa_a": 2e-3},
                   "SS_125C": {"pm_deg": 42, "gm_db": -15, "i_opa_a": 2e-3}}
        s, d = constraint_score(main, corners=corners)
        self.assertEqual(s, 20.0)
        self.assertFalse(d["PM"]["passed"])


class TestObjectiveScore(unittest.TestCase):
    refs = {"dc_gain_db": 100.0, "ugb_hz": 100e6, "area_um2": 100.0}

    def test_below_gain_floor_scores_zero(self):
        s, d = objective_score(res(dc_gain_db=35, ugb_hz=100e6), self.refs, area_um2=100.0)
        self.assertEqual(s, 0.0)
        self.assertIn("note", d["DCGain"])
        self.assertNotIn("UGB", d)      # 未过门槛时 UGB/Area 不计分

    def test_best_ever_scores_full(self):
        s, _ = objective_score(res(dc_gain_db=100, ugb_hz=100e6), self.refs, area_um2=100.0)
        self.assertAlmostEqual(s, 40.0)

    def test_proportional_scoring(self):
        s, d = objective_score(res(dc_gain_db=80, ugb_hz=60e6), self.refs, area_um2=100.0)
        self.assertAlmostEqual(d["DCGain"]["points"], 8.0)      # 80/100*10
        self.assertAlmostEqual(d["UGB"]["points"], 9.0)         # 60/100*15
        self.assertAlmostEqual(d["Area"]["points"], 15.0)       # A0/A1 = 1
        self.assertAlmostEqual(s, 32.0)

    def test_area_smaller_is_better(self):
        _, d_small = objective_score(res(dc_gain_db=100, ugb_hz=100e6), self.refs, area_um2=50.0)
        _, d_big = objective_score(res(dc_gain_db=100, ugb_hz=100e6), self.refs, area_um2=200.0)
        self.assertGreater(d_small["Area"]["points"], d_big["Area"]["points"])
        self.assertLessEqual(d_small["Area"]["points"], 15.0)   # 不超过满分

    def test_no_area_no_area_points(self):
        s, d = objective_score(res(dc_gain_db=100, ugb_hz=100e6), self.refs, area_um2=0)
        self.assertNotIn("Area", d)
        self.assertAlmostEqual(s, 25.0)


class TestRuntimeScore(unittest.TestCase):
    def test_fastest_gets_full(self):
        self.assertEqual(runtime_score(100, 100), 10.0)

    def test_scales_inversely_with_time(self):
        self.assertEqual(runtime_score(200, 100), 5.0)

    def test_penalty_per_unmet_constraint(self):
        self.assertEqual(runtime_score(100, 100, unmet_constraints=1), 5.0)
        self.assertEqual(runtime_score(100, 100, unmet_constraints=2), 0.0)
        self.assertEqual(runtime_score(100, 100, unmet_constraints=3), 0.0)

    def test_zero_time_does_not_crash(self):
        self.assertEqual(runtime_score(0, 100), 0.0)
        self.assertEqual(runtime_score(100, 0), 0.0)


class TestTotalAndRanking(unittest.TestCase):
    def test_total_within_bounds(self):
        refs = {"dc_gain_db": 100.0, "ugb_hz": 100e6, "area_um2": 100.0}
        sb = total_score(res(dc_gain_db=90, ugb_hz=80e6, pm_deg=55, gm_db=-12, i_opa_a=2.5e-3),
                         refs, elapsed_s=100, fastest_s=100, area_um2=120.0)
        self.assertLessEqual(sb.total, 80.0)
        self.assertGreater(sb.total, 0.0)
        self.assertEqual(sb.detail["unmet_constraints"], 0)
        for key in ("constraint_score", "objective_score", "runtime_score", "total"):
            self.assertIn(key, sb.as_dict())

    def test_refs_from_results(self):
        rs = [res(dc_gain_db=80, ugb_hz=50e6, area_um2=200.0),
              res(dc_gain_db=95, ugb_hz=70e6, area_um2=150.0)]
        refs = refs_from_results(rs)
        self.assertEqual(refs["dc_gain_db"], 95)
        self.assertEqual(refs["ugb_hz"], 70e6)
        self.assertEqual(refs["area_um2"], 150.0)

    def test_constraint_gate_only_applies_to_dc_gain(self):
        """重要细节：PM/GM/I_OPA 不满足**不封锁**目标项，只是各扣 10 分（+时间项扣 5）。

        赛题只在 6.3(3) 里写了"在满足 DCGain 大于 40dB 的前提下"计算 UGB/Area，
        并未要求 PM/GM/I_OPA 合格才计目标分。因此一个 PM 不合格但指标很好的方案，
        在纯分数上仍可能超过各方面平庸但全合格的方案——这正是优化器必须
        采用"可行性优先"、而不能只盯标量化分数的原因。
        """
        compliant = res(dc_gain_db=85, ugb_hz=60e6, pm_deg=60, gm_db=-14,
                        i_opa_a=2.5e-3, area_um2=200.0)
        pm_fail_but_fast = res(dc_gain_db=110, ugb_hz=120e6, pm_deg=40, gm_db=-14,
                               i_opa_a=2.5e-3, area_um2=100.0)
        refs = refs_from_results([compliant, pm_fail_but_fast])
        s_ok, d_ok = objective_score(compliant, refs, area_um2=200.0)
        s_fail, d_fail = objective_score(pm_fail_but_fast, refs, area_um2=100.0)
        self.assertGreater(s_fail, s_ok)          # 目标项照常计分，且更好的方案更高
        self.assertEqual(d_fail["DCGain"]["points"], 10.0)   # 目标项未受约束拖累
        c_ok, _ = constraint_score(compliant)
        c_fail, _ = constraint_score(pm_fail_but_fast)
        self.assertEqual(c_ok - c_fail, 10.0)     # 约束不合格的代价: 10 分

    def test_ranking_uses_runtime_penalty_to_reflect_compliance(self):
        """同指标下，约束全过者靠"约束 30 分 + 时间项不被扣 5 分"胜出（差 15 分）。"""
        compliant = res(dc_gain_db=95, ugb_hz=70e6, pm_deg=60, gm_db=-14,
                        i_opa_a=2.5e-3, area_um2=150.0)
        pm_fail = res(dc_gain_db=95, ugb_hz=70e6, pm_deg=40, gm_db=-14,
                      i_opa_a=2.5e-3, area_um2=150.0)
        ranked = rank_candidates([pm_fail, compliant],
                                 areas=[150.0, 150.0], elapsed=[100.0, 100.0])
        self.assertIs(ranked[0][1], compliant)
        best, worst = ranked[0][0], ranked[1][0]
        self.assertAlmostEqual(best.constraint_score - worst.constraint_score, 10.0)
        self.assertAlmostEqual(best.runtime_score - worst.runtime_score, 5.0)
        self.assertAlmostEqual(best.total - worst.total, 15.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
