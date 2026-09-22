"""拓扑模块识别：基于网表连接关系的图规则算法。

识别目标（评分点）：电流镜、输入对管、有源负载、匹配器件、Dummy 器件。
每条规则输出 {module_type, roles, devices, evidence, scope, confidence}，
evidence 即报告所需的"识别依据"。

## 架构：多角色标注（multi-label），而不是器件二选一

原实现把器件当成互斥的划分：一旦 M3/M4 被判成"电流镜"，就不再可能是"有源负载"，
于是**标准两级运放的模块列表里根本没有"有源负载"**——而它恰恰就是那对 PMOS 镜像。
同理，"匹配器件"把已被认领的管子重复报一遍，成了纯噪音。

现在改成：**每个器件可以同时承载多个功能角色**。
* 一个 PMOS 镜像既登记 "电流镜"，也登记 "有源负载"（当它的漏极接在差分对输出节点上时）；
* 顶层给出 `device_roles` 映射，报告里可以直接画"器件 -> 角色"表；
* "匹配器件"只报告**尚未被任何结构规则覆盖**的匹配组，避免重复计数。

## 本版新增/修正

* **有源负载**：沿"第一级输出节点 -> 电源轨"的负载通路识别，支持共源共栅堆叠。
* **共源共栅**：`上管源极 == 下管漏极` 且同极性、上管非二极管连接；按共享栅偏置分组。
* **输出级**：用 `.subckt` 端口判定输出节点（非电源轨、且不是任何器件的栅极）。
* **MOS 电容 vs Dummy**：`D/S/B 同节点` 判为 MOS 电容（栅是另一极板），
  与真正的 Dummy 分开；MOS 电容仍保留为优化变量（PyAether 会把所有晶体管参数化）。
* **多个差分对**：全部识别，不再只返回第一组。
* **电流镜**：要求组内有二极管连接参考臂，或栅节点是内部节点（不是子电路端口），
  避免把"共享外部偏置的两只管子"误判成电流镜。
* **补偿网络 / 去耦电容 / CMFB**：密勒补偿 RC、电源去耦、共模反馈网络单独成模块。
"""

import re
from collections import defaultdict

from .netlist_parser import is_rail

CMFB_NET_RE = re.compile(r"cmfb|cm_fb|vcm|cm_?sense|cmref", re.IGNORECASE)
# 哑管命名：DUMMY / DUM / MDUM1 / XDUM 等都要覆盖（"MDUM1" 里并没有连续的 "dummy"）
DUMMY_NAME_RE = re.compile(r"dum", re.IGNORECASE)

PASSIVE_TYPES = ("R", "C", "L")


def _group(seq, key):
    g = defaultdict(list)
    for x in seq:
        g[key(x)].append(x)
    return g


def _d(dev, i):
    """安全取节点。"""
    return dev.nodes[i] if len(dev.nodes) > i else ""


def _is_port(node, ports):
    return node in ports


def _name_stem(name):
    """实例名的同族前缀：M1/M2 -> M，MPA/MPB -> MP，MA1/MA2 -> MA。"""
    return re.sub(r"(?i)(\d+|[ab])$", "", name)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def recognize(netlist):
    """主入口：Netlist -> 模块识别结果列表（按作用域分别识别）。"""
    results = []
    for scope, devs in netlist.analysis_scopes():
        ports = netlist.defs.get(scope, {}).get("ports", [])
        results.extend(recognize_scope(scope, devs, ports=ports))
    return results


def device_role_map(modules):
    """{限定器件名: [角色...]}，供报告画"器件 -> 角色"表。"""
    out = defaultdict(list)
    for m in modules:
        for dev in m["devices"]:
            for role in m.get("roles") or [m["module_type"]]:
                if role not in out[dev]:
                    out[dev].append(role)
    return {k: v for k, v in out.items()}


def recognize_scope(scope, devs, ports=None, qualifier=None):
    """对单个作用域做模块识别。qualifier(name) 用于多顶层作用域时限定器件名。"""
    q = qualifier or (lambda n: n)
    ports = list(ports or [])
    mos = [d for d in devs if d.dtype == "M"]
    passives = [d for d in devs if d.dtype in PASSIVE_TYPES]
    out = []

    def add(mtype, devices, evidence, roles=None, confidence=0.9):
        if not devices:
            return
        out.append({
            "module_type": mtype,
            "roles": roles or [mtype],
            "devices": [q(d.name) for d in devices],
            "evidence": evidence,
            "scope": scope,
            "confidence": confidence,
        })

    if not mos:
        _add_passives(add, passives, q)
        return out

    dummy, moscaps = _split_dummy_moscap(mos)
    add("Dummy器件", dummy,
        "D/S 接同一节点（或命名含 dummy），不参与信号放大，作为匹配/边界哑管，"
        "不设优化变量", confidence=0.85)
    add("MOS电容", moscaps,
        "栅极是独立节点、D/S/B 短接成另一极板，用作去耦/滤波电容；"
        "仍按 PDK W/L/m 参数化", confidence=0.8)

    signal = [m for m in mos if m not in dummy and m not in moscaps]

    mirrors = _find_current_mirrors(signal, ports)
    diff_pairs = _find_diff_pairs(signal)
    tails = _find_tails(diff_pairs, signal)
    diffs = {d for pair in diff_pairs for d in pair["devices"]}
    tail_devs = {t["device"] for t in tails}
    cascodes = _find_cascodes(signal, tail_devs)

    # ---- 输入对管 ----
    for pair in diff_pairs:
        a, b = pair["devices"]
        add("输入对管（差分对）", [a, b],
            f"两管极性相同（{'NMOS' if a.polarity == 'n' else 'PMOS'}），"
            f"源极共连至尾节点 {a.nodes[2]}，栅极分别接 {a.nodes[1]} / {b.nodes[1]}，"
            f"构成差分输入对", confidence=0.95)

    # ---- 尾电流源 ----
    for t in tails:
        dev = t["device"]
        add("尾电流源", [dev],
            f"漏极接差分对尾节点 {dev.nodes[0]}，源极接电源轨 {dev.nodes[2]}，"
            f"栅接偏置 {dev.nodes[1]}，为输入对提供恒定尾电流", confidence=0.9)

    # ---- 电流镜 ----
    for mir in mirrors:
        names = [mir["ref"]] + mir["slaves"]
        add("电流镜", names, mir["evidence"], confidence=0.9)

    # ---- 共源共栅 ----
    for cas in _group_cascodes(cascodes):
        devs_ = [x for pair in cas["pairs"] for x in (pair["upper"], pair["lower"])]
        uniq = list(dict.fromkeys(devs_))
        add("共源共栅（cascode）", uniq,
            f"上管源极接在下管漏极上（{'、'.join(pair['text'] for pair in cas['pairs'])}），"
            f"同极性堆叠、上管非二极管连接，"
            + (f"栅极共接固定偏置 {cas['gate']}，" if cas["gate"] else "")
            + "用于提高输出阻抗/增益", confidence=0.85)

    # ---- 有源负载（沿第一级输出节点到电源轨的负载通路）----
    load_devs = _find_active_load(diff_pairs, signal, diffs, tail_devs)
    if load_devs:
        add("有源负载", load_devs,
            "接在差分对输出节点上、向电源轨提供负载电流的管子"
            "（镜像负载/共源共栅负载）；这是第一级的增益负载，"
            "与电流镜角色并存", confidence=0.85)

    # ---- 输出级 ----
    out_nodes = _find_output_nodes(devs, ports, diffs)
    out_stage = [d for d in signal
                 if _d(d, 0) in out_nodes and d not in diffs and d not in tail_devs]
    if out_stage:
        add("输出级", out_stage,
            f"漏极直接驱动输出端口 {', '.join(sorted(out_nodes))}，"
            f"决定输出摆幅与驱动能力", confidence=0.8)

    # ---- 无源网络 ----
    _add_passives(add, passives, q)

    # ---- CMFB ----
    cmfb_devs = [d for d in mos
                 if any(CMFB_NET_RE.search(str(n)) for n in d.nodes)]
    if cmfb_devs:
        nets = sorted({str(n) for d in cmfb_devs for n in d.nodes
                       if CMFB_NET_RE.search(str(n))})
        add("共模反馈（CMFB）", cmfb_devs,
            f"与共模反馈网络 {', '.join(nets)} 相连，用于稳定输出共模电平",
            confidence=0.7)

    # ---- 匹配器件（全部报告，并标注与哪些结构模块重叠）----
    covered = {}
    for i, m in enumerate(out):
        for dev in m["devices"]:
            covered.setdefault(dev, i)
    for grp in _find_matched_devices(mos, dummy):
        if len(grp) < 2:
            continue
        names = [q(d.name) for d in grp]
        owners = sorted({out[covered[n]]["module_type"]
                         for n in names if n in covered})
        add("匹配器件", grp,
            "同族前缀 + 同模型 + 同极性 + 尺寸参数完全相同，属版图匹配结构"
            + (f"（同时归属：{'、'.join(owners)}）" if owners else ""),
            confidence=0.6)
        out[-1]["subsumed_by"] = owners
        for n in names:
            idx = covered.get(n)
            if idx is not None and "匹配器件" not in out[idx]["roles"]:
                out[idx]["roles"].append("匹配器件")

    # ---- 兜底：未被任何模块覆盖的 MOS ----
    identified = {dev for m in out for dev in m["devices"]}
    others = [q(d.name) for d in mos if q(d.name) not in identified]
    if others:
        add("其他/待人工复核", [d for d in mos if q(d.name) in others],
            "未命中现有规则（可能是特殊偏置或有源器件），可扩展规则或调用 LLM 复核",
            confidence=0.3)

    return out


# ---------------------------------------------------------------------------
# Dummy / MOS 电容
# ---------------------------------------------------------------------------
def _split_dummy_moscap(mos):
    """区分 Dummy 与 MOS 电容。

    * 命名含 dum（DUMMY/MDUM/XDUM）-> Dummy
    * D/S 短接且 B 与 D 同节点 -> MOS 电容（D/S/B 短接成一块极板、栅是另一极板）
    * 其余 D/S 短接 -> Dummy
    """
    dummy, moscaps = [], []
    for m in mos:
        if DUMMY_NAME_RE.search(m.name):
            dummy.append(m)
            continue
        d, s = _d(m, 0).lower(), _d(m, 2).lower()
        b = _d(m, 3).lower()
        if d and d == s:
            if b and b == d:
                moscaps.append(m)
            else:
                dummy.append(m)
    return dummy, moscaps


# ---------------------------------------------------------------------------
# 电流镜
# ---------------------------------------------------------------------------
def _find_current_mirrors(cand, ports):
    """同极性、同源极节点、栅极互联 -> 镜像组。

    为防止把"共享外部偏置的两只管子"误判为电流镜：要求组内有二极管连接参考臂，
    或栅节点是电路内部节点（不是 .subckt 端口）。
    """
    mirrors = []
    cand = [m for m in cand if m.polarity]
    for (pol, src), members in _group(cand, lambda m: (m.polarity, m.nodes[2])).items():
        if len(members) < 2:
            continue
        for gate, gm in _group(members, lambda m: m.nodes[1]).items():
            if len(gm) < 2 or is_rail(gate):
                continue
            refs = [m for m in gm if _d(m, 0) == gate]
            if not refs and _is_port(gate, ports):
                continue        # 无参考臂且栅来自外部端口 -> 不是内部电流镜
            ref = refs[0] if refs else gm[0]
            slaves = [m for m in gm if m is not ref]
            if not slaves:
                continue
            kind = "NMOS" if pol == "n" else "PMOS"
            if refs:
                ev = (f"{kind} 源极共连 {src}，栅极共连 {gate}；"
                      f"{ref.name} 栅漏短接为参考臂，"
                      f"{'、'.join(s.name for s in slaves)} 按比例镜像")
            else:
                ev = (f"{kind} 源极共连 {src}，栅极共连内部偏置节点 {gate}，"
                      f"构成电流镜")
            mirrors.append({"ref": ref, "slaves": slaves, "gate": gate,
                            "source": src, "polarity": pol, "evidence": ev})
    return mirrors


# ---------------------------------------------------------------------------
# 差分对与尾电流源
# ---------------------------------------------------------------------------
def _find_diff_pairs(cand):
    """所有"两管极性相同、源极共连于非电源轨节点、栅极不同"的组。"""
    pairs = []
    for src, members in _group(cand, lambda m: m.nodes[2]).items():
        if is_rail(src) or len(members) < 2:
            continue
        for _, gm in _group(members, lambda m: m.polarity).items():
            if len(gm) != 2:
                continue
            a, b = gm
            if a.polarity and a.nodes[1] != b.nodes[1]:
                pairs.append({"devices": (a, b), "source": src})
    return pairs


def _find_tails(diff_pairs, cand):
    tails = []
    seen = set()
    for pair in diff_pairs:
        src = pair["source"]
        members = set(pair["devices"])
        for m in cand:
            if m in members or m in seen:
                continue
            if _d(m, 0) == src:
                tails.append({"device": m, "pair": pair})
                seen.add(m)
                break
    return tails


# ---------------------------------------------------------------------------
# 共源共栅
# ---------------------------------------------------------------------------
def _find_cascodes(cand, tail_devs=None):
    """上管源极 == 下管漏极，同极性，且上管不是二极管连接。

    `tail_devs` 用于排除一个重要误判：差分对的源极本来就接在尾电流源的漏极上，
    这是"尾电流源"而不是"共源共栅"，必须排除。
    """
    tail_devs = tail_devs or set()
    by_drain = defaultdict(list)
    for m in cand:
        by_drain[_d(m, 0)].append(m)
    stacks = []
    for upper in cand:
        src = _d(upper, 2)
        if not src or is_rail(src):
            continue
        if _d(upper, 0) == _d(upper, 1):
            continue                      # 二极管连接，不是共源共栅
        for lower in by_drain.get(src, []):
            if lower is upper or lower.polarity != upper.polarity:
                continue
            if lower in tail_devs:
                continue                  # 下管是尾电流源 -> 属于差分对结构
            if _d(lower, 1) == _d(upper, 1):
                continue                  # 栅极相同 -> 更像镜像堆叠
            stacks.append({"upper": upper, "lower": lower,
                           "text": f"{upper.name}.S={lower.name}.D({src})"})
    return stacks


def _group_cascodes(stacks):
    """按共享的栅偏置节点分组，便于输出一条模块记录。"""
    groups = []
    for gate, items in _group(stacks, lambda s: _d(s["upper"], 1)).items():
        groups.append({
            "gate": gate if gate and not is_rail(gate) else "",
            "pairs": items,
        })
    return groups


# ---------------------------------------------------------------------------
# 有源负载 / 输出级
# ---------------------------------------------------------------------------
def _find_active_load(diff_pairs, signal, diffs, tail_devs):
    """从差分对漏极出发，沿"漏极->源极"向上走到电源轨，沿途器件即负载。"""
    if not diff_pairs:
        return []
    first_stage_out = {_d(d, 0) for d in diffs}
    load, seen = [], set()
    frontier = [d for d in signal
                if _d(d, 0) in first_stage_out and d not in diffs and d not in tail_devs]
    order = {id(d): i for i, d in enumerate(signal)}
    while frontier:
        dev = frontier.pop()
        if dev in seen:
            continue
        seen.add(dev)
        load.append(dev)
        src = _d(dev, 2)
        if is_rail(src):
            continue
        for up in signal:
            if (up is not dev and up not in seen
                    and _d(up, 0) == src
                    and up.polarity == dev.polarity
                    and up not in diffs and up not in tail_devs):
                frontier.append(up)
    load.sort(key=lambda d: order.get(id(d), 0))
    return load


def _find_output_nodes(devs, ports, diffs):
    """输出节点：优先用 .subckt 端口（非电源轨、且不是任何 MOS 的栅极）。

    注意栅极/漏极只对 MOS 有意义：无源器件的 nodes[1] 只是它的第二个端子，
    若一并计入会把 `RC1 drn1 outn comp1 1k` 的 outn 误当成"被驱动的栅极"。
    """
    mos = [d for d in devs if d.dtype == "M"]
    gates = {_d(d, 1) for d in mos}
    if ports:
        outs = {p for p in ports if not is_rail(p) and p not in gates}
        if outs:
            return outs
    # 无端口信息时的兜底：是某些 MOS 的漏极、但不驱动任何 MOS 栅极
    drains = {_d(d, 0) for d in mos}
    return {n for n in drains if n and not is_rail(n) and n not in gates}


# ---------------------------------------------------------------------------
# 无源网络：补偿 / 去耦
# ---------------------------------------------------------------------------
def _add_passives(add, passives, q):
    """补偿网络（密勒/调零）与去耦电容分开报告，不再笼统归为"无源器件"。"""
    if not passives:
        return
    comp_caps, decap, others = [], [], []
    for d in passives:
        if d.dtype != "C":
            others.append(d)
            continue
        # 三端电容（如 MOM/MIM）前两个节点是信号端，其余视为屏蔽/衬底
        sig = [n for n in d.nodes[:2]]
        if any(is_rail(n) for n in sig):
            decap.append(d)
        else:
            comp_caps.append(d)

    if comp_caps:
        # 与补偿电容共节点的电阻 = 调零电阻
        comp_nodes = {n for c in comp_caps for n in c.nodes[:2]}
        nulling = [d for d in others
                   if d.dtype == "R" and comp_nodes & set(d.nodes)]
        devs_ = comp_caps + nulling
        extra = "，并与调零电阻串联（消右半平面零点）" if nulling else ""
        add("补偿网络（密勒补偿）", devs_,
            "跨接在两级之间、两端均非电源轨的电容，用于频率补偿/设置主极点"
            + extra, confidence=0.75)
        others = [d for d in others if d not in nulling]

    if decap:
        add("去耦电容", decap,
            "一端接电源轨的电容，用于电源去耦/滤波，不参与信号通路",
            confidence=0.8)

    if others:
        add("其他无源器件", others,
            "未归入补偿/去耦的电阻电感，按 PDK 参数化", confidence=0.5)


# ---------------------------------------------------------------------------
# 匹配器件
# ---------------------------------------------------------------------------
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
