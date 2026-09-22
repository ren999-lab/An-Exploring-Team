"""全链路演示入口（demo.py）回归测试。

重点覆盖两类问题：
1. 演示不得改写仓库里的测试 fixture（曾把 tests/data/param_init.txt 覆盖成优化结果）；
2. 终端表格必须按东亚字符宽度对齐，中文不能错位、不能出现半截句子。
"""

import hashlib
import io
import contextlib
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import demo                                  # noqa: E402

DATA_PARAM = ROOT / "tests" / "data" / "param_init.txt"
SCRATCH_ROOT = ROOT / "tests" / "_scratch"


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class ScratchCase(unittest.TestCase):
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


class TestTerminalLayout(unittest.TestCase):
    def test_display_width_counts_cjk_as_two(self):
        self.assertEqual(demo._dw("abc"), 3)
        self.assertEqual(demo._dw("中文"), 4)
        self.assertEqual(demo._dw("中a"), 3)

    def test_pad_aligns_by_display_width(self):
        self.assertEqual(demo._dw(demo._pad("中文", 10)), 10)
        self.assertEqual(demo._dw(demo._pad("ab", 10)), 10)

    def test_truncation_never_splits_cjk(self):
        s = "这是一段很长的识别依据文字"
        out = demo._trunc(s, 10)
        self.assertLessEqual(demo._dw(out), 10)
        self.assertTrue(out.endswith("…"))
        # 未超宽时原样返回，不加省略号
        self.assertEqual(demo._trunc("短", 10), "短")

    def test_table_columns_are_aligned(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            demo.table(["指标", "值"], [["PM", "60"], ["增益裕度", "1"]])
        lines = [l for l in buf.getvalue().splitlines() if l.strip()]
        widths = {demo._dw(l) for l in lines}
        self.assertEqual(len(widths), 1, f"列宽未对齐: {widths}")


class TestDemoRun(ScratchCase):
    def test_demo_does_not_mutate_test_fixture(self):
        """回归：--run-opt 曾把 tests/data/param_init.txt 覆盖成优化结果。"""
        before = _sha(DATA_PARAM)
        td = self.scratch("demo_mut")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = demo.main([
                "设计全差分运放，PM≥60°，工作电流3mA",
                "--netlist", "tests/sample_netlist.sp",
                "--run-opt",
                "--spec-out", str(td / "spec.json"),
                "--topo-out", str(td / "topo"),
                "--opt-out", str(td / "opt"),
            ])
        self.assertEqual(rc, 0)
        self.assertEqual(_sha(DATA_PARAM), before,
                         "演示改写了仓库里的测试 fixture！")

    def test_demo_produces_all_deliverables(self):
        td = self.scratch("demo_out")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = demo.main([
                "设计全差分运放，相位裕度不低于60度，增益尽可能大",
                "--netlist", "tests/sample_netlist.sp",
                "--spec-out", str(td / "spec.json"),
                "--topo-out", str(td / "topo"),
            ])
        self.assertEqual(rc, 0)
        for rel in ("spec.json", "topo/topology_result.json",
                    "topo/variables.csv", "topo/netlist_reduced.sp"):
            self.assertTrue((td / rel).exists(), rel)
        out = buf.getvalue()
        # 三个阶段的渲染标题都要出现
        self.assertIn("第①问", out)
        self.assertIn("第②问", out)
        self.assertIn("有源负载", out)
        self.assertIn("多角色器件", out)

    def test_demo_without_opt_skips_section(self):
        td = self.scratch("demo_noopt")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = demo.main([
                "PM≥60°",
                "--netlist", "tests/sample_netlist.sp",
                "--spec-out", str(td / "spec.json"),
                "--topo-out", str(td / "topo"),
            ])
        self.assertEqual(rc, 0)
        self.assertNotIn("第③问", buf.getvalue())

    def test_demo_renders_scoring_self_assessment(self):
        td = self.scratch("demo_score")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = demo.main([
                "PM≥60°，工作电流3mA",
                "--netlist", "tests/sample_netlist.sp",
                "--run-opt",
                "--spec-out", str(td / "spec.json"),
                "--topo-out", str(td / "topo"),
                "--opt-out", str(td / "opt"),
            ])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("评分口径自评", out)
        self.assertIn("/ 30 分", out)
        self.assertIn("相对初始解", out)

    def test_missing_netlist_is_reported_not_crashed(self):
        td = self.scratch("demo_badnl")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            demo.main(["PM≥60°", "--netlist", "no/such/file.sp",
                       "--spec-out", str(td / "spec.json")])
        self.assertIn("跳过第②问", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
