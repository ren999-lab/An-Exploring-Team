"""Small, testable adapter shared by the Streamlit demonstration page."""

from __future__ import annotations

from agent1_spec_parser.main import parse_spec
from agent1_spec_parser.schema import validate_spec


def parse_for_ui(text: str, *, use_llm: bool) -> tuple[dict | None, list[str]]:
    """Parse user input and return a display-ready Spec plus validation errors."""
    if not text or not text.strip():
        return None, ["请输入一段电路性能需求。"]

    spec = parse_spec(text.strip(), use_llm=use_llm)
    return spec, validate_spec(spec)
