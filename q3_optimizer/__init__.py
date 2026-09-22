"""第③问优化器包：参数文件读写、仿真适配、可行性优先多目标优化、赛题评分复现。"""

from .optimize import (Constraints, ObjectiveWeights, ParamSpace,  # noqa: F401
                       differential_evolution, plan_budget, score)
from .paramfile import (ParamCodec, ParamFile, build_bounds,       # noqa: F401
                        format_value, free_variables, load_variables_csv,
                        parse_value)
from .scoring import (ScoreBreakdown, compute_area, constraint_score,  # noqa: F401
                      objective_score, rank_candidates, refs_from_results,
                      runtime_score, total_score)
from .simulator import (CommandSimulator, MockSimulator, SimResult,  # noqa: F401
                        parse_metrics)

__all__ = [
    "Constraints", "ObjectiveWeights", "ParamSpace", "differential_evolution",
    "plan_budget", "score", "ParamCodec", "ParamFile", "build_bounds",
    "format_value", "free_variables", "load_variables_csv", "parse_value",
    "CommandSimulator", "MockSimulator", "SimResult", "parse_metrics",
    "ScoreBreakdown", "compute_area", "constraint_score", "objective_score",
    "rank_candidates", "refs_from_results", "runtime_score", "total_score",
]
