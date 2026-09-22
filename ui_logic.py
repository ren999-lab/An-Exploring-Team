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


def format_api_error(error: Exception) -> str:
    """Convert API errors to a short UI message without exposing request details."""
    detail = str(error)
    if "HTTP 401" in detail or "invalid_api_key" in detail:
        return "Qwen API Key 无效或已失效，请从百炼控制台重新复制有效 Key。"
    if "HTTP 403" in detail:
        return "当前账号没有 Qwen3.8-Max 的调用权限，请检查百炼模型授权。"
    return "Qwen 调用失败，请检查网络、模型权限和账户余额后重试。"


def llm_failure_message(spec: dict) -> str | None:
    """Return a safe message when Agent 1 had to fall back after an LLM error."""
    if spec.get("source") != "rule(LLM failed)":
        return None
    return format_api_error(RuntimeError(str(spec.get("llm_error", ""))))
