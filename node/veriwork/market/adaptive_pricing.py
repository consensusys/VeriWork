"""Adaptive demand-responsive pricing layered on the bonding curve (paper Eq. 5).

    P_c = P_0 * (1 + alpha_p * D_r / S_r)

D_r is *excess* (unmet) task demand and S_r the verified compute supply, both
time-weighted over the pricing window.  Writing r = demand/supply this gives
    r >= 1 :  m = 1 + alpha_p * (r - 1)          (surge, capped)
    r <  1 :  m = max(floor, 1 - alpha_p * (1 - r))   (mild deflation)
so a balanced market (r = 1) trades exactly at the bonding-curve price P_0.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Tuple


@dataclass
class PricingWindow:
    seconds: float = 900.0     # 15-minute TWAP window


class AdaptivePricer:
    def __init__(self, alpha_p: float = 0.35, window: PricingWindow = PricingWindow(),
                 surge_cap: float = 3.0, deflation_floor: float = 0.8):
        self.alpha_p = alpha_p
        self.window = window
        self.surge_cap = surge_cap
        self.deflation_floor = deflation_floor
        self._obs: Deque[Tuple[float, float, float]] = deque()  # (t, demand, supply)

    def observe(self, t: float, demand: float, supply: float) -> None:
        self._obs.append((t, max(0.0, demand), max(1e-9, supply)))
        cutoff = t - self.window.seconds
        while self._obs and self._obs[0][0] < cutoff:
            self._obs.popleft()

    def demand_supply_ratio(self) -> float:
        """Time-weighted D_r / S_r over the window."""
        if len(self._obs) < 2:
            return self._obs[-1][1] / self._obs[-1][2] if self._obs else 0.0
        num = den = 0.0
        for (t0, d, s), (t1, _, _) in zip(self._obs, list(self._obs)[1:]):
            w = max(1e-9, t1 - t0)
            num += w * d / s
            den += w
        return num / den if den else 0.0

    def multiplier(self) -> float:
        r = self.demand_supply_ratio()
        if r >= 1.0:
            return min(self.surge_cap, 1.0 + self.alpha_p * (r - 1.0))
        return max(self.deflation_floor, 1.0 - self.alpha_p * (1.0 - r))

    def price(self, p0: float) -> float:
        """Effective credit price P_c given bonding-curve base price P_0."""
        return p0 * self.multiplier()
