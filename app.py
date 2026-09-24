"""Streamlit 演示页面：覆盖第①②③问的本地完整链路。

页面只做渲染，业务逻辑都在 ui_logic.py（那里不依赖 streamlit，可单独测试）。

标签页：
  ① Spec 解析        —— 自然语言 -> JSON（Qwen3.8-Max 在线 / 规则离线两种模式）
  ② 拓扑识别与约减   —— 网表 -> 模块识别 + 参数约减 + variables.csv
  ③ 尺寸优化与评分   —— mock 仿真器驱动 DE，看收敛、Pareto 与赛题评分自评
  ④ 环境自检         —— 一键定位"为什么起不来/为什么解析失败"

密钥只存在页面会话里（st.session_state），不写文件、不进日志、不进 Git。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import streamlit as st

from ui_logic import (analyze_netlist_text, environment_report, format_api_error,
                      llm_failure_message, parse_for_ui, run_optimization_demo,
                      variable_table)

ROOT = Path(__file__).resolve().parent
EXAMPLE_SPEC = (
    "设计一个带有共模反馈的全差分运放，要求：DC增益不低于95dB，"
    "单位增益带宽至少60MHz，相位裕度大于55度，所有PVT下工作电流不超过3mA，"
    "并尽量减小面积。"
)

st.set_page_config(page_title="模拟电路 AI 设计智能体", page_icon="⚡", layout="wide")
st.title("模拟电路 AI 设计智能体")
st.caption("赛题二：自然语言 Spec → 拓扑识别/参数约减 → 尺寸优化。①②③问均可在本地跑通（③问为 mock 仿真演示）。")


# ---------------------------------------------------------------------------
# 侧边栏：密钥与模式
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("运行设置")
    api_key = st.text_input(
        "DashScope API Key",
        type="password",
        value=st.session_state.get("api_key", ""),
        help="只保存在当前浏览器会话的内存里，不写入文件、日志或 Git。",
    )
    if api_key:
        st.session_state["api_key"] = api_key
    offline = st.toggle(
        "离线模式（不用大模型）",
        value=st.session_state.get("offline", False),
        help="用规则解析器兜底，断网或没有 Key 时也能演示①②③问。",
    )
    st.session_state["offline"] = offline
    st.divider()
    st.caption("在线模式调用赛题指定的 Qwen3.8-Max；离线模式走本地规则解析。")


def _metric_rows(pairs):
    cols = st.columns(len(pairs))
    for col, (label, value) in zip(cols, pairs):
        col.metric(label, value)


tab1, tab2, tab3, tab4 = st.tabs(
    ["① Spec 解析", "② 拓扑识别与参数约减", "③ 尺寸优化与评分", "④ 环境自检"])


# ---------------------------------------------------------------------------
# ① Spec 解析
# ---------------------------------------------------------------------------
with tab1:
    st.subheader("自然语言需求 → 结构化 Spec")
    if offline:
        st.info("当前为离线模式：使用本地规则解析，结果可用于演示但不代表 Qwen 的真实输出。")
    else:
        st.info("当前为在线模式：调用 Qwen3.8-Max。思维模型首次响应可能需数十秒。")

    text = st.text_area("电路性能需求", value=EXAMPLE_SPEC, height=150,
                        key="spec_text")

    if st.button("生成 Spec JSON", type="primary", use_container_width=True):
        if not offline and not st.session_state.get("api_key", "").strip():
            st.error("请先在左侧填入 DashScope API Key，或打开离线模式。")
        else:
            t0 = time.time()
            with st.spinner("正在解析需求..."):
                try:
                    spec, errors = parse_for_ui(
                        text,
                        use_llm=not offline,
                        api_key=None if offline else st.session_state["api_key"].strip(),
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    st.error(format_api_error(exc))
                else:
                    elapsed = time.time() - t0
                    st.caption(f"耗时 {elapsed:.1f}s · 解析来源 `{spec.get('source')}`")
                    online_error = llm_failure_message(spec) if spec else None
                    if online_error:
                        st.error(online_error)
                        st.caption("已给出离线规则备份结果，但本次 Qwen 在线解析未成功。")
                        st.json(spec)
                    elif errors:
                        for error in errors:
                            st.error(error)
                    else:
                        st.success("Spec 校验通过（硬约束 / 优化目标 / 单位 / 优先级四要素齐全）。")
                        n_h = len(spec.get("hard_constraints", []))
                        n_o = len(spec.get("optimization_targets", []))
                        _metric_rows([("硬约束", n_h), ("优化目标", n_o),
                                      ("耗时(s)", f"{elapsed:.1f}")])
                        st.json(spec)
                        st.download_button(
                            "下载 spec.json",
                            data=json.dumps(spec, ensure_ascii=False, indent=2),
                            file_name="spec.json",
                            mime="application/json",
                            use_container_width=True,
                        )


# ---------------------------------------------------------------------------
# ② 拓扑识别与参数约减
# ---------------------------------------------------------------------------
with tab2:
    st.subheader("电路网表 → 模块识别 + 参数约减")
    source = st.radio("网表来源", ["内置示例", "粘贴文本", "上传文件"], horizontal=True)

    sample_path = ROOT / "tests" / "sample_netlist.sp"
    netlist_text, netlist_name = "", "pasted"
    if source == "内置示例":
        netlist_text = sample_path.read_text(encoding="utf-8")
        netlist_name = sample_path.name
        st.code(netlist_text, language="text")
    elif source == "粘贴文本":
        netlist_text = st.text_area("SPICE 网表", height=220, key="nl_text")
    else:
        up = st.file_uploader("选择 .sp/.cir/.txt 网表文件", type=["sp", "cir", "txt", "net"])
        if up is not None:
            netlist_text = up.getvalue().decode("utf-8", errors="ignore")
            netlist_name = up.name

    if st.button("识别拓扑并约减变量", type="primary", use_container_width=True):
        if not netlist_text.strip():
            st.error("请先提供网表内容。")
        else:
            with st.spinner("正在识别模块并约减参数..."):
                res = analyze_netlist_text(netlist_text, netlist_name)
            if not res["ok"]:
                st.error(f"分析失败：{res['error']}")
            else:
                r = res["result"]
                st.session_state["variables"] = r.get("variables", [])
                st.session_state["topology"] = r

                net = r.get("netlist", {})
                vr = r.get("variable_reduction", {})
                _metric_rows([
                    ("器件总数", net.get("total_devices", 0)),
                    ("识别模块", len(r.get("modules", []))),
                    ("变量(约减前)", vr.get("before", 0)),
                    ("自由变量(约减后)", vr.get("after_free", 0)),
                    ("约减率", f"{vr.get('reduction_ratio', 0):.0%}"),
                ])

                st.markdown("**模块识别结果**")
                st.dataframe(
                    [{"作用域": m.get("scope") or "顶层",
                      "模块": m.get("module_type", ""),
                      "角色": "+".join(m.get("roles") or [m.get("module_type", "")]),
                      "器件": ", ".join(m.get("devices", [])),
                      "识别依据": m.get("evidence", "")}
                     for m in r.get("modules", [])],
                    use_container_width=True, hide_index=True)

                st.markdown("**约减后的变量表**")
                st.dataframe(variable_table(r), use_container_width=True, hide_index=True)

                st.caption("联动变量（非独立）随参考变量按初始比例缩放，不进入搜索空间。")
                col1, col2 = st.columns(2)
                col1.download_button("下载 variables.csv", data=res["csv_text"],
                                     file_name="variables.csv", mime="text/csv",
                                     use_container_width=True)
                col2.download_button("下载 netlist_reduced.sp", data=res["netlist_text"],
                                     file_name="netlist_reduced.sp", mime="text/plain",
                                     use_container_width=True)
                with st.expander("查看变量化后的网表"):
                    st.code(res["netlist_text"], language="text")


# ---------------------------------------------------------------------------
# ③ 尺寸优化与评分
# ---------------------------------------------------------------------------
with tab3:
    st.subheader("多目标尺寸优化（本地 mock 仿真演示）")
    st.warning(
        "本页用 MockSimulator 驱动优化器，用于验证**工程链路**（约束处理、时间预算、"
        "Pareto、评分口径）；仿真数值无物理意义，真实结果必须走九天服务器 PyAether。",
        icon="⚠️",
    )

    variables = st.session_state.get("variables")
    if not variables:
        st.info("请先在「② 拓扑识别」跑一次网表，或用下方按钮直接载入内置示例。")
        if st.button("载入内置示例网表并重跑第②问"):
            sample = (ROOT / "tests" / "sample_netlist.sp").read_text(encoding="utf-8")
            res = analyze_netlist_text(sample, "sample_netlist.sp")
            if res["ok"]:
                st.session_state["variables"] = res["result"].get("variables", [])
                st.session_state["topology"] = res["result"]
                st.success("已载入内置示例，可开始优化。")
            else:
                st.error(res["error"])

    variables = st.session_state.get("variables")
    if variables:
        c1, c2, c3, c4 = st.columns(4)
        generations = c1.number_input("最大代数", min_value=1, max_value=200, value=15, step=1)
        pop_size = c2.number_input("种群规模", min_value=4, max_value=40, value=10, step=1)
        budget_sec = c3.number_input("时间预算(秒)", min_value=5, max_value=10800, value=45, step=5)
        seed = c4.number_input("随机种子", min_value=0, max_value=9999, value=0, step=1)

        if st.button("运行差分进化优化", type="primary", use_container_width=True):
            with st.spinner("正在优化（mock 仿真）..."):
                out = run_optimization_demo(
                    variables, generations=int(generations), pop_size=int(pop_size),
                    budget_sec=float(budget_sec), seed=int(seed))
            if not out["ok"]:
                st.error(out["error"])
            else:
                st.session_state["opt"] = out
                best = out["best"]
                _metric_rows([
                    ("仿真次数", out["n_evals"]),
                    ("代数", out["generations"]),
                    ("耗时(s)", out["elapsed_sec"]),
                    ("可行", "是" if out["feasible"] else "否"),
                ])
                st.markdown("**最优解指标**")
                st.dataframe([{
                    "DC Gain(dB)": round(best.get("dc_gain_db") or 0, 2),
                    "UGB(MHz)": round((best.get("ugb_hz") or 0) / 1e6, 3),
                    "PM(deg)": round(best.get("pm_deg") or 0, 2),
                    "GM(dB)": round(best.get("gm_db") or 0, 2),
                    "I_OPA(mA)": round((best.get("i_opa_a") or 0) * 1e3, 4),
                    "Area(um²)": round(best.get("area_um2") or 0, 2),
                    "违反度": round(out["violation"] or 0, 4),
                }], use_container_width=True, hide_index=True)

                st.markdown("**赛题评分自评（按 6.3 规则）**")
                s = out["score"]
                _metric_rows([
                    ("约束项 /30", s["constraint_score"]),
                    ("目标项 /40", s["objective_score"]),
                    ("时间项 /10", s["runtime_score"]),
                    ("总分 /80", s["total"]),
                ])
                st.caption("目标项以本次搜索到的各单项最优为基准（模拟冠军队成绩），用于横向比较候选方案。")

                if out["curve"]:
                    st.markdown("**收敛过程**")
                    st.line_chart(
                        [{"gen": c["gen"],
                          "UGB(MHz)": c["ugb_mhz"],
                          "Area(um²)": c["area_um2"],
                          "DCGain(dB)": c["dc_gain_db"]} for c in out["curve"]],
                        x="gen")

                if out["pareto"]:
                    st.markdown(f"**Pareto 前沿（{len(out['pareto'])} 个非支配解）**")
                    st.dataframe([{
                        "DC Gain(dB)": round(p.get("dc_gain_db") or 0, 2),
                        "UGB(MHz)": round((p.get("ugb_hz") or 0) / 1e6, 3),
                        "PM(deg)": round(p.get("pm_deg") or 0, 2),
                        "Area(um²)": round(p.get("area_um2") or 0, 2),
                    } for p in out["pareto"]], use_container_width=True, hide_index=True)

                best_txt = "\n".join(
                    f"{k}={v:.6e}" if isinstance(v, float) else f"{k}={v}"
                    for k, v in zip(out["names"], out["best_x"] or []))
                st.download_button("下载最优尺寸", data=best_txt,
                                   file_name="best_params.txt", mime="text/plain",
                                   use_container_width=True)


# ---------------------------------------------------------------------------
# ④ 环境自检
# ---------------------------------------------------------------------------
with tab4:
    st.subheader("环境自检")
    st.caption("页面起不来或解析失败时，先看这里。")
    st.dataframe(environment_report(key_present=bool(st.session_state.get("api_key"))),
                 use_container_width=True, hide_index=True)
    st.markdown(
        "**启动命令**\n\n"
        "```bash\n"
        "pip install -r requirements.txt\n"
        "streamlit run app.py\n"
        "```\n\n"
        "Windows 双击 `启动UI.bat` 亦可（会自动挑选可用的 Python）。"
    )

st.divider()
st.caption("竞赛链路：Spec JSON → 网表拓扑识别/参数约减 → 华大九天服务器 PVT 仿真与尺寸优化。")
