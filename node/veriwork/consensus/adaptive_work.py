"""Adaptive work model (paper Sec. III-A).

Per-epoch work quotas are set by a governance-tunable controller that reacts to
task backlog, proof latency and the credit-price signal.  When the task market is
congested, consensus weight shifts towards application tasks; when demand is
slack, idle capacity is directed to standing scientific / infrastructure
workloads so that useful output still backs participation.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class NetworkSignals:
    backlog: int              # unassigned application tasks
    capacity: int             # tasks the network can verify per epoch
    proof_latency_s: float    # mean proof-generation latency this epoch
    price_ratio: float        # P_c / P_0 from the adaptive pricing controller


@dataclass
class WorkQuota:
    app_task_share: float          # fraction of consensus work reserved for app tasks
    standing_task_share: float
    difficulty_multiplier: float   # scales proof-size / batch-size targets

    def __post_init__(self):
        assert abs(self.app_task_share + self.standing_task_share - 1) < 1e-9


class AdaptiveWorkController:
    """Proportional controller with clamped output; stateless between epochs."""

    def __init__(self, base_app_share: float = 0.6, k_backlog: float = 0.3,
                 k_price: float = 0.1, target_latency_s: float = 8.0,
                 min_app_share: float = 0.3, max_app_share: float = 0.95):
        self.base_app_share = base_app_share
        self.k_backlog = k_backlog
        self.k_price = k_price
        self.target_latency_s = target_latency_s
        self.min_app_share = min_app_share
        self.max_app_share = max_app_share

    def quota(self, sig: NetworkSignals) -> WorkQuota:
        congestion = sig.backlog / sig.capacity if sig.capacity else 0.0
        share = (self.base_app_share
                 + self.k_backlog * (congestion - 1.0)
                 + self.k_price * (sig.price_ratio - 1.0))
        share = max(self.min_app_share, min(self.max_app_share, share))
        # Slow provers -> lower per-task difficulty so batches still close inside
        # the telemetry window; fast provers -> allow larger circuits.
        diff = self.target_latency_s / sig.proof_latency_s if sig.proof_latency_s > 0 else 1.0
        diff = max(0.5, min(2.0, diff))
        return WorkQuota(app_task_share=share,
                         standing_task_share=1.0 - share,
                         difficulty_multiplier=diff)
