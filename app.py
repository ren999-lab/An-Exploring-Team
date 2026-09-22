"""Streamlit demonstration page: natural-language analog-circuit Spec -> JSON."""

from __future__ import annotations

import json
import os

import streamlit as st

from ui_logic import parse_for_ui

EXAMPLE = (
    "设计一个带有共模反馈的全差分运放，要求：DC增益不低于95dB，"
    "单位增益带宽至少60MHz，相位裕度大于55度，所有PVT下工作电流不超过3mA，"
    "并尽量减小面积。"
)

st.set_page_config(page_title="模拟电路 Spec 智能体", page_icon="⚡", layout="centered")
st.title("模拟电路 Spec 智能体")
st.caption("输入自然语言需求，输出可供后续拓扑识别与尺寸优化使用的 JSON。")

has_key = bool(os.environ.get("DASHSCOPE_API_KEY"))
mode = st.radio(
    "解析方式",
    options=["离线规则（推荐先试用）", "Qwen3.8-Max API"],
    index=0,
    disabled=not has_key,
    help="未配置 DASHSCOPE_API_KEY 时只能使用离线规则解析。",
)
if not has_key:
    st.info("当前未配置 API Key，正在使用离线规则解析。")

text = st.text_area("电路性能需求", value=EXAMPLE, height=170)

if st.button("生成 Spec JSON", type="primary", use_container_width=True):
    use_llm = has_key and mode == "Qwen3.8-Max API"
    with st.spinner("正在解析需求..."):
        try:
            spec, errors = parse_for_ui(text, use_llm=use_llm)
        except Exception as exc:
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
