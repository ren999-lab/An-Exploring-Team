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
from agent2_topology.topology_recognizer import (                  # noqa: E402
    device_role_map, recognize)

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

    def test_reduction_still_happens_across_scopes(self):
        """回归：曾因"带前缀名查表 / 不带前缀名返回"不一致，导致多作用域网表约减率为 0。"""
        linked = [v for v in self.variables if v["constraint"] != "free"]
        self.assertTrue(linked, "多作用域网表完全没有产生联动变量，约减失效")

    def test_mirror_and_pair_merged_in_their_scopes(self):
        v = {x["name"]: x for x in self.variables}
        # 电流镜从臂 MB2 初始 W 是 MB1 的 3 倍（2u -> 6u），比例必须保留
        self.assertEqual(v["bias.MB2_w"]["constraint"], "=bias.MB1_w*3")
        self.assertAlmostEqual(v["bias.MB2_w"]["ratio"], 3.0)
        # 差分对强制同尺寸
        self.assertEqual(v["amp.MA2_w"]["constraint"], "=amp.MA1_w")


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


class TestActiveLoadAndRoles(unittest.TestCase):
    """阶段 B 核心：多角色标注。

    原实现把器件当成互斥划分，PMOS 镜像一旦被判成"电流镜"就不再可能是"有源负载"，
    导致标准两级运放的模块列表里根本没有"有源负载"。
    """

    def setUp(self):
        self.nl = parse_netlist(ROOT / "tests" / "sample_netlist.sp")
        self.modules = recognize(self.nl)
        self.roles = device_role_map(self.modules)

    def types(self):
        return {m["module_type"] for m in self.modules}

    def test_active_load_is_identified(self):
        self.assertIn("有源负载", self.types())
        load = [m for m in self.modules if m["module_type"] == "有源负载"][0]
        self.assertEqual(sorted(load["devices"]), ["M3", "M4"])

    def test_mirror_and_active_load_share_devices(self):
        """同一批器件同时是电流镜和有源负载——这正是多角色标注的意义。"""
        self.assertIn("电流镜", self.roles["M3"])
        self.assertIn("有源负载", self.roles["M3"])
        self.assertIn("电流镜", self.roles["M4"])
        self.assertIn("有源负载", self.roles["M4"])

    def test_output_stage_identified(self):
        self.assertIn("输出级", self.types())
        stage = [m for m in self.modules if m["module_type"] == "输出级"][0]
        self.assertEqual(sorted(stage["devices"]), ["M5", "M6"])

    def test_matched_role_annotation(self):
        self.assertIn("匹配器件", self.roles["M5"])
        self.assertIn("匹配器件", self.roles["M1"])

    def test_matched_pair_still_merged_in_reduction(self):
        """多角色不能让匹配对丢掉变量合并（M6 必须联动 M5）。"""
        variables, _ = reduce_parameters(self.nl)
        v = {x["name"]: x for x in variables}
        self.assertEqual(v["M6_w"]["constraint"], "=M5_w")
        self.assertEqual(v["M6_m"]["constraint"], "=M5_m")


class TestCascode(unittest.TestCase):
    def test_folded_cascode_devices_identified(self):
        nl = load("folded_cascode.sp")
        modules = recognize(nl)
        cas = [m for m in modules if m["module_type"] == "共源共栅（cascode）"]
        self.assertTrue(cas, "折叠共源共栅未被识别")
        devs = {d for m in cas for d in m["devices"]}
        self.assertTrue({"M4", "M5", "M6", "M7"} <= devs, devs)

    def test_diff_pair_on_tail_is_not_cascode(self):
        """差分对叠在尾电流源上是尾电流源结构，不能误判成共源共栅。"""
        nl = load("folded_cascode.sp")
        modules = recognize(nl)
        for m in modules:
            if m["module_type"] == "共源共栅（cascode）":
                self.assertNotIn("M3", m["devices"])   # M3 是尾电流源

    def test_folded_cascode_has_active_load(self):
        nl = load("folded_cascode.sp")
        types = {m["module_type"] for m in recognize(nl)}
        self.assertIn("有源负载", types)
        self.assertIn("输入对管（差分对）", types)
        self.assertIn("尾电流源", types)
        self.assertIn("电流镜", types)


class TestMultipleDiffPairs(unittest.TestCase):
    def test_both_pairs_found(self):
        nl = load("two_diff_pairs.sp")
        modules = recognize(nl)
        pairs = [m for m in modules if m["module_type"] == "输入对管（差分对）"]
        self.assertEqual(len(pairs), 2, [m["devices"] for m in pairs])
        found = {frozenset(m["devices"]) for m in pairs}
        self.assertEqual(found, {frozenset({"M1", "M2"}), frozenset({"M3", "M4"})})

    def test_both_tails_found(self):
        nl = load("two_diff_pairs.sp")
        tails = [m for m in recognize(nl) if m["module_type"] == "尾电流源"]
        self.assertEqual(len(tails), 2)


class TestMoscapDummyCmfb(unittest.TestCase):
    def setUp(self):
        self.nl = load("moscap_cmfb.sp")
        self.modules = recognize(self.nl)
        self.by_type = {}
        for m in self.modules:
            self.by_type.setdefault(m["module_type"], []).append(m)

    def test_moscap_not_treated_as_dummy(self):
        self.assertIn("MOS电容", self.by_type)
        self.assertIn("MC1", self.by_type["MOS电容"][0]["devices"])
        dummies = {d for m in self.by_type.get("Dummy器件", []) for d in m["devices"]}
        self.assertNotIn("MC1", dummies)

    def test_named_dummy_still_dummy(self):
        dummies = {d for m in self.by_type.get("Dummy器件", []) for d in m["devices"]}
        self.assertIn("MDUM", dummies)

    def test_moscap_keeps_variables(self):
        """MOS 电容是晶体管，PyAether 会参数化它，不应被剔除出变量表。"""
        variables, _ = reduce_parameters(self.nl)
        self.assertIn("MC1_w", {v["name"] for v in variables})

    def test_cmfb_detected(self):
        self.assertIn("共模反馈（CMFB）", self.by_type)


class TestPassiveNetworks(unittest.TestCase):
    def test_compensation_network_separated_from_decoupling(self):
        nl = parse_netlist(ROOT / "tests" / "sample_netlist.sp")
        modules = recognize(nl)
        by_type = {}
        for m in modules:
            by_type.setdefault(m["module_type"], []).append(m)
        self.assertIn("补偿网络（密勒补偿）", by_type)
        comp = {d for m in by_type["补偿网络（密勒补偿）"] for d in m["devices"]}
        self.assertIn("CC1", comp)
        self.assertIn("RC1", comp)          # 调零电阻应与补偿电容同组
        self.assertIn("去耦电容", by_type)
        decap = {d for m in by_type["去耦电容"] for d in m["devices"]}
        self.assertIn("CDEC", decap)
        self.assertNotIn("CC1", decap)

    def test_three_terminal_cap_uses_first_two_nodes(self):
        """`CC1 outn comp1 vss 1p`：前两个节点是信号端，第三端是屏蔽/衬底。"""
        nl = parse_netlist(ROOT / "tests" / "sample_netlist.sp")
        comp = [m for m in recognize(nl)
                if m["module_type"] == "补偿网络（密勒补偿）"][0]
        self.assertIn("CC1", comp["devices"])


if __name__ == "__main__":
    unittest.main()
