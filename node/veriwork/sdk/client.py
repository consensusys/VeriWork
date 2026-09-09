"""Python SDK (paper Sec. IV-B, 'SDK and Development Tools').

`VeriWorkClient` is the interface AI agents and dApps use: task submission,
proof-status polling, twin-state queries, credit swaps and agent hooks.

`LocalL2` is an in-process implementation backed by the reference node
modules, so agents and the FlexFactory simulator run end-to-end without a
live chain.  A JSON-RPC implementation against a deployed rollup exposes the
same methods.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ..consensus import StakingLedger, NodeStats, scores_by_id, select_committee
from ..market import BancorCurve, AdaptivePricer
from ..sequencer import BatchBuilder, TaskSubmission, CommitRevealPool
from ..zk import MockProver, Proof, twin_state_hash, field_elem


@dataclass
class TwinState:
    machine_id: str
    state_root: bytes
    cycle_count: int
    health_score: int          # 0-100
    last_updated: float
    maintenance_flag: bool


@dataclass
class Task:
    task_id: str
    machine_id: str
    bounds: Sequence[float]
    deadline: float
    reward_vwc: float
    status: str = "open"       # open | submitted | verified | rejected
    worker: Optional[str] = None


class VeriWorkClient:  # interface
    def submit_task(self, machine_id: str, bounds, reward_vwc: float, deadline_s: float) -> Task: ...
    def submit_result(self, task_id: str, worker: str, cid: str, twin_hash: bytes,
                      proof: Proof, public_inputs: Sequence[int], **twin_fields) -> None: ...
    def task_status(self, task_id: str) -> str: ...
    def twin_state(self, machine_id: str) -> Optional[TwinState]: ...
    def all_twin_states(self) -> List[TwinState]: ...
    def swap_credits(self, from_credit: str, to_credit: str, amount: float) -> float: ...
    def quote(self, from_credit: str, to_credit: str, amount: float) -> float: ...


class LocalL2(VeriWorkClient):
    """In-process rollup: PoAW election + batching + verification + credit market."""

    def __init__(self, batch_window_s: float = 60.0, k_eligible: int = 20,
                 committee_size: int = 7):
        self.prover = MockProver()
        self.staking = StakingLedger()
        self.batcher = BatchBuilder(self.prover, window_s=batch_window_s)
        self.mempool = CommitRevealPool()
        self.pricer = AdaptivePricer()
        self.curves: Dict[str, BancorCurve] = {}
        self.tasks: Dict[str, Task] = {}
        self.twins: Dict[str, TwinState] = {}
        self.node_stats: Dict[str, NodeStats] = {}
        self.balances: Dict[str, float] = {}
        self.block = 0
        self.epoch = 0
        self.committee: List[str] = []
        self.k_eligible, self.committee_size = k_eligible, committee_size
        self.events: List[Dict[str, Any]] = []
        self._pending_twin: Dict[str, Dict[str, Any]] = {}

    # ---- credits ---------------------------------------------------------------
    def register_credit(self, name: str, reserve: float, supply: float, weight: float) -> None:
        self.curves[name] = BancorCurve(reserve, supply, weight)

    def quote(self, from_credit: str, to_credit: str, amount: float) -> float:
        return self.curves[from_credit].quote_swap(self.curves[to_credit], amount)

    def swap_credits(self, from_credit: str, to_credit: str, amount: float) -> float:
        out = self.curves[from_credit].swap(self.curves[to_credit], amount)
        self._emit("CreditSwapped", frm=from_credit, to=to_credit, amount=amount, out=out)
        return out

    def credit_price(self, name: str) -> float:
        return self.pricer.price(self.curves[name].price)

    # ---- tasks & twins ---------------------------------------------------------
    def submit_task(self, machine_id: str, bounds, reward_vwc: float,
                    deadline_s: float = 120.0) -> Task:
        tid = hashlib.sha256(f"{machine_id}:{time.time_ns()}:{len(self.tasks)}".encode()).hexdigest()[:16]
        t = Task(tid, machine_id, tuple(bounds), time.time() + deadline_s, reward_vwc)
        self.tasks[tid] = t
        return t

    def submit_result(self, task_id: str, worker: str, cid: str, twin_hash: bytes,
                      proof: Proof, public_inputs: Sequence[int], **twin_fields) -> None:
        t = self.tasks[task_id]
        if t.status != "open":
            raise ValueError("task not open")
        t.status, t.worker = "submitted", worker
        self.batcher.add(TaskSubmission(task_id, worker, cid, twin_hash, proof, list(public_inputs)))
        self._pending_twin[task_id] = dict(machine_id=t.machine_id, root=twin_hash, **twin_fields)
        self.node_stats.setdefault(worker, NodeStats(worker, 0, 0, 0.0, 1.0))

    def task_status(self, task_id: str) -> str:
        return self.tasks[task_id].status

    def twin_state(self, machine_id: str) -> Optional[TwinState]:
        return self.twins.get(machine_id)

    def all_twin_states(self) -> List[TwinState]:
        return list(self.twins.values())

    # ---- chain progression -----------------------------------------------------
    def close_batch(self):
        """Close the current telemetry window: verify proofs, credit rewards,
        update PoUW stats, publish twin states (Algorithm 1)."""
        batch = self.batcher.close()
        for tid in batch.valid_task_ids:
            t = self.tasks[tid]; t.status = "verified"
            st = self.node_stats[t.worker]
            st.tasks_valid += 1; st.tasks_submitted += 1; st.compute_volume += 1.0
            self.balances[t.worker] = self.balances.get(t.worker, 0.0) + t.reward_vwc
            p = self._pending_twin.pop(tid)
            hs = int(p.get("health_score", 100))
            self.twins[p["machine_id"]] = TwinState(
                p["machine_id"], p["root"], int(p.get("cycle_count", 0)), hs, time.time(), hs < 30)
            self._emit("TwinUpdated", machine_id=p["machine_id"], health_score=hs)
            if hs < 30:
                self._emit("MaintenanceTriggered", machine_id=p["machine_id"])
        for tid in batch.invalid_task_ids:
            t = self.tasks[tid]; t.status = "rejected"
            st = self.node_stats[t.worker]; st.tasks_submitted += 1
            self._pending_twin.pop(tid, None)
            self._emit("ProofRejected", task_id=tid, worker=t.worker)
        self.block += 1
        return batch

    def advance_epoch(self, seed: Optional[bytes] = None) -> List[str]:
        """Apply dynamic slashing from this epoch's stats and elect the next committee."""
        for nid, st in self.node_stats.items():
            if nid in self.staking.nodes and st.tasks_submitted:
                self.staking.slash_epoch(nid, st.invalid_rate)
        scores = scores_by_id(self.node_stats.values())
        seed = seed or hashlib.sha256(f"epoch-{self.epoch}".encode()).digest()
        self.committee = select_committee(self.staking.stake_map(), scores,
                                          self.k_eligible, self.committee_size, seed)
        self.epoch += 1
        for st in self.node_stats.values():          # reset epoch counters
            st.tasks_valid = st.tasks_submitted = 0
        return self.committee

    # ---- events ----------------------------------------------------------------
    def _emit(self, name: str, **kw) -> None:
        self.events.append({"event": name, "block": self.block, "t": time.time(), **kw})

    def recent_events(self, since_block: int) -> List[Dict[str, Any]]:
        return [e for e in self.events if e["block"] >= since_block]
