"""FlexFactory orchestration agent (paper Code Snippet 2, Algorithm 2).

Each tick (one L2 block):
  1. read all twin states, inventory and recent L2 events
  2. ask the planner for {maintenance, procurement, rebalance}
  3. execute: escrow VWC for maintenance, issue x402-paid procurement orders,
     redistribute workload -- subject to the safety rails of Sec. VIII-D.

On-chain, FlexFactoryTwinRegistry enforces the same rails regardless of what
the agent does (whitelisted agents and service providers, a per-agent daily
spending cap, and human review of orders above a threshold).  `AgentSafetyRails`
mirrors them client-side so that the in-process LocalL2, which has no escrow
contract, exercises the same behaviour.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .llm import Planner, RuleBasedPlanner
from .x402_client import X402Client
from .erc8004 import AgentIdentity


@dataclass
class AgentSafetyRails:
    max_escrow_per_order: float = 500.0      # above this => human review (humanReviewThreshold)
    daily_budget_vwc: float = 50_000.0       # dailyAgentCap
    whitelisted_providers: List[str] = field(default_factory=lambda: ["svc-provider-0x02"])
    whitelisted_suppliers: List[str] = field(default_factory=lambda: ["SUP-B"])


class FactoryAgent:
    def __init__(self, l2, model, agent_wallet: str, budget_vwc: float = 50_000.0,
                 planner: Optional[Planner] = None, rails: Optional[AgentSafetyRails] = None,
                 sim=None, identity: Optional[AgentIdentity] = None):
        self.l2 = l2
        self.model = model
        self.wallet = agent_wallet
        self.budget = budget_vwc
        self.planner = planner or RuleBasedPlanner()
        self.rails = rails or AgentSafetyRails()
        self.sim = sim                       # optional: closes the loop by repairing machines
        self.identity = identity or AgentIdentity.local(agent_wallet)
        self.x402 = X402Client(payer=agent_wallet)
        self.inventory: Dict[str, int] = {"bearing-6205": 40, "spindle-belt": 15, "hydraulic-seal": 8}
        self.open_orders: Dict[str, Dict[str, Any]] = {}
        self.pending_human_review: List[Dict[str, Any]] = []
        self.log: List[Dict[str, Any]] = []
        self._last_block = 0
        self.spent_today = 0.0

    # ---- one control step ----------------------------------------------------
    def tick(self) -> int:
        """Sense, plan, act: once per L2 block."""
        orders = self.open_orders
        twins = []
        for t in self.l2.all_twin_states():
            twins.append({
                "machine_id": t.machine_id,
                "health_score": t.health_score,
                "maintenance_flag":
                    t.maintenance_flag,
                "cycle_count": t.cycle_count,
                "order_open":
                    t.machine_id in orders,
            })
        events = self.l2.recent_events(
            self._last_block)
        self._last_block = self.l2.block
        # LLM or rule-based planner; malformed LLM
        # output falls back to the rule-based plan
        plan = self.planner.plan(
            twins, self.inventory, events)

        acted = 0
        for m in plan.get("maintenance", []):
            acted += self._schedule_maintenance(
                m["machineId"],
                m["serviceProvider"],
                float(m["escrowVWC"]))
        for p in plan.get("procurement", []):
            self._issue_procurement(
                p["supplierId"], p["partSKU"],
                int(p["quantity"]),
                float(p["maxPrice"]))
        for r in plan.get("rebalance", []):
            self.log.append({
                "action": "rebalance", **r,
                "t": time.time()})
        self._complete_finished_orders()
        return int(acted)

    # ---- actions ---------------------------------------------------------------
    def _schedule_maintenance(
            self, machine_id: str, provider: str,
            escrow: float) -> bool:
        """Mirror of the on-chain rails of the
        twin registry (scheduleMaintenance)."""
        rails = self.rails
        allowed = rails.whitelisted_providers
        if provider not in allowed:
            why = "provider not whitelisted"
            return self._reject(machine_id, why)
        if escrow > rails.max_escrow_per_order:
            # held for human review (on-chain:
            # PendingReview, then reviewOrder)
            self.pending_human_review.append(
                {"machine": machine_id,
                 "provider": provider,
                 "escrow": escrow})
            return False
        if (self.spent_today + escrow
                > rails.daily_budget_vwc
                or escrow > self.budget):
            return self._reject(machine_id,
                                "budget")
        self.budget -= escrow
        self.spent_today += escrow
        self.open_orders[machine_id] = {
            "provider": provider,
            "escrow": escrow,
            "block": self.l2.block}
        self.l2._emit("MaintenanceScheduled",
                      machine_id=machine_id,
                      provider=provider,
                      escrow=escrow,
                      agent=self.wallet)
        self.log.append({
            "action": "schedule_maintenance",
            "machine": machine_id,
            "escrow": escrow})
        return True

    def _reject(self, machine_id: str,
                reason: str) -> bool:
        self.log.append({"action": "maintenance_rejected",
                         "reason": reason,
                         "machine": machine_id})
        return False

    def _issue_procurement(self, supplier: str, sku: str, qty: int, max_price: float) -> None:
        if supplier not in self.rails.whitelisted_suppliers:
            return
        # pay the supplier's quote endpoint over x402 (HTTP 402 -> stablecoin/VWC settle)
        receipt = self.x402.pay(f"https://{supplier.lower()}.suppliers.veriwork.io/order/{sku}",
                                amount=qty * max_price, asset="VWC")
        self.inventory[sku] = self.inventory.get(sku, 0) + qty
        self.log.append({"action": "procure", "supplier": supplier, "sku": sku, "qty": qty, "x402_receipt": receipt.tx_hash})

    def _complete_finished_orders(self) -> None:
        done = []
        for mid, o in self.open_orders.items():
            if self.l2.block - o["block"] >= 3:          # service window elapsed
                if self.sim is not None:
                    self.sim.repair(mid)
                self.l2._emit("MaintenanceCompleted", machine_id=mid, provider=o["provider"], paid=o["escrow"])
                done.append(mid)
        for mid in done:
            del self.open_orders[mid]
