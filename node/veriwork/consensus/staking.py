"""Multi-layer staking and dynamic slashing (paper Sec. III-B).

Layers:
  * economic bond layer      -- own bonded VWC
  * delegation layer         -- VWC delegated by non-operating holders
  * restaking layer          -- share of the bond re-pledged to secure bridges /
                                services, capped per service to bound correlated loss

Dynamic slashing (paper Eq. 1):  sigma_i <- sigma_i * (1 - lambda * epsilon_i)
where epsilon_i is the epoch invalid-submission rate derived from on-chain ZK
verification outcomes (objective, no vote needed).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class SlashingParams:
    lam: float = 0.5                # lambda: penalty coefficient
    max_epoch_slash: float = 0.5    # never slash more than this fraction per epoch
    restake_cap_per_service: float = 0.25   # <= 25% of own bond per external service
    min_bond: float = 1000.0        # minimum own bond to be sequencer-eligible


@dataclass
class NodeStake:
    own: float = 0.0
    delegations: Dict[str, float] = field(default_factory=dict)   # delegator -> amount
    restaked: Dict[str, float] = field(default_factory=dict)      # service -> amount

    @property
    def delegated(self) -> float:
        return sum(self.delegations.values())

    @property
    def total(self) -> float:
        """sigma_i = own + delegated (used in Eq. 2)."""
        return self.own + self.delegated


class StakingLedger:
    def __init__(self, params: SlashingParams = SlashingParams()):
        self.params = params
        self.nodes: Dict[str, NodeStake] = {}
        self.slash_log: List[Tuple[str, float, float]] = []   # (node, epsilon, amount)

    # ---- bonding & delegation -------------------------------------------------
    def bond(self, node: str, amount: float) -> None:
        if amount <= 0:
            raise ValueError("bond must be positive")
        self.nodes.setdefault(node, NodeStake()).own += amount

    def delegate(self, delegator: str, node: str, amount: float) -> None:
        if amount <= 0:
            raise ValueError("delegation must be positive")
        if node not in self.nodes:
            raise KeyError(f"unknown node {node}")
        d = self.nodes[node].delegations
        d[delegator] = d.get(delegator, 0.0) + amount

    def restake(self, node: str, service: str, amount: float) -> None:
        ns = self.nodes[node]
        cap = self.params.restake_cap_per_service * ns.own
        current = ns.restaked.get(service, 0.0)
        if current + amount > cap + 1e-9:
            raise ValueError(
                f"restake for {service} would exceed per-service cap {cap:.2f}")
        ns.restaked[service] = current + amount

    # ---- views -----------------------------------------------------------------
    def stake_of(self, node: str) -> float:
        return self.nodes[node].total if node in self.nodes else 0.0

    def stake_map(self) -> Dict[str, float]:
        return {n: s.total for n, s in self.nodes.items()}

    def is_eligible(self, node: str) -> bool:
        return node in self.nodes and self.nodes[node].own >= self.params.min_bond

    # ---- dynamic slashing (Eq. 1) ---------------------------------------------
    def slash_epoch(self, node: str, epsilon: float) -> float:
        """Apply sigma <- sigma * (1 - lambda * epsilon). Returns amount slashed.

        The penalty is applied pro-rata across own stake and delegations so that
        delegators share the risk of the operator they chose (paper Sec. VII-D).
        """
        if not 0.0 <= epsilon <= 1.0:
            raise ValueError("epsilon must be in [0,1]")
        ns = self.nodes[node]
        frac = min(self.params.max_epoch_slash, self.params.lam * epsilon)
        if frac <= 0 or ns.total <= 0:
            return 0.0
        slashed = ns.total * frac
        ns.own *= (1 - frac)
        for d in ns.delegations:
            ns.delegations[d] *= (1 - frac)
        # restaked positions cannot exceed the (now smaller) bond
        cap = self.params.restake_cap_per_service * ns.own
        for svc in ns.restaked:
            ns.restaked[svc] = min(ns.restaked[svc], cap)
        self.slash_log.append((node, epsilon, slashed))
        return slashed

    def slash_for_ordering_violation(self, node: str) -> float:
        """Deviating from the ZK-attested batch order is treated as epsilon = 1."""
        return self.slash_epoch(node, 1.0)
