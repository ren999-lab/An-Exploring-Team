"""Tests for the Streamlit Spec-parser demonstration adapter."""

import unittest

from ui_logic import parse_for_ui


class TestParseForUi(unittest.TestCase):
    def test_offline_parse_returns_valid_spec_and_no_validation_errors(self):
        spec, errors = parse_for_ui(
            "设计全差分运放，相位裕度不低于60度，工作电流不超过3mA，面积尽可能小。",
            use_llm=False,
        )

        self.assertEqual(errors, [])
        self.assertEqual(spec["source"], "rule")
        self.assertTrue(spec["hard_constraints"])
        self.assertTrue(spec["optimization_targets"])

    def test_blank_input_is_rejected_without_calling_parser(self):
        spec, errors = parse_for_ui("   ", use_llm=False)

        self.assertIsNone(spec)
        self.assertEqual(errors, ["请输入一段电路性能需求。"])


if __name__ == "__main__":
    unittest.main()
