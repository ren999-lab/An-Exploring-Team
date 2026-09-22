"""赛题评分口径的本地复现：面积公式 + 6.3 的名次打分规则。

**为什么要有这一层**

优化器里的 `score()` 只是为了驱动搜索方向（一个标量化目标）；
而赛题真正打分的是 6.3 的规则：约束项全有或全无、目标项按名次比例给分。
两者不是一回事——搜索时"多拿一点 UGB"可能划算，评分时"少丢一个约束"价值 10 分。
把这个口径单独复现出来，好处是：

1. 本地就能回答"我们现在这个解大概能拿几分"，不必等提交后才知道；
2. 报告里可以直接给出量化自评，而不是只堆仿真曲线；
3. 多个候选解之间可以**按真实评分规则**排序，而不是按搜索用的标量化分数排序。

评分规则（赛题 PDF 6.3）：

    约束项 30 分：PM ≥ 50deg、GM ≤ -10dB、I_OPA ≤ 3mA 各 10 分；
                 任一在任意 PVT 不满足 -> 该项 10 分归零
    目标项 40 分：DC Gain < 40dB 记 0 分；否则：
                     DCGain = (Gain1 / Gain0) * 10
                     UGB    = (UGB1 / UGB0) * 15      （需 DCGain ≥ 40dB）
                     Area   = (A0 / A1) * 15          （需 DCGain ≥ 40dB）
    运行时间 10 分：T0 / T1 * 10，每有一项约束不满足再扣 5 分

其中带 0 下标的是**该单项做得最好的队伍**的成绩（自评时用我们自己的最优解代替，
用于横向比较不同候选方案）。
"""

from dataclasses import dataclass, field

# 评分项常量
CONSTRAINT_POINTS = {"PM": 10.0, "GM": 10.0, "I_OPA": 10.0}
DCGAIN_FLOOR_DB = 40.0          # 低于此值 DC Gain 项直接 0 分
GAIN_POINTS = 10.0
UGB_POINTS = 15.0
AREA_POINTS = 15.0
RUNTIME_POINTS = 10.0
UNMET_CONSTRAINT_PENALTY = 5.0  # 运行时间项每项约束不满足扣分

# 面积公式系数（赛题 6.3 注）
AREA_FACTOR = {"M": 1.5, "C": 1.0, "R": 2.0}


def _get(res, name):
    if res is None:
        return None
    if isinstance(res, dict):
        return res.get(name)
    return getattr(res, name, None)


# ---------------------------------------------------------------------------
# 面积模型
# ---------------------------------------------------------------------------
def compute_area(devices, cap_f_per_um2=2e-15, res_ohm_per_sq=200.0):
    """按赛题公式计算总面积（返回 um^2）。

    赛题规定：晶体管 f*W*L*m*1.5、电容 W*L*1、电阻 W*L*2。

    `devices` 为 {器件名: {参数: 值}} 的映射，键名大小写不敏感的常见写法都支持
    （w/W/width、l/L/length、m/mult/multiplier、seg/segments、value…）。
    长度单位统一按 **um** 解释（与 variables.csv、param_file 一致）。

    对于只给数值、没有几何尺寸的电容/电阻（赛方 Public 电路就是这种），
    按等效面积估算，并在返回的 notes 里标注为估算项——**避免与真实面积口径混淆**。
    返回 (area_um2, notes)；notes 列出被估算的设备，报告里应如实注明。
    """
    total = 0.0
    notes = []

    def pick(p, *names):
        for n in names:
            if n in p and p[n] is not None:
                try:
                    return float(p[n])
                except (TypeError, ValueError):
                    continue
        return None

    for name, params in (devices or {}).items():
        if not isinstance(params, dict):
            continue
        upper = str(name).upper()
        w = pick(params, "w", "W", "width")
        l = pick(params, "l", "L", "length")
        m = pick(params, "m", "M", "mult", "multiplier", "f", "fingers")
        seg = pick(params, "seg", "segments")
        value = pick(params, "value", "val")

        if upper.startswith("R"):
            if w and l:
                total += w * l * (seg or 1) * AREA_FACTOR["R"]
            elif value:
                # R = Rsh * L / W，取 W = 1um 反推 L，再按 W*L*2 计
                l_um = value / res_ohm_per_sq
                total += 1.0 * l_um * AREA_FACTOR["R"]
                notes.append(f"{name}: 由阻值估算面积（假设 W=1um）")
            continue

        if upper.startswith("C"):
            if w and l:
                total += w * l * AREA_FACTOR["C"]
            elif value:
                total += (value / cap_f_per_um2)
                notes.append(f"{name}: 由容值估算面积（Cox={cap_f_per_um2:g}F/um^2）")
            continue

        # 其余视为晶体管：f*W*L*m*1.5
        if w and l:
            total += w * l * (m or 1) * AREA_FACTOR["M"]
        else:
            notes.append(f"{name}: 缺少 W/L，未计入面积")
    return total, notes


# ---------------------------------------------------------------------------
# 6.3 评分
# ---------------------------------------------------------------------------
@dataclass
class ScoreBreakdown:
    constraint_score: float = 0.0
    objective_score: float = 0.0
    runtime_score: float = 0.0
    total: float = 0.0
    detail: dict = field(default_factory=dict)

    def as_dict(self):
        return {"constraint_score": round(self.constraint_score, 2),
                "objective_score": round(self.objective_score, 2),
                "runtime_score": round(self.runtime_score, 2),
                "total": round(self.total, 2),
                "detail": self.detail}


def constraint_score(res, pm_min=50.0, gm_max=-10.0, i_opa_max=3e-3, corners=None):
    """约束项得分（0~30）。缺测指标视为不满足。

    `corners` 为 {corner名: 指标dict} 时，按"任意 corner 不满足即该项归零"判定，
    与赛题"所有 PVT 均需满足"一致；不传则只判主结果。
    """
    detail = {}
    score = 0.0

    def judge(key, value, ok):
        pts = CONSTRAINT_POINTS[key] if ok else 0.0
        detail[key] = {"value": value, "passed": bool(ok), "points": pts}
        return pts

    # 主结果 + 各 corner：任意一项不满足即该项 0 分
    def worst(values):
        return any(not v for v in values)

    pm_vals, gm_vals, i_vals = [], [], []
    if corners:
        for cname, m in corners.items():
            pm_vals.append((_get_dict(m, "pm_deg"), cname))
            gm_vals.append((_get_dict(m, "gm_db"), cname))
            i_vals.append((_get_dict(m, "i_opa_a"), cname))

    pm = _get(res, "pm_deg")
    gm = _get(res, "gm_db")
    iopa = _get(res, "i_opa_a")
    pm_ok = pm is not None and pm >= pm_min
    gm_ok = gm is not None and gm <= gm_max
    iopa_ok = iopa is not None and iopa <= i_opa_max
    if corners:
        pm_ok = pm_ok and not worst([v is not None and v >= pm_min for v, _ in pm_vals])
        gm_ok = gm_ok and not worst([v is not None and v <= gm_max for v, _ in gm_vals])
        iopa_ok = iopa_ok and not worst([v is not None and v <= i_opa_max for v, _ in i_vals])

    detail["PM"] = {"value": pm, "rule": f">= {pm_min}deg", "passed": pm_ok,
                    "points": CONSTRAINT_POINTS["PM"] if pm_ok else 0.0}
    detail["GM"] = {"value": gm, "rule": f"<= {gm_max}dB", "passed": gm_ok,
                    "points": CONSTRAINT_POINTS["GM"] if gm_ok else 0.0}
    detail["I_OPA"] = {"value": iopa, "rule": f"<= {i_opa_max}A", "passed": iopa_ok,
                       "points": CONSTRAINT_POINTS["I_OPA"] if iopa_ok else 0.0}
    score = sum(d["points"] for d in detail.values())
    return score, detail


def _get_dict(m, key):
    if isinstance(m, dict):
        return m.get(key)
    return getattr(m, key, None)


def objective_score(res, refs, area_um2=None):
    """目标项得分（0~40）。

    refs = {"dc_gain_db": Gain0, "ugb_hz": UGB0, "area_um2": A0}
    —— 带 0 下标的是各单项最优秀的成绩（自评时可用自己的最优解）。
    """
    detail = {}
    gain = _get(res, "dc_gain_db")
    ugb = _get(res, "ugb_hz")
    area = area_um2 if area_um2 is not None else _get(res, "area_um2")
    score = 0.0

    if gain is None or gain < DCGAIN_FLOOR_DB:
        detail["DCGain"] = {"value": gain, "points": 0.0,
                            "note": f"< {DCGAIN_FLOOR_DB}dB 该项记 0 分"}
        return score, detail          # 未过门槛，UGB / Area 项也不计分

    g0 = refs.get("dc_gain_db") or gain
    pts = min(GAIN_POINTS * gain / g0, GAIN_POINTS)
    score += pts
    detail["DCGain"] = {"value": gain, "points": round(pts, 3), "ref": g0}

    if ugb:
        u0 = refs.get("ugb_hz") or ugb
        pts = min(UGB_POINTS * ugb / u0, UGB_POINTS)
        score += pts
        detail["UGB"] = {"value_hz": ugb, "points": round(pts, 3), "ref_hz": u0}

    if area:
        a0 = refs.get("area_um2") or area
        pts = min(AREA_POINTS * a0 / area, AREA_POINTS)
        score += pts
        detail["Area"] = {"value_um2": area, "points": round(pts, 3), "ref_um2": a0}

    return score, detail


def runtime_score(elapsed_s, fastest_s, unmet_constraints=0):
    """运行时间项（0~10）：T0/T1*10，每项约束不满足扣 5 分。"""
    if not elapsed_s or not fastest_s or elapsed_s <= 0 or fastest_s <= 0:
        base = 0.0
    else:
        base = min(RUNTIME_POINTS * fastest_s / elapsed_s, RUNTIME_POINTS)
    return max(base - UNMET_CONSTRAINT_PENALTY * unmet_constraints, 0.0)


def total_score(res, refs, elapsed_s=None, fastest_s=None, corners=None,
                area_um2=None):
    """总分（0~80 = 约束 30 + 目标 40 + 时间 10）。"""
    c_score, c_detail = constraint_score(res, corners=corners)
    o_score, o_detail = objective_score(res, refs, area_um2=area_um2)
    unmet = sum(1 for v in c_detail.values() if not v["passed"])
    t_score = runtime_score(elapsed_s, fastest_s, unmet) if elapsed_s and fastest_s else 0.0
    return ScoreBreakdown(
        constraint_score=c_score, objective_score=o_score, runtime_score=t_score,
        total=c_score + o_score + t_score,
        detail={"constraints": c_detail, "objectives": o_detail,
                "unmet_constraints": unmet},
    )


def refs_from_results(results, areas=None):
    """从一组可行解里取各单项最优，作为自评基准（模拟"冠军队成绩"）。"""
    res = [r for r in results if r is not None]
    if not res:
        return {}
    gains = [_get(r, "dc_gain_db") for r in res]
    ugbs = [_get(r, "ugb_hz") for r in res]
    if areas is None:
        areas = [_get(r, "area_um2") for r in res]
    gains = [g for g in gains if g is not None]
    ugbs = [u for u in ugbs if u is not None]
    areas = [a for a in areas if a]
    refs = {}
    if gains:
        refs["dc_gain_db"] = max(gains)
    if ugbs:
        refs["ugb_hz"] = max(ugbs)
    if areas:
        refs["area_um2"] = min(areas)
    return refs


def rank_candidates(results, areas=None, elapsed=None):
    """按赛题评分规则给多个候选解排序，返回 [(ScoreBreakdown, res), ...]（优到劣）。

    与优化器内部的标量化分数不同，这里就是**真实评分口径**，
    用于最终"交哪个方案"的决策。
    """
    refs = refs_from_results(results, areas=areas)
    scored = []
    for i, r in enumerate(results):
        area = areas[i] if areas else _get(r, "area_um2")
        sb = total_score(r, refs, elapsed_s=(elapsed[i] if elapsed else None),
                         fastest_s=(min(elapsed) if elapsed else None),
                         area_um2=area)
        scored.append((sb, r))
    return sorted(scored, key=lambda t: t[0].total, reverse=True)
