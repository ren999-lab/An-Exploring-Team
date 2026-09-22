"""第③问：基于约束的多目标尺寸优化。

## 为什么是"可行性优先"而不是加权惩罚

赛题 6.3(2) 是**全有或全无**：PM / GM / I_OPA 三项，任何一项在**任意 PVT** 下不满足，
该项直接扣满 10 分。而 6.3(3) 的 DCGain/UGB/Area 是按名次比例给分。
所以最优策略不是"在目标上多拿 3 分、但把约束弄崩"，而是：

    **先保证 100% 可行，再在可行域内优化目标。**

因此选择算子采用可行性优先规则（Deb 的约束支配思想），只要有可行解，
就绝不接受不可行解；两个都不可行时，比较归一化违反度。

## 多目标处理

评分是 DCGain / UGB / Area **分别排名**再按比例给分，不是加权求和。
但优化器最终只能交一个方案，所以：
* 搜索时用一个可配置的标量化分数驱动方向；
* 同时维护 **Pareto 存档**，把非支配解全部落盘，赛后/需要时可人工挑选，
  也可以拿来写报告里的"权衡曲线"。
"""

import json
import math
import random
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

# 赛题硬约束（PDF 6.3）
PM_MIN_DEG = 50.0
GM_MAX_DB = -10.0
I_OPA_MAX_A = 3e-3
DCGAIN_MIN_DB = 40.0


@dataclass
class Constraints:
    pm_min: float = PM_MIN_DEG
    gm_max: float = GM_MAX_DB
    i_opa_max: float = I_OPA_MAX_A
    dc_gain_min: float = DCGAIN_MIN_DB
    ugb_min: float = None

    def check(self, res):
        """返回违反项列表（空列表 = 满足全部约束）。缺测指标视为违反。"""
        bad = []
        if res is None or not res.ok:
            return ["仿真失败或无有效结果"]
        if res.pm_deg is None or res.pm_deg < self.pm_min:
            bad.append(f"PM {_fmt(res.pm_deg, 'deg')} < {self.pm_min}")
        if res.gm_db is None or res.gm_db > self.gm_max:
            bad.append(f"GM {_fmt(res.gm_db, 'dB')} > {self.gm_max}")
        if res.i_opa_a is None or res.i_opa_a > self.i_opa_max:
            bad.append(f"I_OPA {_fmt(res.i_opa_a, 'A')} > {self.i_opa_max}")
        if res.dc_gain_db is None or res.dc_gain_db < self.dc_gain_min:
            bad.append(f"DCGain {_fmt(res.dc_gain_db, 'dB')} < {self.dc_gain_min}")
        if self.ugb_min is not None and (res.ugb_hz is None or res.ugb_hz < self.ugb_min):
            bad.append(f"UGB {_fmt(res.ugb_hz, 'Hz')} < {self.ugb_min}")
        return bad

    def feasible(self, res):
        return not self.check(res)

    def violation(self, res):
        """归一化总违反度（0 = 可行），用于两个不可行解之间的比较。"""
        if res is None or not res.ok:
            return 1e6
        v = 0.0
        if res.pm_deg is None:
            v += 1.0
        elif res.pm_deg < self.pm_min:
            v += (self.pm_min - res.pm_deg) / (abs(self.pm_min) or 1)
        if res.gm_db is None:
            v += 1.0
        elif res.gm_db > self.gm_max:
            v += (res.gm_db - self.gm_max) / (abs(self.gm_max) or 1)
        if res.i_opa_a is None:
            v += 1.0
        elif res.i_opa_a > self.i_opa_max:
            v += (res.i_opa_a - self.i_opa_max) / self.i_opa_max
        if res.dc_gain_db is None:
            v += 1.0
        elif res.dc_gain_db < self.dc_gain_min:
            v += (self.dc_gain_min - res.dc_gain_db) / self.dc_gain_min
        if self.ugb_min is not None:
            if res.ugb_hz is None:
                v += 1.0
            elif res.ugb_hz < self.ugb_min:
                v += (self.ugb_min - res.ugb_hz) / self.ugb_min
        return v


def _fmt(x, unit):
    return "None" if x is None else f"{x:.4g}{unit}"


@dataclass
class ObjectiveWeights:
    """标量化权重（仅用于驱动搜索方向；真实评分是分别排名）。"""
    gain: float = 10.0
    ugb: float = 15.0
    area: float = 15.0
    gain_ref_db: float = 40.0
    ugb_ref_hz: float = 1e6
    area_ref_um2: float = 100.0


def score(res, w: ObjectiveWeights = None):
    """越大越好。DCGain/UGB 取对数收益，Area 取对数代价。"""
    w = w or ObjectiveWeights()
    if res is None or not res.ok:
        return -1e9
    s = 0.0
    if res.dc_gain_db is not None:
        s += w.gain * (res.dc_gain_db - w.gain_ref_db) / w.gain_ref_db
    if res.ugb_hz and res.ugb_hz > 0:
        s += w.ugb * math.log10(res.ugb_hz / w.ugb_ref_hz)
    if res.area_um2 and res.area_um2 > 0:
        s -= w.area * math.log10(res.area_um2 / w.area_ref_um2)
    return s


# ---------------------------------------------------------------------------
# 参数空间
# ---------------------------------------------------------------------------
@dataclass
class ParamSpace:
    names: list
    lo: list
    hi: list
    is_int: list

    def clip(self, vec):
        out = []
        for v, a, b, ii in zip(vec, self.lo, self.hi, self.is_int):
            v = min(max(v, a), b)
            out.append(int(round(v)) if ii else float(v))
        return out

    def random(self, rnd):
        return self.clip([rnd.uniform(a, b) for a, b in zip(self.lo, self.hi)])

    def dim(self):
        return len(self.names)


# ---------------------------------------------------------------------------
# 预算规划（3 小时总时限倒推）
# ---------------------------------------------------------------------------
def plan_budget(total_seconds, per_eval_seconds, reserve=0.2, pop_size=None,
                min_pop=6):
    """由单次仿真耗时倒推可用评估次数与推荐种群规模。

    留 `reserve` 比例做余量（VPN 掉线、反标、全 PVT 验证都要时间）。
    """
    usable = max(0.0, total_seconds * (1.0 - reserve))
    if per_eval_seconds <= 0:
        raise ValueError("per_eval_seconds 必须为正（先测一次真实仿真耗时）")
    n_eval = int(usable // per_eval_seconds)
    if pop_size is None:
        # 小维度下 6~12 的种群足够；评估次数很少时缩小种群以保证有若干代
        pop_size = max(min_pop, min(12, max(min_pop, n_eval // 4)))
    generations = n_eval // max(pop_size, 1)
    return {"usable_seconds": usable, "per_eval_seconds": per_eval_seconds,
            "max_evaluations": n_eval, "pop_size": pop_size,
            "generations": generations}


# ---------------------------------------------------------------------------
# 差分进化（可行性优先）
# ---------------------------------------------------------------------------
def _is_better(cand_res, cand_vio, tgt_res, tgt_vio, cons, weights):
    """可行性优先选择：可行 > 不可行；都可行比目标；都不可行比违反度。"""
    cf, tf = cand_vio <= 0, tgt_vio <= 0
    if cf and not tf:
        return True
    if tf and not cf:
        return False
    if cf and tf:
        return score(cand_res, weights) > score(tgt_res, weights)
    return cand_vio < tgt_vio


def differential_evolution(evaluate, space, x0, budget, cons=None,
                           weights=None, F=0.7, CR=0.9, seed=0,
                           logger=None, checkpoint_path=None, resume=True,
                           wall_clock_budget=None, on_improve=None):
    """可行性优先的 DE/rand/1/bin。

    evaluate(vec) -> SimResult
    返回 {'best_x', 'best_result', 'history', 'n_evals', 'pareto'}

    每个个体的适应度随种群一起缓存，**目标个体不会重复仿真**——
    这一点很重要：每次仿真都很贵，重复评估会直接浪费预算。
    """
    cons = cons or Constraints()
    weights = weights or ObjectiveWeights()
    rnd = random.Random(seed)
    ckpt = Path(checkpoint_path) if checkpoint_path else None

    state = {"generation": 0, "population": None, "fitness": None,
             "vios": None, "best_x": None, "best": None, "best_vio": None,
             "n_evals": 0, "history": [], "pareto": []}

    if resume and ckpt and ckpt.exists():
        try:
            saved = json.loads(ckpt.read_text(encoding="utf-8"))
            if saved.get("space_names") == space.names:
                for k in state:
                    if k in saved:
                        state[k] = saved[k]
        except Exception:
            pass

    def snapshot():
        if ckpt:
            ckpt.parent.mkdir(parents=True, exist_ok=True)
            payload = dict(state)
            payload["space_names"] = space.names
            ckpt.write_text(json.dumps(payload, ensure_ascii=False),
                            encoding="utf-8")

    def record_pareto(x, res):
        if not cons.feasible(res):
            return
        d = res.as_dict()
        for e in state["pareto"]:
            if _dominates(e["result"], d):
                return
        state["pareto"] = [e for e in state["pareto"]
                           if not _dominates(d, e["result"])]
        state["pareto"].append({"x": list(x), "result": d})

    t_start = time.time()

    def time_left():
        if wall_clock_budget is None:
            return True
        return (time.time() - t_start) < wall_clock_budget

    def budget_left():
        return state["n_evals"] < budget["max_evaluations"]

    if state["population"] is None:
        pop = [space.clip(list(x0))]
        while len(pop) < budget["pop_size"]:
            v = space.random(rnd)
            if v not in pop:
                pop.append(v)
        state["population"] = pop
        state["fitness"] = []
        state["vios"] = []
        for x in pop:
            res = evaluate(list(x))
            state["n_evals"] += 1
            state["fitness"].append(res.as_dict())
            v = cons.violation(res)
            state["vios"].append(v)
            record_pareto(x, res)
            if logger:
                logger({"event": "init", "x": list(x), "vio": v,
                        **res.as_dict()})
        best_i = min(range(len(pop)),
                     key=lambda i: (state["vios"][i] > 0,
                                    state["vios"][i],
                                    -score(_Res(state["fitness"][i]), weights)))
        state["best_x"] = list(pop[best_i])
        state["best"] = state["fitness"][best_i]
        state["best_vio"] = state["vios"][best_i]
        snapshot()

    pop_size = len(state["population"])
    while budget_left() and time_left():
        state["generation"] += 1
        population = [list(x) for x in state["population"]]
        for i in range(pop_size):
            if not budget_left() or not time_left():
                break
            idxs = [j for j in range(pop_size) if j != i]
            if len(idxs) < 3:
                break
            a, b, c = (population[rnd.choice(idxs)] for _ in range(3))
            mutant = space.clip([a[k] + F * (b[k] - c[k])
                                 for k in range(space.dim())])
            trial = space.clip([mutant[k] if rnd.random() < CR
                                else population[i][k]
                                for k in range(space.dim())])

            cand_res = evaluate(trial)
            state["n_evals"] += 1
            cand_vio = cons.violation(cand_res)
            tgt_res = _Res(state["fitness"][i])
            tgt_vio = state["vios"][i]

            if _is_better(cand_res, cand_vio, tgt_res, tgt_vio, cons, weights):
                population[i] = trial
                state["fitness"][i] = cand_res.as_dict()
                state["vios"][i] = cand_vio
                record_pareto(trial, cand_res)
                if _is_better(cand_res, cand_vio, _Res(state["best"]),
                              state["best_vio"], cons, weights):
                    state["best_x"] = list(trial)
                    state["best"] = cand_res.as_dict()
                    state["best_vio"] = cand_vio
                    if logger:
                        logger({"event": "improve", "gen": state["generation"],
                                "x": list(trial), **cand_res.as_dict()})
                    if on_improve:
                        on_improve(list(trial), cand_res)
            if logger:
                logger({"event": "eval", "gen": state["generation"],
                        "n": state["n_evals"], "x": list(trial),
                        "vio": cand_vio, **cand_res.as_dict()})
        state["population"] = population
        state["history"].append({"gen": state["generation"],
                                 "n_evals": state["n_evals"],
                                 "best": state["best"],
                                 "best_vio": state["best_vio"]})
        snapshot()

    snapshot()
    return {"best_x": state["best_x"], "best_result": state["best"],
            "best_violation": state["best_vio"], "n_evals": state["n_evals"],
            "generations": state["generation"], "pareto": state["pareto"],
            "history": state["history"]}


class _Res:
    """把 dict 还原成能喂给 Constraints / score 的轻量对象。"""
    def __init__(self, d):
        self.__dict__.update(d or {})
        self.ok = bool((d or {}).get("ok"))


def _as_result(d):
    return _Res(d) if d else None


def _dominates(a: dict, b: dict) -> bool:
    """a 是否支配 b：DCGain/UGB 越大越好，Area 越小越好。"""
    def get(d, k):
        v = d.get(k)
        return None if v is None else float(v)
    better_or_equal = True
    strictly = False
    for k, sign in (("dc_gain_db", 1), ("ugb_hz", 1), ("area_um2", -1)):
        av, bv = get(a, k), get(b, k)
        if av is None or bv is None:
            continue
        if sign * av < sign * bv:
            better_or_equal = False
            break
        if sign * av > sign * bv:
            strictly = True
    return better_or_equal and strictly
