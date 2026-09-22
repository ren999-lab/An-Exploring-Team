"""第②问回归测试：拓扑模块识别与参数约减。

运行:
    python -m unittest discover -s tests -t . -v

每个用例都对应一个曾经真实存在的缺陷（见 tests/netlists/*.sp 里的"回归目标"注释）。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent2_topology.netlist_parser import parse_netlist          # noqa: E402
from agent2_topology.param_reducer import (                        # noqa: E402
    CSV_FIELDS, count_raw_variables, export_reduced_netlist,
    export_variables_csv, reduce_parameters)
from agent2_topology.topology_recognizer import recognize          # noqa: E402

NETLISTS = ROOT / "tests" / "netlists"


def load(name):
    return parse_netlist(NETLISTS / name)


def reduced_text(nl, variables):
    out = ROOT / "tests" / "_tmp_reduced.sp"
    export_reduced_netlist(nl, variables, out)
    text = out.read_text(encoding="utf-8")
    out.unlink()
    return text


def run_case(name):
    nl = load(name)
    variables, modules = reduce_parameters(nl)
    return nl, variables, modules, reduced_text(nl, variables)


def by_name(variables):
    return {v["name"]: v for v in variables}


class TestDeviceClassification(unittest.TestCase):
    """器件类型不能只看首字母（原实现 NM1/PM1 整张网表丢光）。"""

    def test_nm_pm_names_are_parsed_as_mos(self):
        nl = load("ota_nm_pm.sp")
        self.assertEqual(len(nl.devices), 7)      # 6 个 MOS + 1 个补偿电容
        mos = [d for d in nl.devices if d.dtype == "M"]
        self.assertEqual(len(mos), 6)
        names = {d.name: d for d in mos}
        self.assertEqual(names["NM1"].polarity, "n")
        self.assertEqual(names["PM1"].polarity, "p")

    def test_x_prefix_with_model_is_mos(self):
        nl = parse_netlist(
            "XM1 n1 inp nt vss nch w=2u l=1u\n"
            "XM2 n2 inn nt vss nch w=2u l=1u\n"
            "XM3 nt nb vss vss nch w=4u l=1u\n")
        self.assertEqual(len([d for d in nl.devices if d.dtype == "M"]), 3)

    def test_lowercase_names(self):
        nl = parse_netlist(
            "m1 d1 a t vss nmos w=2u l=1u\n"
            "m2 d2 b t vss nmos w=2u l=1u\n"
            "m3 t nb vss vss nmos w=4u l=1u\n")
        self.assertEqual(len([d for d in nl.devices if d.dtype == "M"]), 3)

    def test_single_letter_model(self):
        nl = parse_netlist("N1 d g s b n w=1u l=1u\n")
        self.assertEqual(nl.devices[0].dtype, "M")

    def test_source_devices_excluded(self):
        nl = parse_netlist(
            "V1 vdd 0 1.2\nI1 vdd n1 10u\n"
            "M1 d1 a t vss nmos w=2u l=1u\n")
        self.assertEqual(len(nl.devices), 1)
        self.assertEqual([d.dtype for d in nl.others], ["V", "I"])


class TestSubcktScoping(unittest.TestCase):
    """不能跨 .subckt 把器件缝合（原实现会生成错误约束 MA3_w=MB1_w）。"""

    def setUp(self):
        self.nl, self.variables, self.modules, _ = run_case("hier_bias_amp.sp")

    def test_two_scopes(self):
        self.assertEqual(len(self.nl.analysis_scopes()), 2)

    def test_no_module_mixes_scopes(self):
        for m in self.modules:
            scopes = {self.nl.devices[i].subckt
                      for i, d in enumerate(self.nl.devices)
                      if d.name in m["devices"]}
            self.assertLessEqual(
                len(scopes), 1,
                f"模块 {m['module_type']} 跨作用域: {m['devices']}")

    def test_bias_and_amp_not_merged(self):
        names = {d.name for d in self.nl.devices if d.subckt == "bias"}
        amp = {d.name for d in self.nl.devices if d.subckt == "amp"}
        for m in self.modules:
            self.assertFalse(set(m["devices"]) & names and
                             set(m["devices"]) & amp,
                             f"模块混用了两个 subckt: {m['devices']}")

    def test_variables_are_scope_qualified(self):
        vnames = {v["name"] for v in self.variables}
        self.assertIn("bias.MB1_w", vnames)
        self.assertIn("amp.MA1_w", vnames)


class TestMirrorRatio(unittest.TestCase):
    """镜像比必须原样保留（原实现按 W 表达比例时会被静默改成 1:1）。"""

    def setUp(self):
        self.nl, self.variables, _, self.text = run_case("mirror_ratio.sp")
        self.vars = by_name(self.variables)

    def test_width_ratio_preserved(self):
        self.assertEqual(self.vars["M5_w"]["constraint"], "=M4_w*3")
        self.assertAlmostEqual(self.vars["M5_w"]["ratio"], 3.0)

    def test_m_ratio_preserved(self):
        self.assertEqual(self.vars["M7_m"]["constraint"], "=M6_m*4")

    def test_linked_int_bound_tightened(self):
        # M7_m = M6_m*4 且 PDK m<=20 -> M6_m 实际只能取到 5
        self.assertEqual(self.vars["M6_m"]["derived_max"], 5)

    def test_no_illegal_variable_name(self):
        # 只看网表正文：注释里会引用这个错误写法作为回归目标说明
        body = "\n".join(l for l in self.text.splitlines()
                         if not l.strip().startswith("*"))
        self.assertNotIn("*3_w", body)
        self.assertNotIn("*4_m}", body)
        self.assertIn(".param M5_w='M4_w*3'", body)
        self.assertIn(".param M7_m='M6_m*4'", body)

    def test_all_braces_resolve_to_declared_params(self):
        declared = {p.split("=")[0].strip()
                    for line in self.text.splitlines()
                    if line.startswith(".param ")
                    for p in line[len(".param "):].split()}
        used = set()
        for line in self.text.splitlines():
            if line.startswith("*") or line.startswith("."):
                continue
            for tok in line.replace("{", " {").replace("}", "} ").split():
                if tok.startswith("{") and tok.endswith("}"):
                    used.add(tok[1:-1])
        self.assertTrue(used, "约减网表里应当出现变量引用")
        self.assertTrue(used <= declared,
                        f"未声明的变量: {sorted(used - declared)}")


class TestPassiveDevices(unittest.TestCase):
    """R/C 的取值与多端节点不能丢（原实现两者都丢）。"""

    def setUp(self):
        self.nl = load("passives.sp")
        self.variables, _ = reduce_parameters(self.nl)
        self.devices = {d.name: d for d in self.nl.devices}
        self.vars = by_name(self.variables)

    def test_resistor_value_preserved(self):
        self.assertEqual(self.devices["R1"].value, "5k")
        self.assertAlmostEqual(self.devices["R1"].value_si, 5000.0)

    def test_three_terminal_cap_nodes_and_value(self):
        self.assertEqual(self.devices["CC1"].nodes, ["d1", "d2", "vss"])
        self.assertEqual(self.devices["CC1"].value, "1p")

    def test_value_only_device_gets_value_variable(self):
        self.assertIn("R1_value", self.vars)
        self.assertEqual(self.vars["R1_value"]["value"], "5k")
        self.assertEqual(self.vars["R1_value"]["unit"], "ohm")

    def test_geometry_device_gets_pdk_variables(self):
        for name in ("R2_w", "R2_l", "R2_seg", "C2_w", "C2_l"):
            self.assertIn(name, self.vars)
        self.assertEqual(self.vars["R2_seg"]["type"], "int")

    def test_mos_always_has_multiplier_variable(self):
        self.assertIn("M1_m", self.vars)
        self.assertEqual(self.vars["M1_m"]["value"], "1")


class TestDeliverables(unittest.TestCase):
    """三个交付物本身的结构要求。"""

    def setUp(self):
        self.nl = parse_netlist(ROOT / "tests" / "sample_netlist.sp")
        self.variables, self.modules = reduce_parameters(self.nl)
        self.text = reduced_text(self.nl, self.variables)

    def test_dummy_has_no_variables(self):
        dummy = [d.name for d in self.nl.devices if "DUM" in d.name.upper()]
        self.assertTrue(dummy)
        for name in dummy:
            self.assertFalse([v for v in self.variables if v["device"] == name])

    def test_comments_are_preserved(self):
        self.assertIn("*--- 尾电流源 ---", self.text)

    def test_param_block_present(self):
        self.assertIn(".param M7_w=", self.text)

    def test_module_types_cover_expected_set(self):
        types = {m["module_type"] for m in self.modules}
        self.assertIn("电流镜", types)
        self.assertIn("输入对管（差分对）", types)
        self.assertIn("尾电流源", types)
        self.assertIn("Dummy器件", types)
        self.assertIn("匹配器件", types)

    def test_passives_are_not_silently_dropped(self):
        covered = {d for m in self.modules for d in m["devices"]}
        for d in self.nl.devices:
            self.assertIn(d.name, covered, f"{d.name} 未被任何模块覆盖")

    def test_variable_count_reduces(self):
        before = count_raw_variables(self.nl)
        free = sum(1 for v in self.variables if v["constraint"] == "free")
        self.assertLess(free, before)

    def test_csv_columns_and_rows(self):
        out = ROOT / "tests" / "_tmp_variables.csv"
        export_variables_csv(self.variables, out)
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        out.unlink()
        self.assertEqual(lines[0].split(","), CSV_FIELDS)
        self.assertEqual(len(lines), len(self.variables) + 1)
        first = lines[1].split(",")
        self.assertEqual(first[0], self.variables[0]["name"])
        self.assertTrue(first[1], "value 列（初值）不能为空")


if __name__ == "__main__":
    unittest.main()
