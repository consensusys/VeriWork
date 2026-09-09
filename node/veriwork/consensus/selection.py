"""Stake- and contribution-weighted sequencer selection (paper Eq. 2).

    P(n_i) = sigma_i * S_i / sum_j sigma_j * S_j

VeriWork instantiates this as a hybrid: the top-k nodes by S_i form the
eligibility set, and committee seats are drawn from that set by stake-weighted
sortition, seeded by a verifiable epoch randomness beacon.
"""
from __future__ import annotations

import hashlib
from typing import Dict, List, Mapping, Optional


def selection_probabilities(stake: Mapping[str, float],
                            score: Mapping[str, float],
                            reputation: Optional[Mapping[str, float]] = None,
                            reputation_cap: float = 1.25) -> Dict[str, float]:
    """Return the PoAW election distribution over nodes present in both maps.

    A bounded reputation multiplier (<= reputation_cap) is applied to S_i for
    nodes with long histories of valid proofs; it improves liveness without
    materially concentrating selection.
    """
    weights: Dict[str, float] = {}
    for nid in stake:
        if nid not in score:
            continue
        rep = 1.0
        if reputation and nid in reputation:
            rep = min(reputation_cap, max(1.0, reputation[nid]))
        w = max(0.0, stake[nid]) * max(0.0, score[nid]) * rep
        if w > 0:
            weights[nid] = w
    total = sum(weights.values())
    if total == 0:
        return {}
    return {k: v / total for k, v in weights.items()}


def _prng_stream(seed: bytes):
    """Deterministic floats in [0,1) derived from a beacon seed (SHA-256 counter mode)."""
    counter = 0
    while True:
        h = hashlib.sha256(seed + counter.to_bytes(8, "big")).digest()
        counter += 1
        yield int.from_bytes(h[:8], "big") / float(1 << 64)


def select_committee(stake: Mapping[str, float],
                     score: Mapping[str, float],
                     k_eligible: int,
                     committee_size: int,
                     epoch_seed: bytes,
                     reputation: Optional[Mapping[str, float]] = None) -> List[str]:
    """Hybrid PoAW committee selection.

    1. Eligibility set  = top-k_eligible nodes by PoUW score S_i (with sigma_i > 0).
    2. Committee        = committee_size nodes drawn *without replacement* from the
                          eligibility set with probability proportional to sigma_i * S_i.

    The draw is deterministic given epoch_seed, so every node can recompute it.
    """
    eligible = sorted(
        (nid for nid in score if stake.get(nid, 0) > 0 and score[nid] > 0),
        key=lambda n: (-score[n], n),
    )[:k_eligible]
    if not eligible:
        return []
    pool = {n: stake[n] for n in eligible}
    pool_score = {n: score[n] for n in eligible}
    rng = _prng_stream(epoch_seed)
    committee: List[str] = []
    while pool and len(committee) < committee_size:
        probs = selection_probabilities(pool, pool_score, reputation)
        r = next(rng)
        acc = 0.0
        chosen = None
        for nid in sorted(probs):  # deterministic iteration order
            acc += probs[nid]
            if r < acc:
                chosen = nid
                break
        if chosen is None:
            chosen = sorted(probs)[-1]
        committee.append(chosen)
        del pool[chosen]
        del pool_score[chosen]
    return committee


def bft_liveness_threshold(committee_size: int) -> int:
    """Honest sequencers required for liveness: floor((k-1)/3) + 1 (paper Sec. VII-B)."""
    return (committee_size - 1) // 3 + 1
