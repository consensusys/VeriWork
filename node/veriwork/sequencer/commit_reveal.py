"""MEV-resistant transaction ordering (paper Sec. III-D).

  1. Users submit commitment  c = H(tx || r)  in L2 block N.
  2. They reveal (tx, r) in block N+1.
  3. Within a batch, revealed transactions are ordered by a deterministic rule
     over commitment hashes that the batch ZK proof attests, so the sequencer
     never learns transaction contents at ordering time and cannot covertly
     reorder without being slashable.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


def commit_hash(tx_bytes: bytes, r: bytes) -> bytes:
    return hashlib.sha256(tx_bytes + b"||" + r).digest()


@dataclass(frozen=True)
class Commitment:
    commit: bytes
    sender: str
    block_committed: int


@dataclass(frozen=True)
class RevealedTx:
    commit: bytes
    tx_bytes: bytes
    r: bytes
    sender: str
    block_committed: int


class CommitRevealPool:
    def __init__(self, reveal_window_blocks: int = 1):
        self.reveal_window = reveal_window_blocks
        self._pending: Dict[bytes, Commitment] = {}
        self._revealed: Dict[bytes, RevealedTx] = {}

    # ---- client helpers -------------------------------------------------------
    @staticmethod
    def make_commitment(tx_bytes: bytes) -> Tuple[bytes, bytes]:
        r = os.urandom(32)
        return commit_hash(tx_bytes, r), r

    # ---- sequencer side -------------------------------------------------------
    def submit_commit(self, commit: bytes, sender: str, block: int) -> None:
        if commit in self._pending or commit in self._revealed:
            raise ValueError("duplicate commitment")
        self._pending[commit] = Commitment(commit, sender, block)

    def reveal(self, tx_bytes: bytes, r: bytes, sender: str, block: int) -> RevealedTx:
        c = commit_hash(tx_bytes, r)
        pend = self._pending.get(c)
        if pend is None:
            raise ValueError("no matching commitment")
        if pend.sender != sender:
            raise ValueError("reveal sender mismatch")
        if block < pend.block_committed + 1:
            raise ValueError("reveal too early: contents must stay hidden for one block")
        if block > pend.block_committed + 1 + self.reveal_window:
            raise ValueError("reveal window expired")
        rt = RevealedTx(c, tx_bytes, r, sender, pend.block_committed)
        del self._pending[c]
        self._revealed[c] = rt
        return rt

    def drain_for_batch(self) -> List[RevealedTx]:
        txs = list(self._revealed.values())
        self._revealed.clear()
        return canonical_order(txs)

    def expire(self, current_block: int) -> int:
        """Drop commitments that were never revealed. Returns count dropped."""
        stale = [c for c, p in self._pending.items()
                 if current_block > p.block_committed + 1 + self.reveal_window]
        for c in stale:
            del self._pending[c]
        return len(stale)


def canonical_order(txs: List[RevealedTx]) -> List[RevealedTx]:
    """Deterministic intra-batch order attested by the batch proof:
    ascending by (block_committed, commitment hash)."""
    return sorted(txs, key=lambda t: (t.block_committed, t.commit))


def order_root(txs: List[RevealedTx]) -> bytes:
    """Merkle-ish commitment to the ordering (simple hash chain for the reference impl)."""
    h = hashlib.sha256(b"veriwork-order-v1")
    for t in txs:
        h.update(t.commit)
    return h.digest()
