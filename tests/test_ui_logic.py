"""Tests for the Streamlit Spec-parser demonstration adapter."""

import os
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

    def test_session_api_key_is_available_to_llm_call_then_removed(self):
        previous = os.environ.pop("DASHSCOPE_API_KEY", None)
        seen = {}

        def parser(text, *, use_llm):
            seen["key"] = os.environ.get("DASHSCOPE_API_KEY")
            seen["use_llm"] = use_llm
            return {
                "source": "llm+rule",
                "hard_constraints": [{"key": "PM", "op": ">=", "value": 50, "unit": "deg"}],
                "optimization_targets": [{"key": "Area", "direction": "min", "unit": "um^2", "priority": 1}],
                "units": {"PM": "deg", "Area": "um^2"},
                "priorities": {"Area": 1},
            }

        try:
            spec, errors = parse_for_ui(
                "相位裕度不低于50度，面积尽可能小。",
                use_llm=True,
                api_key="session-only-key",
                parser=parser,
            )
        finally:
            if previous is not None:
                os.environ["DASHSCOPE_API_KEY"] = previous

        self.assertEqual(errors, [])
        self.assertEqual(spec["source"], "llm+rule")
        self.assertEqual(seen, {"key": "session-only-key", "use_llm": True})
        self.assertNotIn("DASHSCOPE_API_KEY", os.environ)


if __name__ == "__main__":
    unittest.main()
