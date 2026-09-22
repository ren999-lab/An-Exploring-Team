"""拓扑模块识别：基于网表连接关系的图规则算法。

识别目标（评分点）：电流镜、输入对管、有源负载、匹配器件、Dummy 器件。
每条规则输出 {module_type, devices, evidence, scope}，evidence 即报告所需的"识别依据"。

本版修正（第②问 P0）：
* **按 .subckt 作用域分别识别**。原实现把所有 subckt 的器件扁平合并，会把偏置支路
  与放大支路"缝合"成并不存在的电流镜，直接导致"每错一个约束扣 1 分"。
* MOS 极性改用 `Device.polarity`（由模型名判定），不再假设器件名首字母为 M。
* 未被任何模块认领的 R/C/L 也进入结果（原实现只扫 MOS，补偿/去耦器件被静默丢弃）。
* `identified` 集合显式计算，避免原实现里"恰好等价"的写法。
"""

import re
from collections import defaultdict

from .netlist_parser import is_rail


def _group(seq, key):
    g = defaultdict(list)
    for x in seq:
        g[key(x)].append(x)
    return g


def recognize(netlist):
    """主入口：Netlist -> 模块识别结果列表（按作用域分别识别）。"""
    results = []
    for scope, devs in netlist.analysis_scopes():
        results.extend(recognize_scope(scope, devs))
    return results


def recognize_scope(scope, devs, qualifier=None):
    """对单个作用域做模块识别。qualifier(name) 用于多顶层作用域时限定器件名。"""
    q = qualifier or (lambda n: n)
    mos = [d for d in devs if d.dtype == "M"]
    results = []
    if not mos:
        results.extend(_passive_bucket(scope, devs, q))
        return results

    dummy = _find_dummy(mos)
    if dummy:
        results.append({"module_type": "Dummy器件",
                        "devices": [q(d.name) for d in dummy],
                        "scope": scope,
                        "evidence": "D/S 接同一节点（或命名含 dummy），"
                                    "栅接固定偏置/电源轨，不参与信号放大"})

    signal = [m for m in mos if m not in dummy]
    mirrors = _find_current_mirrors(signal)
    for ref, slaves, ev in mirrors:
        results.append({"module_type": "电流镜",
                        "devices": [q(ref.name)] + [q(s.name) for s in slaves],
                        "scope": scope, "evidence": ev})

    mirror_members = {d for _, slaves, _ in mirrors for d in slaves}
    mirror_members |= {ref for ref, _, _ in mirrors}
    rest = [m for m in signal if m not in mirror_members]

    diff, tail = _find_diff_pair(rest)
    if diff:
        results.append({"module_type": "输入对管（差分对）",
                        "devices": [q(d.name) for d in diff],
                        "scope": scope,
                        "evidence": f"两管极性相同、源极共连至尾节点 {diff[0].nodes[2]}，"
                                    f"栅极分别接 {diff[0].nodes[1]} / {diff[1].nodes[1]}"})
    if tail:
        results.append({"module_type": "尾电流源",
                        "devices": [q(tail.name)],
                        "scope": scope,
                        "evidence": f"漏极接差分对尾节点，源极接电源轨 {tail.nodes[2]}，"
                                    f"栅接固定偏置 {tail.nodes[1]}"})

    claimed = set(mirror_members)
    claimed |= set(diff or [])
    if tail:
        claimed.add(tail)
    loads = _find_active_load([m for m in signal if m not in claimed], diff)
    if loads:
        results.append({"module_type": "有源负载",
                        "devices": [q(d.name) for d in loads],
                        "scope": scope,
                        "evidence": "栅漏短接或栅极互联的负载管，连接差分对漏极输出节点"})

    for grp in _find_matched_devices(mos, dummy):
        results.append({"module_type": "匹配器件",
                        "devices": [q(d.name) for d in grp],
                        "scope": scope,
                        "evidence": "同类模型且尺寸参数完全相同，属版图匹配结构"})

    # 未被认领的 MOS 单独列出，避免静默丢弃
    identified = set()
    for r in results:
        identified |= set(r["devices"])
    others = [q(d.name) for d in mos if q(d.name) not in identified]
    if others:
        results.append({"module_type": "其他/待人工复核", "devices": others,
                        "scope": scope,
                        "evidence": "未命中现有规则（可能是共源共栅、输出级等），"
                                    "可扩展规则或调用 LLM 复核"})

    results.extend(_passive_bucket(scope, devs, q, already=identified))
    return results


def _passive_bucket(scope, devs, q, already=None):
    """R/C/L 单独成组：原实现只扫 MOS，补偿网络与去耦电容被完整丢弃。"""
    already = already or set()
    passives = [d for d in devs if d.dtype in ("R", "C", "L") and q(d.name) not in already]
    if not passives:
        return []
    return [{
        "module_type": "无源器件（RC/去耦，用途待确认）",
        "devices": [q(d.name) for d in passives],
        "scope": scope,
        "evidence": "电阻/电容/电感：可能是密勒补偿、调零电阻或电源去耦，"
                    "按 PDK 以 W/L/Seg 参数化",
    }]


def _find_dummy(mos):
    dummy = []
    for m in mos:
        if "dummy" in m.name.lower():
            dummy.append(m)
        elif len(m.nodes) >= 3 and m.nodes[0].lower() == m.nodes[2].lower():
            # D/S 短接：典型 dummy；注意 MOS 电容也是同节点，Stage B 再细分
            dummy.append(m)
    return dummy


def _find_current_mirrors(cand):
    """同极性、同源极节点、栅极互联 -> 镜像组；其中栅漏短接者为参考臂。"""
    mirrors = []
    cand = [m for m in cand if m.polarity]
    groups = _group(cand, lambda m: (m.polarity, m.nodes[2]))
    for (pol, src), members in groups.items():
        if len(members) < 2:
            continue
        for gate, gm in _group(members, lambda m: m.nodes[1]).items():
            if len(gm) < 2:
                continue
            refs = [m for m in gm if m.nodes[0] == gate and m.nodes[0] == m.nodes[1]]
            if not refs:
                refs = [m for m in gm if m.nodes[0] == gate]
            if not refs:
                continue
            ref = refs[0]
            slaves = [m for m in gm if m is not ref]
            if not slaves:
                continue
            ev = (f"{'NMOS' if pol == 'n' else 'PMOS'} 源极共连 {src}，栅极共连 {gate}；"
                  f"{ref.name} 栅漏短接为参考臂")
            mirrors.append((ref, slaves, ev))
    return mirrors


def _find_diff_pair(cand):
    """两管极性相同、源极共连于非电源轨节点，栅极分别接不同节点（输入）。"""
    for src, members in _group(cand, lambda m: m.nodes[2]).items():
        if is_rail(src) or len(members) != 2:
            continue
        a, b = members
        if a.polarity and a.polarity == b.polarity and a.nodes[1] != b.nodes[1]:
            tail = None
            for m in cand:
                if m is not a and m is not b and m.nodes[0] == src:
                    tail = m
                    break
            return [a, b], tail
    return None, None


def _find_active_load(cand, diff):
    if not diff:
        return []
    out_nodes = {d.nodes[0] for d in diff}
    loads = [m for m in cand if m.nodes[0] in out_nodes]
    picked = []
    for _, gm in _group(loads, lambda m: m.nodes[1]).items():
        if len(gm) >= 2:
            picked.extend(gm)
    for m in loads:
        if m.nodes[0] == m.nodes[1] and m not in picked:
            picked.append(m)
    return picked


def _name_stem(name):
    """取出实例名的"同族前缀"：M1/M2 -> M，MPA/MPB -> MP，MA1/MA2 -> MA。"""
    return re.sub(r"(?i)(\d+|[ab])$", "", name)


def _find_matched_devices(mos, dummy):
    """同族前缀 + 同模型 + 同极性 + 尺寸参数完全相同 -> 匹配结构。"""
    matched = []
    cand = [m for m in mos if m not in dummy]
    key = lambda m: (m.polarity, m.model, _name_stem(m.name),
                     str(sorted(m.params.items())))
    for _, members in _group(cand, key).items():
        if len(members) >= 2:
            matched.append(members)
    return matched
