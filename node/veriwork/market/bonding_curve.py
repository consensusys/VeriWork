"""Bancor-style bonding curve for application-credit price discovery (paper Sec. V-C).

  W_a = B_a / T_m,   T_m = P_m * N_m
  P   = B_a / (O_m * W_a)
  buy  (Eq. 6):  CE_a = (1 + T_a/B_a)^W_a - 1,       I_a  = O_m * CE_a   (credit issued)
  sell (Eq. 7):  CE_p = 1 - (1 - R_a/O_m)^(1/W_a),   Paid = B_a * CE_p   (VWC returned)

Names follow the paper: B_a reserve balance (in main credit, VWC), O_m
outstanding supply of the application credit (S_a in Eq. 6-7), W_a reserve
weight, T_a VWC paid in, R_a application credit sold back.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BancorCurve:
    reserve_balance: float     # B_a  (VWC held in reserve)
    supply: float              # O_m  (application credit outstanding)
    weight: float              # W_a  in (0, 1]

    def __post_init__(self):
        if not 0 < self.weight <= 1:
            raise ValueError("reserve weight must be in (0,1]")
        if self.reserve_balance <= 0 or self.supply <= 0:
            raise ValueError("reserve and supply must be positive")

    @property
    def price(self) -> float:
        """Spot price P = B_a / (O_m * W_a) in VWC per app credit."""
        return self.reserve_balance / (self.supply * self.weight)

    def buy(self, vwc_in: float) -> float:
        """Deposit `vwc_in` VWC; returns app credit minted.
        S_a = O_m * ((1 + T_a/B_a)^W_a - 1)."""
        if vwc_in <= 0:
            raise ValueError("amount must be positive")
        ce = (1 + vwc_in / self.reserve_balance) ** self.weight - 1
        minted = self.supply * ce
        self.reserve_balance += vwc_in
        self.supply += minted
        return minted

    def sell(self, credit_in: float) -> float:
        """Burn `credit_in` app credit; returns VWC paid out.
        Paid = B_a * (1 - (1 - S/O_m)^(1/W_a))."""
        if credit_in <= 0 or credit_in > self.supply:
            raise ValueError("invalid sell amount")
        ce = 1 - (1 - credit_in / self.supply) ** (1 / self.weight)
        paid = self.reserve_balance * ce
        self.reserve_balance -= paid
        self.supply -= credit_in
        return paid

    def quote_swap(self, other: "BancorCurve", credit_in: float) -> float:
        """Quote credit_a -> VWC -> credit_b without mutating either curve."""
        a = BancorCurve(self.reserve_balance, self.supply, self.weight)
        b = BancorCurve(other.reserve_balance, other.supply, other.weight)
        return b.buy(a.sell(credit_in))

    def swap(self, other: "BancorCurve", credit_in: float) -> float:
        """Atomic swap credit_a -> credit_b via the main credit (VWC)."""
        return other.buy(self.sell(credit_in))
