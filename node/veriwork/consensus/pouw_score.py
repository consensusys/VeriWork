"""PoUW score (paper Eq. 3).

    S_i = alpha * T_valid/T_submitted + beta * V_i/V_max + gamma * U_i/U_max

with alpha + beta + gamma = 1 (governance parameters).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple


@dataclass(frozen=True)
class PoUWWeights:
    alpha: float = 0.5   # weight on proof validity ratio
    beta: float = 0.3    # weight on compute volume
    gamma: float = 0.2   # weight on uptime

    def __post_init__(self) -> None:
        total = self.alpha + self.beta + self.gamma
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"alpha+beta+gamma must equal 1, got {total}")
        if min(self.alpha, self.beta, self.gamma) < 0:
            raise ValueError("weights must be non-negative")


@dataclass
class NodeStats:
    node_id: str
    tasks_valid: int          # T_i^valid  (ZK proofs that verified)
    tasks_submitted: int      # T_i^submitted
    compute_volume: float     # V_i  (e.g. GPU-seconds, task units)
    uptime: float             # U_i in [0, 1]

    @property
    def validity_ratio(self) -> float:
        if self.tasks_submitted == 0:
            return 0.0
        return self.tasks_valid / self.tasks_submitted

    @property
    def invalid_rate(self) -> float:
        """epsilon_i used by dynamic slashing (paper Eq. 1)."""
        return 1.0 - self.validity_ratio if self.tasks_submitted else 0.0


def pouw_score(stats: NodeStats, v_max: float, u_max: float,
               weights: PoUWWeights = PoUWWeights()) -> float:
    """Compute S_i for one node given epoch-wide maxima V_max and U_max."""
    v_term = stats.compute_volume / v_max if v_max > 0 else 0.0
    u_term = stats.uptime / u_max if u_max > 0 else 0.0
    score = (weights.alpha * stats.validity_ratio
             + weights.beta * v_term
             + weights.gamma * u_term)
    return max(0.0, min(1.0, score))


def rank_nodes(nodes: Iterable[NodeStats],
               weights: PoUWWeights = PoUWWeights()) -> List[Tuple[str, float]]:
    """Return [(node_id, S_i)] sorted by descending score."""
    nodes = list(nodes)
    if not nodes:
        return []
    v_max = max(n.compute_volume for n in nodes)
    u_max = max(n.uptime for n in nodes)
    scored = [(n.node_id, pouw_score(n, v_max, u_max, weights)) for n in nodes]
    scored.sort(key=lambda t: (-t[1], t[0]))
    return scored


def scores_by_id(nodes: Iterable[NodeStats],
                 weights: PoUWWeights = PoUWWeights()) -> Dict[str, float]:
    return dict(rank_nodes(nodes, weights))
