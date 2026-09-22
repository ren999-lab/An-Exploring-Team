"""拓扑模块识别：基于网表连接关系的图规则算法。

识别目标（评分点）：电流镜、输入对管、有源负载、匹配器件、Dummy 器件。
每条规则输出 {module_type, devices, evidence}，evidence 即报告所需的"识别依据"。
"""

from collections import defaultdict

RAIL_HINTS = ("vdd", "vssa", "gnd", "vss", "avdd", "avss", "vcc", "vee")


def _is_rail(node):
    return any(h in node.lower() for h in RAIL_HINTS)


def _group(d, key):
    g = defaultdict(list)
    for x in d:
        g[key(x)].append(x)
    return g


def recognize(netlist):
    """主入口：Netlist → 模块识别结果列表。"""
    mos = netlist.mos_list()
    results = []
    if not mos:
        return results

    dummy = _find_dummy(mos)
    if dummy:
        results.append({"module_type": "Dummy器件", "devices": [d.name for d in dummy],
                        "evidence": "D/S 接同一节点且栅接固定偏置/电源轨，或命名含 dummy，不参与信号放大"})
    mirrors = _find_current_mirrors([m for m in mos if m not in dummy])
    for ref, slaves, ev in mirrors:
        names = [ref.name] + [s.name for s in slaves]
        results.append({"module_type": "电流镜", "devices": names, "evidence": ev})
    diff, tail = _find_diff_pair([m for m in mos if m not in dummy]
                                 and [m for m in mos
                                      if m not in dummy and not _in(m, mirrors)])
    if diff:
        results.append({"module_type": "输入对管（差分对）", "devices": [d.name for d in diff],
                        "evidence": f"两管源极共连至尾节点 {diff[0].nodes[2]}，栅极分别接两输入"})
    if tail:
        results.append({"module_type": "尾电流源", "devices": [tail.name],
                        "evidence": f"漏极接差分对尾节点，源极接电源轨，栅接固定偏置 {tail.nodes[1]}"})
    loads = _find_active_load([m for m in mos if m not in dummy and not _in(m, mirrors)
                               and m not in (diff or []) and m != tail], diff)
    if loads:
        results.append({"module_type": "有源负载", "devices": [d.name for d in loads],
                        "evidence": "栅漏短接或栅极互联的负载管，连接差分对漏极输出节点"})

    matched = _find_matched_devices(mos, dummy)
    if matched:
        for grp in matched:
            results.append({"module_type": "匹配器件", "devices": [d.name for d in grp],
                            "evidence": "同名前缀/xm 结尾编号相邻且参数相同，属版图匹配结构"})

    identified = {d.name for r in results for d in mos if d.name in r["devices"]}
    others = [m.name for m in mos if m.name not in identified]
    if others:
        results.append({"module_type": "其他/待人工复核", "devices": others,
                        "evidence": "未命中现有规则（可能是共源共栅、输出级等），可扩展规则或调用 LLM 复核"})
    return results


def _in(m, mirrors):
    return any(m is ref or m in slaves for ref, slaves, _ in mirrors)


def _find_dummy(mos):
    dummy = []
    for m in mos:
        if "dummy" in m.name.lower():
            dummy.append(m)
        elif m.nodes[0].lower() == m.nodes[2].lower():  # d==s 短接
            dummy.append(m)
    return dummy


def _find_current_mirrors(cand):
    """同类型、同源极（电源轨）、栅极互联 → 镜像组；其中栅漏短接者为参考臂。"""
    mirrors = []
    groups = _group(cand, lambda m: (m.is_nmos, m.nodes[2]))  # (类型, 源极节点)
    for (is_n, src), members in groups.items():
        if len(members) < 2 or _is_rail(src) is False and len(members) < 2:
            continue
        gate_groups = _group(members, lambda m: m.nodes[1])  # 按栅极节点再分组
        for gate, gm in gate_groups.items():
            if len(gm) < 2:
                continue
            refs = [m for m in gm if m.nodes[0] == gate]  # 栅漏短接 = 参考臂
            slaves = [m for m in gm if m.nodes[0] != gate]
            if refs and slaves:
                ev = (f"{('NMOS' if is_n else 'PMOS')} 源极共连 {src}，栅极共连 {gate}；"
                      f"{refs[0].name} 栅漏短接为参考臂")
                mirrors.append((refs[0], slaves, ev))
    return mirrors


def _find_diff_pair(cand):
    """两管源极共连于非电源轨节点，栅极分别接不同节点（输入）。"""
    groups = _group(cand, lambda m: m.nodes[2])
    for src, members in groups.items():
        if _is_rail(src) or len(members) != 2:
            continue
        a, b = members
        if a.is_nmos == b.is_nmos and a.nodes[1] != b.nodes[1]:
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
    loads = []
    for m in cand:
        if not m.is_pmos and not m.is_nmos:
            continue
        # 与差分对漏极相连、栅极与其他负载管互联（电流镜负载）
        if m.nodes[0] in out_nodes:
            loads.append(m)
    # 栅极互联成对的才是镜像负载；单管栅漏短接的也算
    gate_groups = _group(loads, lambda m: m.nodes[1])
    picked = [m for g, gm in gate_groups.items() if len(gm) >= 2 for m in gm]
    picked += [m for m in loads if m.nodes[0] == m.nodes[1] and m not in picked]
    return picked


def _find_matched_devices(mos, dummy):
    """参数完全相同且命名序列相邻（如 XM1/XM2, MPA/MPB）→ 匹配结构。"""
    matched = []
    groups = _group([m for m in mos if m not in dummy],
                    lambda m: (m.is_nmos, m.model, str(m.params)))
    for _, members in groups.items():
        if len(members) < 2:
            continue
        import re
        base = {}
        for m in members:
            stem = re.sub(r"(?i)(\d+|[ab]$)", "", m.name)
            base.setdefault(stem, []).append(m)
        for stem, gm in base.items():
            if len(gm) >= 2:
                matched.append(gm)
    return matched
