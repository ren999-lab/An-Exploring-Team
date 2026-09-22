"""第①问回归测试：自然语言 Spec 解析四要素。

运行:
    python -m unittest discover -s tests -t . -v

默认只测试**纯规则路径**（不需要 API Key）；设置环境变量
SPEC_TEST_LLM=1 时会额外跑一次真实 Qwen3.8-Max 调用做联调冒烟测试。
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent1_spec_parser.main import rule_fallback                # noqa: E402
from agent1_spec_parser.schema import (canon_key, canon_op,       # noqa: E402
                                       normalize_spec, validate_spec)

PDF_EXAMPLE = ("请设计一个带有共模反馈的全差分运放，要求：DC 增益不低于95dB，"
               "单位增益带宽至少60MHz，相位裕度大于55度，所有PVT 下工作电流"
               "不超过3mA，并尽量减小面积。")

PDF_TABLE_EXAMPLE = ("设计全差分运放，相位裕度≥60°，工作电流3mA，"
                     "增益尽可能大，单位增益带宽尽可能大，面积尽可能小")

ENGLISH = ("Design a fully differential OTA with DC gain >= 95 dB, "
           "UGB >= 60 MHz, phase margin > 55 deg, and supply current "
           "<= 3 mA over all PVT corners. Minimize area.")


def cons(spec, key):
    for c in spec["hard_constraints"]:
        if c["key"] == key:
            return c
    return None


def target(spec, key):
    for t in spec["optimization_targets"]:
        if t["key"] == key:
            return t
    return None


class TestFourElements(unittest.TestCase):
    """四要素必须永不为空（每缺一项扣 1 分）。"""

    TEXTS = [
        PDF_EXAMPLE,
        PDF_TABLE_EXAMPLE,
        ENGLISH,
        "设计全差分运放，增益不低于80dB，带宽至少50MHz，相位裕度大于60度。",
        "增益尽可能大",
        "帮我设计一个运放",
        "phase margin at least 60 degrees",
    ]

    def test_four_elements_always_present(self):
        for text in self.TEXTS:
            with self.subTest(text=text[:24]):
                spec = rule_fallback(text)
                self.assertTrue(spec["hard_constraints"], text)
                self.assertTrue(spec["optimization_targets"], text)
                self.assertTrue(spec["units"], text)
                self.assertTrue(spec["priorities"], text)
                self.assertEqual(validate_spec(spec), [], text)

    def test_every_constraint_and_target_has_unit(self):
        for text in self.TEXTS:
            spec = rule_fallback(text)
            for c in spec["hard_constraints"]:
                self.assertTrue(c.get("unit"), f"{text}: {c}")
            for t in spec["optimization_targets"]:
                self.assertTrue(t.get("unit"), f"{text}: {t}")
                self.assertIsInstance(t.get("priority"), int)


class TestMetricParsing(unittest.TestCase):
    def test_pdf_example_constraints(self):
        spec = rule_fallback(PDF_EXAMPLE)
        self.assertEqual(cons(spec, "DCGain")["value"], 95)
        self.assertEqual(cons(spec, "DCGain")["op"], ">=")
        self.assertEqual(cons(spec, "UGB")["value"], 60)
        self.assertEqual(cons(spec, "PM")["value"], 55)
        self.assertEqual(cons(spec, "I_OPA")["value"], 3)
        self.assertEqual(cons(spec, "I_OPA")["cond"], "所有PVT")
        self.assertIsNotNone(cons(spec, "CMFB"))

    def test_gm_is_parsed(self):
        """GM<=-10dB 是赛题 30 分项里单独 10 分的硬约束，原实现完全没有解析。"""
        spec = rule_fallback("增益裕度不大于-10dB，相位裕度不低于50度")
        gm = cons(spec, "GM")
        self.assertIsNotNone(gm)
        self.assertEqual(gm["op"], "<=")
        self.assertEqual(gm["value"], -10)
        self.assertEqual(gm["unit"], "dB")

    def test_pdf_table_example_targets(self):
        """"增益尽可能大，单位增益带宽尽可能大，面积尽可能小" 三个目标都要出来。"""
        spec = rule_fallback(PDF_TABLE_EXAMPLE, apply_competition_defaults=False)
        self.assertEqual(target(spec, "DCGain")["direction"], "max")
        self.assertEqual(target(spec, "UGB")["direction"], "max")
        self.assertEqual(target(spec, "Area")["direction"], "min")

    def test_max_phrase_forms(self):
        for text in ("增益尽可能大", "增益尽量大", "增益越大越好", "增益最大化"):
            with self.subTest(text=text):
                spec = rule_fallback(text, apply_competition_defaults=False)
                self.assertEqual(target(spec, "DCGain")["direction"], "max")

    def test_min_phrase_forms(self):
        for text in ("面积尽可能小", "面积尽量小", "面积越小越好", "面积最小化"):
            with self.subTest(text=text):
                spec = rule_fallback(text, apply_competition_defaults=False)
                self.assertEqual(target(spec, "Area")["direction"], "min")

    def test_english_input(self):
        spec = rule_fallback(ENGLISH)
        self.assertEqual(cons(spec, "DCGain")["value"], 95)
        self.assertEqual(cons(spec, "UGB")["value"], 60)
        self.assertEqual(cons(spec, "PM")["value"], 55)
        self.assertEqual(cons(spec, "I_OPA")["value"], 3)

    def test_unit_bandwidth_not_confused_with_gain(self):
        # 用 strict 关掉赛题默认约束补齐，才能验证"文本里没提到增益"
        spec = rule_fallback("单位增益带宽至少60MHz",
                             apply_competition_defaults=False)
        self.assertIsNotNone(cons(spec, "UGB"))
        self.assertIsNone(cons(spec, "DCGain"))

    def test_unit_conversion(self):
        spec = rule_fallback("单位增益带宽至少1GHz，工作电流不超过500uA")
        self.assertEqual(cons(spec, "UGB")["value"], 1000)   # 1GHz -> 1000MHz
        self.assertAlmostEqual(cons(spec, "I_OPA")["value"], 0.5)  # 500uA -> 0.5mA

    def test_unparsed_requirement_is_kept(self):
        """规则 4：识别到指标但没有数值/方向的要求不得丢弃。"""
        spec = rule_fallback("相位裕度要好", apply_competition_defaults=False)
        self.assertIsNotNone(cons(spec, "PM"))
        self.assertTrue(spec["unparsed_requirements"])


class TestCompetitionDefaults(unittest.TestCase):
    def test_defaults_fill_missing_keys(self):
        spec = rule_fallback("增益尽可能大")
        for key in ("PM", "GM", "I_OPA", "DCGain"):
            self.assertIsNotNone(cons(spec, key), key)
        self.assertTrue(spec["assumptions"])

    def test_strict_mode_adds_nothing(self):
        spec = rule_fallback("面积尽可能小", apply_competition_defaults=False)
        keys = {c["key"] for c in spec["hard_constraints"]}
        self.assertNotIn("GM", keys)
        self.assertEqual(keys, set())

    def test_explicit_value_not_overridden(self):
        spec = rule_fallback("相位裕度不低于65度")
        self.assertEqual(cons(spec, "PM")["value"], 65)
        self.assertEqual(cons(spec, "PM")["op"], ">=")


class TestNormalization(unittest.TestCase):
    """LLM 常返回自由表述，必须映射到规范指标名与单位。"""

    def test_chinese_key_and_unit_mapped(self):
        llm_like = {
            "hard_constraints": [{"key": "相位裕度", "op": "≥",
                                  "value": 55, "unit": "度"}],
            "optimization_targets": [
                {"key": "单位增益带宽", "direction": "max", "unit": "MHz"}],
            "units": {}, "priorities": {},
        }
        spec = normalize_spec(llm_like, raw_text="x")
        c = spec["hard_constraints"][0]
        self.assertEqual(c["key"], "PM")
        self.assertEqual(c["unit"], "deg")
        self.assertEqual(c["op"], ">=")
        self.assertEqual(spec["optimization_targets"][0]["key"], "UGB")

    def test_canonical_helpers(self):
        self.assertEqual(canon_key("增益"), "DCGain")
        self.assertEqual(canon_key("GM"), "GM")
        self.assertEqual(canon_op("≥"), ">=")
        self.assertEqual(canon_op("不超过"), "<=")


@unittest.skipUnless(os.environ.get("SPEC_TEST_LLM") == "1",
                     "设置 SPEC_TEST_LLM=1 才跑真实 LLM 联调")
class TestLiveLLM(unittest.TestCase):
    def test_live_llm_roundtrip(self):
        from agent1_spec_parser.main import parse_spec
        spec = parse_spec(PDF_EXAMPLE, use_llm=True)
        self.assertEqual(validate_spec(spec), [])
        self.assertIn("cross_check", spec)
        self.assertIsNotNone(cons(spec, "DCGain"))
        self.assertIsNotNone(cons(spec, "UGB"))


if __name__ == "__main__":
    unittest.main()
