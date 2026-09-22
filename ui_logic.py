"""Small, testable adapter shared by the Streamlit demonstration page."""

from __future__ import annotations

import os
from collections.abc import Callable

from agent1_spec_parser.main import parse_spec
from agent1_spec_parser.schema import validate_spec


def parse_for_ui(
    text: str,
    *,
    use_llm: bool,
    api_key: str | None = None,
    parser: Callable[..., dict] = parse_spec,
) -> tuple[dict | None, list[str]]:
    """Parse user input and return a display-ready Spec plus validation errors."""
    if not text or not text.strip():
        return None, ["请输入一段电路性能需求。"]

    previous_key = os.environ.get("DASHSCOPE_API_KEY")
    if api_key is not None:
        os.environ["DASHSCOPE_API_KEY"] = api_key
    try:
        spec = parser(text.strip(), use_llm=use_llm)
    finally:
        if api_key is not None:
            if previous_key is None:
                os.environ.pop("DASHSCOPE_API_KEY", None)
            else:
                os.environ["DASHSCOPE_API_KEY"] = previous_key
    return spec, validate_spec(spec)
