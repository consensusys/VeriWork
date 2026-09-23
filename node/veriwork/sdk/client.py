"""Python SDK (paper Sec. IV-B, 'SDK and Development Tools').

`VeriWorkClient` is the interface AI agents and dApps use: twin updates, task
submission, proof-status polling, twin-state queries, credit swaps and agent
hooks.

`LocalL2` is an in-process implementation backed by the reference node
modules, so agents and the FlexFactory simulator run end-to-end without a
live chain.  It mirrors the on-chain rules of `FlexFactoryTwinRegistry` and
`TaskRegistry`: the verifier's public inputs are rebuilt from L2 state (prior
state root, registered bounds, machine / task id, submitter) rather than taken
from the submitter, only a machine's registered operator may update it, cycle
counts must increase, and failed proofs are recorded against the submitter
(epsilon_i) instead of being dropped.  Proofs are checked when the batch
closes (sequencer-side verification).
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..consensus import StakingLedger, NodeStats, scores_by_id, select_committee
from ..market import BancorCurve, AdaptivePricer
from ..sequencer import BatchBuilder, TaskSubmission, CommitRevealPool
from ..zk import (MockProver, Proof, BN254_FIELD, machine_field, submitter_field, mock_bounds_hash,
                  telemetry_public_signals)

CRITICAL_HEALTH = 40          # FlexFactoryTwinRegistry.CRITICAL_HEALTH


@dataclass
class TwinState:
    machine_id: str
    state_root: bytes          # 32-byte big-endian field element (H_twin)
    cycle_count: int
    health_score: int          # 0-100
    last_updated: float
    maintenance_flag: bool


@dataclass
class Machine:
    machine_id: str
    operator: str
    bounds: Tuple[Tuple[int, int], ...]   # per-sensor (lo, hi)
    bounds_hash: int                      # value registered on-chain
    field_id: int                         # machineId as a field element


@dataclass
class Task:
    task_id: str
    input_hash: int            # H_in
    bounds_hash: int
    deadline: float
    reward_vwc: float
    status: str = "open"       # open | submitted | verified | expired
    worker: Optional[str] = None
    output_hash: Optional[int] = None


class VeriWorkClient:  # interface
    def register_machine(self, machine_id: str, operator: str, bounds, bounds_hash: Optional[int] = None): ...
    def expected_twin_signals(self, machine_id: str, operator: str, twin_hash: int, input_hash: int,
                              health_score: int, cycle_count: int) -> List[int]: ...
    def submit_twin_update(self, machine_id: str, operator: str, cid: str, twin_hash: int, input_hash: int,
                           health_score: int, cycle_count: int, proof: Proof, reward_vwc: float = 0.0) -> str: ...
    def submit_task(self, input_hash: int, bounds_hash: int, reward_vwc: float, deadline_s: float) -> Task: ...
    def submit_result(self, task_id: str, worker: str, cid: str, output_hash: int, health_score: int,
                      cycle_count: int, proof: Proof) -> None: ...
    def task_status(self, task_id: str) -> str: ...
    def twin_state(self, machine_id: str) -> Optional[TwinState]: ...
    def all_twin_states(self) -> List[TwinState]: ...
    def swap_credits(self, from_credit: str, to_credit: str, amount: float) -> float: ...
    def quote(self, from_credit: str, to_credit: str, amount: float) -> float: ...


def task_field(task_id: str) -> int:
    """Value in C_twin's machineId slot for a stand-alone task (TaskRegistry.circuitTaskId)."""
    return int.from_bytes(hashlib.sha256(b"task:" + task_id.encode()).digest(), "big") % BN254_FIELD


class LocalL2(VeriWorkClient):
    """In-process rollup: PoAW election + batching + verification + credit market."""

    def __init__(self, batch_window_s: float = 60.0, k_eligible: int = 20,
                 committee_size: int = 7, prover=None):
        self.prover = prover or MockProver()
        self.staking = StakingLedger()
        self.batcher = BatchBuilder(self.prover, window_s=batch_window_s)
        self.mempool = CommitRevealPool()
        self.pricer = AdaptivePricer()
        self.curves: Dict[str, BancorCurve] = {}
        self.machines: Dict[str, Machine] = {}
        self.tasks: Dict[str, Task] = {}
        self.twins: Dict[str, TwinState] = {}
        self.node_stats: Dict[str, NodeStats] = {}
        self.balances: Dict[str, float] = {}
        self.good_proofs: Dict[str, int] = {}
        self.bad_proofs: Dict[str, int] = {}
        self.block = 0
        self.epoch = 0
        self.committee: List[str] = []
        self.k_eligible, self.committee_size = k_eligible, committee_size
        self.events: List[Dict[str, Any]] = []
        self._pending: Dict[str, Dict[str, Any]] = {}      # submission id -> what to apply if valid
        self._pending_machines: set = set()

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

    # ---- twin registry (mirrors FlexFactoryTwinRegistry) -------------------------
    def register_machine(self, machine_id: str, operator: str, bounds,
                         bounds_hash: Optional[int] = None) -> Machine:
        if machine_id in self.machines:
            raise ValueError("Registered")
        b = tuple((int(lo), int(hi)) for lo, hi in
                  ([bounds] if not isinstance(bounds[0], (list, tuple)) else bounds))
        bh = mock_bounds_hash(b) if bounds_hash is None else int(bounds_hash)
        m = Machine(machine_id, operator, b, bh, machine_field(machine_id))
        self.machines[machine_id] = m
        return m

    def _root_field(self, machine_id: str) -> int:
        t = self.twins.get(machine_id)
        return int.from_bytes(t.state_root, "big") if t else 0

    def expected_twin_signals(self, machine_id: str, operator: str, twin_hash: int, input_hash: int,
                              health_score: int, cycle_count: int) -> List[int]:
        """The eight public signals updateTwinState rebuilds -- the prover must use these."""
        m = self.machines[machine_id]
        return telemetry_public_signals(twin_hash, input_hash, m.bounds_hash, self._root_field(machine_id),
                                        health_score, cycle_count, m.field_id, submitter_field(operator))

    def submit_twin_update(self, machine_id: str, operator: str, cid: str, twin_hash: int, input_hash: int,
                           health_score: int, cycle_count: int, proof: Proof, reward_vwc: float = 0.0) -> str:
        m = self.machines.get(machine_id)
        if m is None or m.operator != operator:
            raise PermissionError("Not operator")
        t = self.twins.get(machine_id)
        if int(cycle_count) < (t.cycle_count if t else 0):
            raise ValueError("Stale update")
        if machine_id in self._pending_machines:
            raise ValueError("update already pending for this machine in the open batch")
        public = self.expected_twin_signals(machine_id, operator, twin_hash, input_hash,
                                            health_score, cycle_count)
        sid = f"twin:{machine_id}:{cycle_count}"
        self.batcher.add(TaskSubmission(sid, operator, cid, int(twin_hash).to_bytes(32, "big"), proof, public))
        self._pending[sid] = dict(kind="twin", machine_id=machine_id, worker=operator, root=int(twin_hash),
                                  health=int(health_score), cycle=int(cycle_count), reward=reward_vwc)
        self._pending_machines.add(machine_id)
        self.node_stats.setdefault(operator, NodeStats(operator, 0, 0, 0.0, 1.0))
        return sid

    def twin_state(self, machine_id: str) -> Optional[TwinState]:
        return self.twins.get(machine_id)

    def all_twin_states(self) -> List[TwinState]:
        return list(self.twins.values())

    # ---- generic task market (mirrors TaskRegistry) ------------------------------
    def submit_task(self, input_hash: int, bounds_hash: int, reward_vwc: float,
                    deadline_s: float = 120.0) -> Task:
        tid = hashlib.sha256(f"{input_hash}:{time.time_ns()}:{len(self.tasks)}".encode()).hexdigest()[:16]
        t = Task(tid, int(input_hash), int(bounds_hash), time.time() + deadline_s, reward_vwc)
        self.tasks[tid] = t
        return t

    def expected_task_signals(self, task_id: str, worker: str, output_hash: int,
                              health_score: int, cycle_count: int) -> List[int]:
        t = self.tasks[task_id]
        return telemetry_public_signals(output_hash, t.input_hash, t.bounds_hash, 0, health_score,
                                        cycle_count, task_field(task_id), submitter_field(worker))

    def submit_result(self, task_id: str, worker: str, cid: str, output_hash: int, health_score: int,
                      cycle_count: int, proof: Proof) -> None:
        t = self.tasks[task_id]
        if t.status != "open":
            raise ValueError("not open")
        if time.time() > t.deadline:
            raise ValueError("late")
        public = self.expected_task_signals(task_id, worker, output_hash, health_score, cycle_count)
        sid = f"task:{task_id}:{worker}:{len(self._pending)}"
        self.batcher.add(TaskSubmission(sid, worker, cid, int(output_hash).to_bytes(32, "big"), proof, public))
        self._pending[sid] = dict(kind="task", task_id=task_id, worker=worker, root=int(output_hash))
        t.status = "submitted"
        self.node_stats.setdefault(worker, NodeStats(worker, 0, 0, 0.0, 1.0))

    def task_status(self, task_id: str) -> str:
        return self.tasks[task_id].status

    # ---- chain progression -----------------------------------------------------
    def close_batch(self):
        """Close the current window: verify each proof against the rebuilt public
        inputs, apply valid updates, record failures against the submitter."""
        batch = self.batcher.close()
        for sid in batch.valid_task_ids:
            p = self._pending.pop(sid)
            w = p["worker"]
            st = self.node_stats[w]
            st.tasks_valid += 1; st.tasks_submitted += 1; st.compute_volume += 1.0
            self.good_proofs[w] = self.good_proofs.get(w, 0) + 1
            if p["kind"] == "twin":
                mid, hs = p["machine_id"], p["health"]
                crit = hs < CRITICAL_HEALTH
                self.twins[mid] = TwinState(mid, p["root"].to_bytes(32, "big"), p["cycle"], hs, time.time(), crit)
                self.balances[w] = self.balances.get(w, 0.0) + p["reward"]
                self._emit("TwinUpdated", machine_id=mid, health_score=hs)
                if crit:
                    self._emit("MaintenanceTriggered", machine_id=mid, operator=w)
            else:
                t = self.tasks[p["task_id"]]
                t.status, t.worker, t.output_hash = "verified", w, p["root"]
                self.balances[w] = self.balances.get(w, 0.0) + t.reward_vwc
                self._emit("TaskVerified", task_id=t.task_id, worker=w)
        for sid in batch.invalid_task_ids:
            p = self._pending.pop(sid)
            w = p["worker"]
            self.node_stats[w].tasks_submitted += 1          # raises epsilon_i
            self.bad_proofs[w] = self.bad_proofs.get(w, 0) + 1
            if p["kind"] == "twin":
                self._emit("InvalidProof", machine_id=p["machine_id"], submitter=w)
            else:
                self.tasks[p["task_id"]].status = "open"     # re-queued, like TaskRegistry
                self._emit("TaskRejected", task_id=p["task_id"], worker=w)
        self._pending_machines.clear()
        self.block += 1
        return batch

    def advance_epoch(self, seed: Optional[bytes] = None) -> List[str]:
        """Apply dynamic slashing from this epoch's verification outcomes and
        elect the next committee (Eq. 1-3)."""
        for nid, st in self.node_stats.items():
            if nid in self.staking.nodes and st.tasks_submitted:
                self.staking.slash_epoch(nid, st.invalid_rate)
        scores = scores_by_id(self.node_stats.values())
        eligible_stake = {n: s for n, s in self.staking.stake_map().items() if self.staking.is_eligible(n)}
        seed = seed or hashlib.sha256(f"epoch-{self.epoch}".encode()).digest()
        self.committee = select_committee(eligible_stake, scores,
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
