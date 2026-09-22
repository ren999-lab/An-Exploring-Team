#!/usr/bin/env python
"""技术报告生成器：从第①②③问的实际产物自动生成 Markdown 报告与 SVG 图表。

为什么是"生成器"而不是"手写文档"
--------------------------------
赛题要求报告必须包含 SystemPrompt、推理链日志、模块识别依据、变量约减理由、
优化结果对比等"实际跑出来的东西"。手写一份静态文档，等真实数据（华大九天服务器上的
Public 电路、真实仿真结果）回来就必须重写一遍，而且很容易出现"报告里的数字和产物对不上"。

所以这里做成：**产物 -> 报告**。真实数据到位后重跑一条命令即可刷新，
报告里的每个数字都直接来自产物文件，不会失真。

用法:
    python tools/make_report.py --run-local          # 先本地重跑 ①②问再生成
    python tools/make_report.py --opt results_case1/optimization_result.json
    python tools/make_report.py --out docs/technical_report.md

依赖：只用标准库（图表是手写 SVG，不需要 matplotlib）。
"""

import argparse
import csv
import html
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SKIP = "> _(待服务器真实数据补充)_"


# ---------------------------------------------------------------------------
# 极简 SVG 图表（零依赖）
# ---------------------------------------------------------------------------
def _esc(s):
    return html.escape(str(s), quote=True)


def svg_bar_chart(title, items, path, width=680, height=340, color="#2f6fb0"):
    """items = [(label, value), ...] 的横向条形图。"""
    items = [(str(k), float(v)) for k, v in items]
    left, top, right, bottom = 130, 46, 30, 40
    plot_w = width - left - right
    plot_h = height - top - bottom
    vmax = max([v for _, v in items] + [1.0])
    n = max(len(items), 1)
    bar_h = max(6.0, plot_h / n * 0.62)
    gap = plot_h / n
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{width/2}" y="24" text-anchor="middle" font-size="15" '
        f'font-family="sans-serif" fill="#222">{_esc(title)}</text>',
    ]
    for i, (label, val) in enumerate(items):
        y = top + gap * i + (gap - bar_h) / 2
        w = max(1.0, plot_w * val / vmax)
        parts.append(
            f'<rect x="{left}" y="{y:.1f}" width="{w:.1f}" height="{bar_h:.1f}" '
            f'fill="{color}" rx="2"/>')
        parts.append(
            f'<text x="{left-8}" y="{y+bar_h*0.75:.1f}" text-anchor="end" '
            f'font-size="12" font-family="sans-serif" fill="#333">'
            f'{_esc(label)}</text>')
        parts.append(
            f'<text x="{left+w+6:.1f}" y="{y+bar_h*0.75:.1f}" font-size="12" '
            f'font-family="sans-serif" fill="#333">{val:g}</text>')
    parts.append(
        f'<line x1="{left}" y1="{top+plot_h}" x2="{left+plot_w}" '
        f'y2="{top+plot_h}" stroke="#bbb"/>')
    parts.append("</svg>")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def svg_scatter(title, points, xlabel, ylabel, path,
                width=680, height=400, color="#2f6fb0", best_color="#c0392b"):
    """points = [(x, y, label, is_best), ...] 的散点图（用于 Pareto 权衡曲线）。"""
    pts = [(float(x), float(y), str(l), bool(b)) for x, y, l, b in points]
    left, top, right, bottom = 78, 46, 30, 56
    plot_w, plot_h = width - left - right, height - top - bottom
    xs = [p[0] for p in pts] or [0, 1]
    ys = [p[1] for p in pts] or [0, 1]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if xmax == xmin:
        xmax = xmin + 1
    if ymax == ymin:
        ymax = ymin + 1
    padx, pady = (xmax - xmin) * 0.08, (ymax - ymin) * 0.08
    xmin, xmax = xmin - padx, xmax + padx
    ymin, ymax = ymin - pady, ymax + pady

    def sx(v):
        return left + plot_w * (v - xmin) / (xmax - xmin)

    def sy(v):
        return top + plot_h * (1 - (v - ymin) / (ymax - ymin))

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{width/2}" y="24" text-anchor="middle" font-size="15" '
        f'font-family="sans-serif" fill="#222">{_esc(title)}</text>',
        f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" '
        f'fill="none" stroke="#bbb"/>',
        f'<text x="{left+plot_w/2}" y="{height-16}" text-anchor="middle" '
        f'font-size="12" font-family="sans-serif" fill="#333">{_esc(xlabel)}</text>',
        f'<text x="18" y="{top+plot_h/2}" text-anchor="middle" font-size="12" '
        f'font-family="sans-serif" fill="#333" '
        f'transform="rotate(-90 18 {top+plot_h/2})">{_esc(ylabel)}</text>',
    ]
    for x, y, label, is_best in pts:
        c = best_color if is_best else color
        r = 6 if is_best else 4
        parts.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="{r}" '
                     f'fill="{c}" fill-opacity="0.85"/>')
        parts.append(f'<text x="{sx(x)+9:.1f}" y="{sy(y)+4:.1f}" font-size="11" '
                     f'font-family="sans-serif" fill="#555">{_esc(label)}</text>')
    parts.append(f'<text x="{left}" y="{top+plot_h+16}" font-size="11" '
                 f'font-family="sans-serif" fill="#777">{xmin:.4g}</text>')
    parts.append(f'<text x="{left+plot_w}" y="{top+plot_h+16}" text-anchor="end" '
                 f'font-size="11" font-family="sans-serif" fill="#777">'
                 f'{xmax:.4g}</text>')
    parts.append("</svg>")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 产物加载
# ---------------------------------------------------------------------------
def load_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[report] 无法解析 {p}: {e}", file=sys.stderr)
        return default


def load_csv_rows(path):
    p = Path(path)
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def scan_cot_logs(log_dir):
    """汇总 logs/ 下的 LLM 推理链日志（技术报告要求的 CoT 素材）。"""
    log_dir = Path(log_dir)
    out = []
    if not log_dir.exists():
        return out
    for p in sorted(log_dir.glob("llm_*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "file": p.name,
            "tag": d.get("tag", ""),
            "model": d.get("model", ""),
            "transport": d.get("transport", ""),
            "elapsed": d.get("elapsed_sec"),
            "reasoning_len": len(d.get("reasoning_content") or ""),
            "response_len": len(d.get("response") or ""),
            "reasoning": (d.get("reasoning_content") or "")[:300],
            "time": d.get("time", ""),
            "usage": d.get("usage"),
        })
    return out


def _run(cmd, cwd=ROOT):
    """统一的子进程调用。

    中文 Windows 上 `subprocess` 默认用本地编码（GBK）解码子进程输出，
    而子进程按 UTF-8 输出中文，会导致读取线程抛 UnicodeDecodeError。
    这里显式指定 UTF-8 并容错。
    """
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def run_local_agents():
    """本地重跑 ①②问（第①问走纯规则，不需要 API Key）。"""
    cmds = [
        [sys.executable, "-m", "agent1_spec_parser.main", "--no-llm"],
        [sys.executable, "-m", "agent2_topology.main", "tests/sample_netlist.sp"],
    ]
    for c in cmds:
        print(f"[report] 运行: {' '.join(c[1:])}")
        r = _run(c)
        if r.returncode != 0:
            print((r.stdout or "")[-2000:])
            print((r.stderr or "")[-2000:], file=sys.stderr)
            raise SystemExit(f"命令失败: {' '.join(c)}")


# ---------------------------------------------------------------------------
# 报告各章节
# ---------------------------------------------------------------------------
def sec_header(ctx):
    return f"""# 模拟电路 AI 智能化设计 —— 技术报告

> 赛题二 · 2026"中国电子杯"高校ICT产教融合创新大赛
> 命题单位：北京华大九天科技股份有限公司

| 项目 | 内容 |
|---|---|
| 报告生成时间 | {datetime.now().strftime('%Y-%m-%d %H:%M')} |
| Spec 解析来源 | `{ctx['spec'].get('source', '-') if ctx['spec'] else '-'}` |
| 识别到的模块数 | {len(ctx['topology'].get('modules', [])) if ctx['topology'] else '-'} |
| 变量约减 | {_reduction_text(ctx['topology'])} |
| 回归测试 | {ctx['test_summary']} |
"""


def _reduction_text(topology):
    if not topology:
        return "-"
    vr = topology.get("variable_reduction", {})
    return (f"{vr.get('before', '-')} → 自由变量 {vr.get('after_free', '-')} 个"
            f"（含联动 {vr.get('after_total', '-')} 个）")


def sec_architecture(ctx):
    return """
## 1. 作品概述与系统架构

本作品实现赛题二的完整链路，覆盖"自然语言 → 结构化 Spec → 拓扑识别与参数约减 →
多目标尺寸优化 → 自动化流程与交付物"五个环节。

```
自然语言 Spec ──► ①Spec解析 ──► ②拓扑识别与参数约减 ──► ③多目标尺寸优化 ──► ④自动化链路
                  agent1        agent2                    q3_optimizer         tools/
                  规则主干      多角色标注                 可行性优先DE         打包+报告
                  +LLM增强     +PDK变量模型
```

**核心设计取舍：** 规则解析是主干、LLM 是增强层（而非相反），
因此系统在没有 API Key 时也能完整跑通第①②问；
LLM 与规则的结果做交叉校验，差异作为报告证据。

**核心技术决策：**

| 决策 | 理由 |
|---|---|
| 拓扑识别采用多角色标注而非互斥划分 | 一个 PMOS 镜像同时就是有源负载，互斥划分会让"有源负载"整项消失 |
| 参数约减两遍处理（依赖型模块优先） | 多角色下同一器件属于多个模块，顺序错了会让匹配对丢掉变量合并 |
| 优化采用可行性优先而非加权惩罚 | PM/GM/I_OPA 是"全有或全无"的 30 分，宁可少赚目标分也不能让约束崩 |
| 参数文件读写保真优先 | 真实格式未知，原样回写比"解析成漂亮结构"更安全 |
"""


def sec_spec(ctx):
    spec = ctx["spec"]
    lines = ["\n## 2. 第①问：自然语言 Spec 解析\n"]
    lines.append("### 2.1 SystemPrompt（赛题要求附上）\n")
    lines.append("```text\n" + (ctx["system_prompt"] or "").strip() + "\n```\n")
    if ctx.get("few_shot"):
        lines.append("### 2.2 Few-shot 示例\n")
        lines.append("```text\n" + ctx["few_shot"].strip() + "\n```\n")

    lines.append("### 2.3 解析结果（四要素）\n")
    if not spec:
        lines.append(SKIP + "\n")
        return "\n".join(lines)

    lines.append(f"原始输入：\n\n> {spec.get('raw_text', '')}\n")
    lines.append("**硬约束项**\n")
    lines.append("| 指标 | 比较 | 数值 | 单位 | 条件 | 来源 |")
    lines.append("|---|---|---|---|---|---|")
    for c in spec.get("hard_constraints", []):
        src = "赛题默认补齐" if c.get("assumed") else (
            "比较词推断" if c.get("op_inferred") else "原文")
        lines.append(f"| {c.get('key')} | {c.get('op') or '-'} | "
                     f"{_num(c.get('value'))} | {c.get('unit') or '-'} | "
                     f"{c.get('cond') or '-'} | {src} |")

    lines.append("\n**优化目标与优先级**\n")
    lines.append("| 优先级 | 指标 | 方向 | 单位 | 来源 |")
    lines.append("|---|---|---|---|---|")
    for t in spec.get("optimization_targets", []):
        src = "赛题默认补齐" if t.get("assumed") else (
            f"由 {t['derived_from']} 派生" if t.get("derived_from") else "原文")
        lines.append(f"| {t.get('priority')} | {t.get('key')} | "
                     f"{t.get('direction')} | {t.get('unit')} | {src} |")

    units = spec.get("units") or {}
    lines.append(f"\n**指标单位表**：{_kv_inline(units)}\n")

    if spec.get("assumptions"):
        lines.append("\n**解析假设（可审计）**\n")
        for a in spec["assumptions"]:
            lines.append(f"- {a}")
    if spec.get("unparsed_requirements"):
        lines.append("\n**识别到但未给出数值的要求（不丢弃）**\n")
        for a in spec["unparsed_requirements"]:
            lines.append(f"- {a}")
    return "\n".join(lines) + "\n"


def sec_crosscheck(ctx):
    spec = ctx["spec"] or {}
    cc = spec.get("cross_check") or {}
    lines = ["\n### 2.4 LLM × 规则 交叉校验\n"]
    if not cc:
        lines.append("本次解析未走 LLM 路径（`--no-llm`），"
                     "或尚无交叉校验数据。\n")
        lines.append(SKIP + "\n")
        return "\n".join(lines)
    lines.append("以「规则引擎作为独立第二意见」校验 LLM 输出，差异如下：\n")
    lines.append("| 差异类型 | 内容 |")
    lines.append("|---|---|")
    lines.append(f"| 仅 LLM 给出的硬约束 | {_join(cc.get('llm_only_constraints'))} |")
    lines.append(f"| 仅规则给出的硬约束 | {_join(cc.get('rule_only_constraints'))} |")
    lines.append(f"| 仅 LLM 给出的优化目标 | {_join(cc.get('llm_only_targets'))} |")
    lines.append(f"| 仅规则给出的优化目标 | {_join(cc.get('rule_only_targets'))} |")
    mism = cc.get("value_mismatch") or []
    lines.append("| 数值不一致 | " + (_join(
        [f"{m['key']}: LLM={m['llm']} vs 规则={m['rule']}" for m in mism])
        or "无") + " |")
    lines.append("\n> 该差异表可直接作为「为什么不能只依赖 LLM」的量化证据。\n")
    return "\n".join(lines)


def sec_cot(ctx):
    logs = ctx["cot_logs"]
    lines = ["\n### 2.5 推理链日志（Chain-of-Thought）\n"]
    if not logs:
        lines.append("暂未找到 `logs/llm_*.json`。运行带 API Key 的解析命令后"
                     "会自动落盘。\n")
        lines.append(SKIP + "\n")
        return "\n".join(lines)
    total_r = sum(x["reasoning_len"] for x in logs)
    lines.append(f"共 {len(logs)} 次 LLM 调用，累计思维链 {total_r} 字。"
                 f"每次调用的 system/user prompt、思维链、原始响应、用量均已落盘。\n")
    lines.append("| 时间 | 用途 | 模型 | 耗时(s) | 思维链(字) | 响应(字) |")
    lines.append("|---|---|---|---|---|---|")
    for x in logs[-8:]:
        lines.append(f"| {x['time']} | {x['tag']} | {x['model']} | "
                     f"{x['elapsed']} | {x['reasoning_len']} | {x['response_len']} |")
    last = logs[-1]
    if last["reasoning"]:
        lines.append("\n**最近一次思维链节选**\n")
        lines.append("```text\n" + last["reasoning"].strip() + " …\n```\n")
    return "\n".join(lines)


def sec_topology(ctx):
    topo = ctx["topology"]
    lines = ["\n## 3. 第②问：拓扑模块识别与参数约减\n"]
    if not topo:
        lines.append(SKIP + "\n")
        return "\n".join(lines)

    nl = topo.get("netlist", {})
    lines.append("### 3.1 识别结果\n")
    lines.append(f"输入网表：`{topo.get('source_netlist', '-')}`；"
                 f"器件 {nl.get('total_devices', '-')} 个"
                 f"（{_kv_inline(nl.get('device_counts', {}))}）；"
                 f"作用域 {len(nl.get('scopes', []))} 个。\n")

    lines.append("| 作用域 | 模块类型 | 器件 | 角色 | 识别依据 | 置信度 |")
    lines.append("|---|---|---|---|---|---|")
    for m in topo.get("modules", []):
        lines.append(f"| {m.get('scope') or '顶层'} | {m.get('module_type')} | "
                     f"{', '.join(m.get('devices', []))} | "
                     f"{'+'.join(m.get('roles') or [])} | "
                     f"{m.get('evidence', '')} | {m.get('confidence', '-')} |")

    lines.append("\n### 3.2 器件 → 角色映射（多角色标注）\n")
    roles = topo.get("device_roles") or {}
    if roles:
        lines.append("| 器件 | 功能角色 |")
        lines.append("|---|---|")
        for dev, rs in roles.items():
            lines.append(f"| {dev} | {'、'.join(rs)} |")
        lines.append("\n> 同一器件出现在多个角色中，是**多角色标注**的直接体现："
                     "例如接在差分对输出节点上的 PMOS 镜像同时是「电流镜」和「有源负载」。\n")

    lines.append("\n### 3.3 参数约减\n")
    vr = topo.get("variable_reduction", {})
    lines.append(f"变量个数：**{vr.get('before', '-')} → 自由变量 "
                 f"{vr.get('after_free', '-')} 个**"
                 f"（含联动变量共 {vr.get('after_total', '-')} 个，"
                 f"约减率 {_pct(vr.get('reduction_ratio'))}）。\n")
    if ctx["figures"].get("reduction"):
        lines.append(f"\n![变量约减对比]({ctx['figures']['reduction']})\n")
    if ctx["figures"].get("modules"):
        lines.append(f"\n![模块识别分布]({ctx['figures']['modules']})\n")

    if vr.get("linked_variables"):
        lines.append("\n**联动变量（非独立，随参考变量按初始比例缩放）**\n")
        lines.append("| 变量 | 约束 |")
        lines.append("|---|---|")
        for v in vr["linked_variables"]:
            lines.append(f"| {v['name']} | {v['constraint']} |")

    rows = ctx["variables"]
    if rows:
        lines.append("\n**完整变量表**（`variables.csv`）\n")
        cols = ["variable", "value", "min", "max", "unit", "type",
                "constraint", "reason"]
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "---|" * len(cols))
        for r in rows:
            lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")

    pdk = topo.get("pdk_limits", {})
    if pdk.get("text"):
        lines.append("\n**PDK 取值约束**\n")
        for k, v in pdk["text"].items():
            lines.append(f"- {k}: {v}")
        af = pdk.get("area_formula") or {}
        if af:
            lines.append(f"- 面积公式：晶体管 `{af.get('transistor')}`、"
                         f"电容 `{af.get('capacitor')}`、电阻 `{af.get('resistor')}`")

    lines.append("\n### 3.4 约减后网表\n")
    reduced = ctx.get("reduced_text")
    if reduced:
        snippet = "\n".join(reduced.splitlines()[:60])
        lines.append("```spice\n" + snippet + "\n```\n")
    else:
        lines.append(SKIP + "\n")
    return "\n".join(lines)


def sec_optimizer(ctx):
    opt = ctx["opt"]
    lines = ["\n## 4. 第③问：基于约束的多目标尺寸优化\n"]
    lines.append("""
### 4.1 为什么"可行性优先"

赛题 6.3(2) 是**全有或全无**：PM / GM / I_OPA 三项，任一项在任意 PVT 下不满足，
该项直接扣满 10 分；而 6.3(3) 的 DCGain / UGB / Area 是按名次比例给分。
因此最优策略是先保证 100% 可行，再在可行域内优化目标。

实现上用约束支配（Deb 思想）：只要有可行解就绝不接受不可行解；
两个都不可行时比较归一化违反度。

### 4.2 预算倒推（3 小时时限）

```
可用评估次数 = 总时限 × (1 - 预留比例) ÷ 单次仿真耗时
```
预留默认 20%，留给反标与全 PVT 验证。先跑一次真实仿真测出单次耗时，再决定种群与代数。

### 4.3 工程保障

| 需求 | 实现 |
|---|---|
| VPN 掉线 | 每次迭代落盘 `checkpoint.json`，`--resume` 续跑 |
| 过程取证（评分项 6） | `optimization_log.jsonl` 逐次记录参数/指标/违反度 |
| 交付物一致 | 最优尺寸写回 `param_file`，并保留 `.orig` 备份 |
""")
    lines.append("### 4.4 优化结果\n")
    if not opt:
        lines.append(SKIP + "\n")
        lines.append("> 拿到华大九天账号后，运行 `optimization.py` 会产出 "
                     "`optimization_result.json`，重跑本生成器即可自动填入真实结果。\n")
        return "\n".join(lines)

    cons = opt.get("constraints", {})
    best = opt.get("best_result") or {}
    first = opt.get("first_result") or {}
    lines.append(f"- 运行模式：`{opt.get('mode')}`"
                 f"（mock 为本地测试替身，不代表真实电路）")
    lines.append(f"- 优化变量 {len(opt.get('variables', []))} 个："
                 f"`{', '.join(opt.get('variables', []))}`")
    lines.append(f"- 评估次数 {opt.get('n_evals')}（{opt.get('generations')} 代），"
                 f"总耗时 {opt.get('elapsed_sec')}s")
    lines.append(f"- 约束满足：**{'是' if opt.get('feasible') else '否'}**"
                 f"{'' if opt.get('feasible') else ' → ' + str(opt.get('best_violations'))}")
    lines.append("")
    lines.append("| 指标 | 初始值 | 最优值 | 要求 |")
    lines.append("|---|---|---|---|")
    lines.append(f"| DCGain | {_num(first.get('dc_gain_db'))} | "
                 f"{_num(best.get('dc_gain_db'))} | ≥ {cons.get('dc_gain_min')} dB |")
    lines.append(f"| UGB | {_num(first.get('ugb_hz'))} | "
                 f"{_num(best.get('ugb_hz'))} | 越大越好 |")
    lines.append(f"| PM | {_num(first.get('pm_deg'))} | "
                 f"{_num(best.get('pm_deg'))} | ≥ {cons.get('pm_min')} deg |")
    lines.append(f"| GM | {_num(first.get('gm_db'))} | "
                 f"{_num(best.get('gm_db'))} | ≤ {cons.get('gm_max')} dB |")
    lines.append(f"| I_OPA | {_num(first.get('i_opa_a'))} | "
                 f"{_num(best.get('i_opa_a'))} | ≤ {cons.get('i_opa_max')} A |")
    lines.append(f"| Area | {_num(first.get('area_um2'))} | "
                 f"{_num(best.get('area_um2'))} | 越小越好 |")
    if ctx["figures"].get("pareto"):
        lines.append(f"\n![Pareto 权衡]({ctx['figures']['pareto']})\n")
        lines.append(f"Pareto 非支配解 {len(opt.get('pareto', []))} 个，"
                     f"完整数据见 `optimization_result.json`。\n")
    return "\n".join(lines)


def sec_flow(ctx):
    return """
## 5. 第④问：PyAether 自动化链路

一条命令跑完全流程：

```
参数化电路图 ──► MDE 仿真 ──► 指标提取 ──► 优化迭代 ──► 尺寸反标 ──► 全PVT验证 ──► 报告
 schematic_var      ALPS/MDE     output.log    优化器        var_best      报告
```

### 5.1 接口契约（阶段 1 需用真实资产校准）

| 项 | 当前状态 | 待办 |
|---|---|---|
| CLI 参数 | 与赛题 PDF 第 13–14 页完全一致 | 无需改动 |
| `param_file` 格式 | 保真读写，未改动行逐字节一致 | 用真实样例补回归用例 |
| 仿真命令 | `--sim-cmd` 模板（`{param_file}` / `{out}`） | 填入官方脚本实际命令 |
| 指标解析 | 多 corner 取最坏值 | 用真实 `output.log` 校准 |
| 变量命名 | `<器件>_<参数>`（如 `NM1_m`） | 对齐 PyAether `_cdf` 命名 |

### 5.2 交付物清单

| 输出 | 文件 | 状态 |
|---|---|---|
| Spec 解析 JSON | `agent1_spec_parser/output/spec.json` | 已产出 |
| 模块识别 + 变量个数 + 约减网表 | `agent2_topology/output/*` | 已产出 |
| 变量统计 CSV | `variables.csv`（含初值/上下界/类型） | 已产出 |
| 最优尺寸 | `best_param_file.txt` / 写回 `param_file` | 待真实运行 |
| 带变量电路图 / 反标电路图 | `schematic_var` / `schematic_var_best` | 待服务器 |
| 全 PVT 仿真验证报告 | 波形 + 达标情况 | 待服务器 |

### 5.3 提交包结构

```
eda_2026_case_1/
├── optimization.py          # 入口（赛题要求）
├── q3_optimizer/            # 优化器实现
├── README.md
└── docs/
```
打包与结构校验：`python tools/pack_submission.py`（顶层目录与入口文件都会被校验）。
"""


def sec_tests(ctx):
    return f"""
## 6. 测试报告

全部回归用例可在**本地、无服务器、无 API Key** 条件下运行：

```bash
python -m unittest discover -s tests -t . -v
```

| 测试文件 | 覆盖内容 | 用例数 |
|---|---|---|
| `tests/test_spec_rules.py` | 四要素完整性、GM 解析、中英文、单位换算、LLM 规范化 | {ctx['test_counts'].get('spec', '-')} |
| `tests/test_topology.py` | 命名判型、subckt 作用域、镜像比、多角色识别、无源网络 | {ctx['test_counts'].get('topo', '-')} |
| `tests/test_optimizer.py` | 参数保真、指标解析、约束优先、预算规划、续跑、端到端 | {ctx['test_counts'].get('opt', '-')} |

**当前结果：{ctx['test_summary']}**

每个金标准网表（`tests/netlists/*.sp`）都对应一个曾经真实存在的缺陷，
构成防回归基线；例如：

- `ota_nm_pm.sp`：器件类型不能靠首字母判断（原实现会把 NM1/PM1 整张网表丢光）
- `hier_bias_amp.sp`：不能跨 `.subckt` 缝合器件（否则生成错误约束）
- `mirror_ratio.sp`：镜像比必须原样保留（按 W 表达的比例曾被静默改成 1:1）
- `folded_cascode.sp`：折叠共源共栅的级联管与有源负载
- `moscap_cmfb.sp`：MOS 电容与 Dummy 的区分、CMFB 识别
"""


def sec_appendix(ctx):
    return f"""
## 7. 附录：可复现性

```bash
pip install -r requirements.txt

# 第①问（无 Key 走规则；有 Key 自动 LLM+规则交叉校验）
python -m agent1_spec_parser.main "设计全差分运放，PM≥60°，工作电流3mA，增益尽可能大…"

# 第②问
python -m agent2_topology.main tests/sample_netlist.sp

# 第③问（本地自测；真实运行把 --mock 换成 --sim-cmd）
python optimization.py --param_file tests/data/param_init.txt \\
    --variables-csv agent2_topology/output/variables.csv --mock

# 回归测试 + 报告
python -m unittest discover -s tests -t . -v
python tools/make_report.py --run-local
```

> 本报告由 `tools/make_report.py` 自动生成，所有数字均直接取自产物文件，
> 真实数据到位后重跑该命令即可刷新。
"""


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _num(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def _pct(v):
    if v is None:
        return "-"
    try:
        return f"{float(v) * 100:.0f}%"
    except (TypeError, ValueError):
        return str(v)


def _join(xs):
    if not xs:
        return "无"
    return "、".join(str(x) for x in xs)


def _kv_inline(d):
    if not d:
        return "-"
    return "、".join(f"{k}={v}" for k, v in d.items())


def count_tests():
    """按文件统计用例数（静态扫描，避免真的跑测试）。"""
    counts = {}
    for name, key in (("test_spec_rules.py", "spec"),
                      ("test_topology.py", "topo"),
                      ("test_optimizer.py", "opt")):
        p = ROOT / "tests" / name
        if p.exists():
            counts[key] = sum(1 for line in p.read_text(encoding="utf-8").splitlines()
                              if line.strip().startswith("def test_"))
    return counts


def run_tests():
    r = _run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."])
    tail = (r.stderr or "") + (r.stdout or "")
    for line in tail.splitlines():
        if line.startswith("Ran ") or line.startswith("OK") or line.startswith("FAILED"):
            return line.strip()
    return "未运行"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="技术报告生成器")
    ap.add_argument("--out", default="docs/technical_report.md")
    ap.add_argument("--spec", default="agent1_spec_parser/output/spec.json")
    ap.add_argument("--topology", default="agent2_topology/output/topology_result.json")
    ap.add_argument("--variables", default="agent2_topology/output/variables.csv")
    ap.add_argument("--reduced", default="agent2_topology/output/netlist_reduced.sp")
    ap.add_argument("--opt", default=None,
                    help="optimization_result.json（有则填入真实优化结果）")
    ap.add_argument("--run-local", action="store_true",
                    help="先生成本地 ①②问产物（第①问走纯规则，不需要 Key）")
    ap.add_argument("--no-tests", action="store_true", help="跳过测试统计")
    args = ap.parse_args(argv)

    if args.run_local:
        run_local_agents()

    out_path = Path(args.out)
    fig_dir = out_path.parent / "figures"
    rel = lambda p: str(Path(p).relative_to(out_path.parent)).replace("\\", "/")

    ctx = {
        "spec": load_json(args.spec),
        "topology": load_json(args.topology),
        "variables": load_csv_rows(args.variables),
        "opt": load_json(args.opt) if args.opt else None,
        "cot_logs": scan_cot_logs(ROOT / "logs"),
        "figures": {},
        "test_counts": count_tests(),
        "test_summary": "未运行" if args.no_tests else run_tests(),
    }

    try:
        from agent1_spec_parser.prompt import SYSTEM_PROMPT, FEW_SHOT
        ctx["system_prompt"], ctx["few_shot"] = SYSTEM_PROMPT, FEW_SHOT
    except Exception:
        ctx["system_prompt"] = ctx["few_shot"] = ""

    reduced = Path(args.reduced)
    ctx["reduced_text"] = reduced.read_text(encoding="utf-8") if reduced.exists() else None

    # ---- 图 ----
    topo = ctx["topology"]
    if topo:
        vr = topo.get("variable_reduction", {})
        if vr.get("before"):
            ctx["figures"]["reduction"] = rel(svg_bar_chart(
                "参数约减：变量个数对比",
                [("约减前", vr.get("before", 0)),
                 ("约减后(自由)", vr.get("after_free", 0)),
                 ("约减后(含联动)", vr.get("after_total", 0))],
                fig_dir / "variable_reduction.svg"))
        summ = topo.get("module_summary") or {}
        if summ:
            ctx["figures"]["modules"] = rel(svg_bar_chart(
                "拓扑模块识别分布", sorted(summ.items(), key=lambda kv: -kv[1]),
                fig_dir / "module_summary.svg"))
    opt = ctx["opt"]
    if opt and opt.get("pareto"):
        best = opt.get("best_result") or {}
        pts = []
        for e in opt["pareto"]:
            r = e["result"]
            if r.get("ugb_hz") is None or r.get("area_um2") is None:
                continue
            is_best = (r.get("ugb_hz") == best.get("ugb_hz")
                       and r.get("area_um2") == best.get("area_um2"))
            pts.append((r["area_um2"], r["ugb_hz"] / 1e6,
                        "best" if is_best else "", is_best))
        if pts:
            ctx["figures"]["pareto"] = rel(svg_scatter(
                "Pareto 前沿：面积 vs 单位增益带宽", pts,
                "面积 (µm²)", "UGB (MHz)", fig_dir / "pareto.svg"))

    sections = [sec_header(ctx), sec_architecture(ctx), sec_spec(ctx),
                sec_crosscheck(ctx), sec_cot(ctx), sec_topology(ctx),
                sec_optimizer(ctx), sec_flow(ctx), sec_tests(ctx),
                sec_appendix(ctx)]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(sections), encoding="utf-8")

    print(f"[report] 已生成 {out_path}")
    print(f"[report] 图表目录 {fig_dir}")
    print(f"[report] 测试: {ctx['test_summary']}")
    print(f"[report] CoT 日志 {len(ctx['cot_logs'])} 条")
    for k, v in ctx["figures"].items():
        print(f"[report] 图 {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
