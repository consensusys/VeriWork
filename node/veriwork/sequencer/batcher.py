"""L2 batch construction (paper Sec. IV, Algorithm 1).

Telemetry attestations arrive continuously; every `window_s` seconds (60 s by
default) the sequencer closes a batch, aggregates the individual Groth16 proofs
into one, and posts (state root, order root, aggregated proof, blob hash) to L1.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from ..zk.prover import Proof, ProverBackend, MockProver


@dataclass
class TaskSubmission:
    task_id: str
    worker: str
    cid: str                    # IPFS CID of the attestation payload
    twin_hash: bytes            # H_twin
    proof: Proof
    public_inputs: List[int]
    received_at: float = field(default_factory=time.time)


@dataclass
class Batch:
    batch_id: int
    submissions: List[TaskSubmission]
    state_root: bytes
    aggregated_proof: Optional[Proof]
    valid_task_ids: List[str]
    invalid_task_ids: List[str]
    closed_at: float

    @property
    def size(self) -> int:
        return len(self.submissions)


class BatchBuilder:
    def __init__(self, prover: ProverBackend = None, window_s: float = 60.0,
                 max_batch: int = 4096):
        self.prover = prover or MockProver()
        self.window_s = window_s
        self.max_batch = max_batch
        self._buf: List[TaskSubmission] = []
        self._opened_at = time.time()
        self._next_id = 1
        self._prev_root = hashlib.sha256(b"genesis").digest()

    def add(self, sub: TaskSubmission) -> None:
        self._buf.append(sub)

    def should_close(self, now: Optional[float] = None) -> bool:
        now = now or time.time()
        return (len(self._buf) >= self.max_batch
                or (self._buf and now - self._opened_at >= self.window_s))

    def close(self) -> Batch:
        subs, self._buf = self._buf, []
        valid, invalid = [], []
        for s in subs:
            ok = self.prover.verify(s.proof, s.public_inputs)
            (valid if ok else invalid).append(s.task_id)
        good = [s for s in subs if s.task_id in set(valid)]
        # state root = H(prev_root || twin hashes in canonical order)
        h = hashlib.sha256(self._prev_root)
        for s in sorted(good, key=lambda x: x.task_id):
            h.update(s.twin_hash)
        root = h.digest()
        agg = self.prover.aggregate([s.proof for s in good]) if good else None
        b = Batch(self._next_id, subs, root, agg, valid, invalid, time.time())
        self._next_id += 1
        self._prev_root = root
        self._opened_at = time.time()
        return b
