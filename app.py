"""Streamlit demonstration page: natural-language analog-circuit Spec -> JSON."""

from __future__ import annotations

import json

import streamlit as st

from ui_logic import parse_for_ui

EXAMPLE = (
    "设计一个带有共模反馈的全差分运放，要求：DC增益不低于95dB，"
    "单位增益带宽至少60MHz，相位裕度大于55度，所有PVT下工作电流不超过3mA，"
    "并尽量减小面积。"
)

st.set_page_config(page_title="模拟电路 Spec 智能体", page_icon="⚡", layout="centered")
st.title("模拟电路 Spec 智能体")
st.caption("输入自然语言需求，调用 Qwen3.8-Max，输出可供后续拓扑识别与尺寸优化使用的 JSON。")

api_key = st.text_input(
    "DashScope API Key",
    type="password",
    help="仅用于当前页面会话的本次调用；不会写入文件、日志或 Git。",
)
st.caption("密钥只在本页会话中临时使用。不要把密钥写入源码或提交到 Git。")

text = st.text_area("电路性能需求", value=EXAMPLE, height=170)

if st.button("生成 Spec JSON", type="primary", use_container_width=True):
    if not api_key.strip():
        st.error("请先输入 DashScope API Key。")
    else:
        with st.spinner("正在调用 Qwen3.8-Max 解析需求..."):
            try:
                spec, errors = parse_for_ui(text, use_llm=True, api_key=api_key.strip())
            except (OSError, RuntimeError, ValueError) as exc:
                st.error(f"解析失败：{exc}")
            else:
                if errors:
                    for error in errors:
                        st.error(error)
                else:
                    st.success("Spec 校验通过。")
                    st.json(spec)
                    st.download_button(
                        "下载 spec.json",
                        data=json.dumps(spec, ensure_ascii=False, indent=2),
                        file_name="spec.json",
                        mime="application/json",
                        use_container_width=True,
                    )

st.divider()
st.caption("竞赛链路：Spec JSON → 网表拓扑识别/参数约减 → 华大服务器 PVT 仿真与尺寸优化。")
