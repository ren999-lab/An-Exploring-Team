"""工具链回归测试：报告生成器 + 变量表/参数文件转换器。

运行: python -m unittest discover -s tests -t . -v
"""

import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tools.convert_params as conv          # noqa: E402
import tools.make_report as rep              # noqa: E402
from q3_optimizer.paramfile import ParamFile  # noqa: E402

DATA = ROOT / "tests" / "data"
SCRATCH_ROOT = ROOT / "tests" / "_scratch"


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


class TestSvgCharts(unittest.TestCase):
    def test_bar_chart_is_valid_svg(self):
        p = rep.svg_bar_chart("t", [("a", 3), ("b", 7)], Path(".") / "_t_bar.svg")
        try:
            text = p.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("<svg"))
            self.assertTrue(text.rstrip().endswith("</svg>"))
            self.assertIn("b", text)
        finally:
            p.unlink(missing_ok=True)

    def test_scatter_handles_single_point(self):
        """只有一个点时 xmax==xmin，不能除零。"""
        p = rep.svg_scatter("t", [(1.0, 2.0, "only", True)], "x", "y",
                            Path(".") / "_t_pt.svg")
        try:
            self.assertIn("<svg", p.read_text(encoding="utf-8"))
        finally:
            p.unlink(missing_ok=True)

    def test_scatter_escapes_xml(self):
        p = rep.svg_scatter("t", [(1.0, 2.0, "<bad&>", False)], "x", "y",
                            Path(".") / "_t_esc.svg")
        try:
            text = p.read_text(encoding="utf-8")
            self.assertNotIn("<bad&>", text)
            self.assertIn("&lt;bad&amp;&gt;", text)
        finally:
            p.unlink(missing_ok=True)

    def test_empty_bar_chart_does_not_crash(self):
        p = rep.svg_bar_chart("t", [], Path(".") / "_t_empty.svg")
        try:
            self.assertIn("<svg", p.read_text(encoding="utf-8"))
        finally:
            p.unlink(missing_ok=True)


class TestCoTLayout(unittest.TestCase):
    def test_missing_dir_returns_empty(self):
        self.assertEqual(rep.scan_cot_logs(ROOT / "no_such_dir"), [])

    def test_parses_real_logs(self):
        logs = rep.scan_cot_logs(ROOT / "logs")
        for x in logs:
            self.assertIn("reasoning_len", x)
            self.assertGreaterEqual(x["reasoning_len"], 0)


class TestHelperFormatting(unittest.TestCase):
    def test_reduction_text(self):
        topo = {"variable_reduction": {"before": 33, "after_free": 15,
                                       "after_total": 27}}
        self.assertIn("33", rep._reduction_text(topo))
        self.assertIn("15", rep._reduction_text(topo))

    def test_pct_and_num(self):
        self.assertEqual(rep._pct(0.5534), "55%")
        self.assertEqual(rep._pct(None), "-")
        self.assertEqual(rep._num(None), "-")
        self.assertEqual(rep._num(1.5), "1.5")


class TestReportEndToEnd(ScratchCase):
    def test_report_generated_with_all_sections(self):
        out = self.scratch("rep") / "technical_report.md"
        rc = rep.main(["--out", str(out), "--run-local", "--no-tests",
                       "--opt", str(DATA / "param_init.txt")])
        self.assertEqual(rc, 0)
        self.assertTrue(out.exists())
        text = out.read_text(encoding="utf-8")
        for sec in ("# 模拟电路 AI 智能化设计", "## 2. 第①问", "## 3. 第②问",
                    "## 4. 第③问", "## 5. 第④问", "## 6. 测试报告",
                    "## 7. 附录"):
            self.assertIn(sec, text, sec)
        # 关键素材必须被带进报告
        self.assertIn("你是一位模拟集成电路设计专家", text)   # SystemPrompt
        self.assertIn("变量 → 角色", text.replace("器件 → 角色", "变量 → 角色"))
        self.assertIn("有源负载", text)
        self.assertIn("variables.csv", text)
        # 图表被生成并引用
        self.assertIn("figures/variable_reduction.svg", text)
        self.assertTrue((out.parent / "figures" / "variable_reduction.svg").exists())

    def test_report_without_opt_shows_placeholder(self):
        out = self.scratch("rep2") / "r.md"
        rc = rep.main(["--out", str(out), "--no-tests"])
        self.assertEqual(rc, 0)
        text = out.read_text(encoding="utf-8")
        self.assertIn("待服务器真实数据补充", text)


class TestConvertCheck(ScratchCase):
    """接口校验：这是阶段 2 最关键的风险卡口。"""

    def test_check_passes_when_keys_align(self):
        # 用 variables.csv 自己生成一个参数文件，键必然对齐
        td = self.scratch("chk_ok")
        pf = ParamFile.from_text(
            "M0_w=5u\nM0_l=1u\nM0_m=1\nM1_w=10u\nM1_l=1u\nM1_m=1\n"
            "M5_w=15u\nM5_l=1u\nM5_m=2\nCC1_value=3p\n")
        p = td / "param.txt"
        pf.write(p)
        rc = conv.main(["check", "--variables", str(DATA / "variables.csv"),
                        "--param-file", str(p)])
        self.assertEqual(rc, 0)

    def test_check_fails_and_lists_missing_keys(self):
        """PyAether 常见命名是 NM1_w，而我们的变量表是 M1_w —— 必须报出来。"""
        td = self.scratch("chk_bad")
        p = td / "param_pyaether.txt"
        p.write_text("NM1_w=10u\nNM1_l=1u\nNM1_m=1\nPM1_w=15u\n",
                     encoding="utf-8")
        rc = conv.main(["check", "--variables", str(DATA / "variables.csv"),
                        "--param-file", str(p)])
        self.assertEqual(rc, 1)

    def test_check_reports_non_numeric_values(self):
        td = self.scratch("chk_expr")
        p = td / "param_expr.txt"
        p.write_text("M0_w=5u\nM0_l=1u\nM0_m=1\nM1_w=W1\nM1_l=1u\nM1_m=1\n"
                     "M5_w=15u\nM5_l=1u\nM5_m=2\nCC1_value=3p\n",
                     encoding="utf-8")
        rc = conv.main(["check", "--variables", str(DATA / "variables.csv"),
                        "--param-file", str(p)])
        self.assertEqual(rc, 1)

    def test_normalization_accepts_case_and_separators(self):
        """键名大小写/点/横线差异不应误报为缺失。"""
        td = self.scratch("chk_norm")
        p = td / "param_norm.txt"
        p.write_text("m0_w=5u\nm0_l=1u\nm0_m=1\nM1.W=10u\nM1-L=1u\nm1_m=1\n"
                     "M5_w=15u\nM5_l=1u\nM5_m=2\nCC1_value=3p\n",
                     encoding="utf-8")
        rc = conv.main(["check", "--variables", str(DATA / "variables.csv"),
                        "--param-file", str(p)])
        self.assertEqual(rc, 0)


class TestConvertApplyExtract(ScratchCase):
    def test_apply_writes_values_preserving_format(self):
        td = self.scratch("apply")
        p = td / "param.txt"
        p.write_text((DATA / "param_init.txt").read_text(encoding="utf-8"),
                     encoding="utf-8")
        out = td / "candidate.txt"
        rc = conv.main(["apply", "--variables", str(DATA / "variables.csv"),
                        "--param-file", str(p), "--out", str(out)])
        self.assertEqual(rc, 0)
        text = out.read_text(encoding="utf-8")
        self.assertIn("# extractcdfVal_0.txt 示意样例", text)   # 注释仍在
        self.assertIn("M5_w=15u      # 输出级宽度", text)       # 行尾注释仍在

    def test_extract_roundtrips_numeric_keys(self):
        td = self.scratch("extract")
        out = td / "variables_from_param.csv"
        rc = conv.main(["extract", "--param-file", str(DATA / "param_init.txt"),
                        "--out", str(out)])
        self.assertEqual(rc, 0)
        text = out.read_text(encoding="utf-8")
        self.assertIn("variable,value,", text)
        self.assertIn("M1_w", text)
        self.assertIn("CC1_value", text)


if __name__ == "__main__":
    unittest.main()
