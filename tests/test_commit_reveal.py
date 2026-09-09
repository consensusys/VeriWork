import pytest
from veriwork.sequencer import CommitRevealPool, canonical_order


def test_reveal_requires_one_block_delay():
    pool = CommitRevealPool()
    tx = b"swap 500 FXA->FXB"
    c, r = pool.make_commitment(tx)
    pool.submit_commit(c, "alice", block=10)
    with pytest.raises(ValueError):
        pool.reveal(tx, r, "alice", block=10)      # same block: contents hidden
    pool.reveal(tx, r, "alice", block=11)


def test_wrong_secret_or_sender_rejected():
    pool = CommitRevealPool()
    tx = b"tx"; c, r = pool.make_commitment(tx)
    pool.submit_commit(c, "alice", 1)
    with pytest.raises(ValueError):
        pool.reveal(tx, b"\x00" * 32, "alice", 2)
    with pytest.raises(ValueError):
        pool.reveal(tx, r, "mallory", 2)


def test_order_is_independent_of_reveal_order():
    p1, p2 = CommitRevealPool(), CommitRevealPool()
    items = []
    for i in range(20):
        tx = f"tx{i}".encode(); c, r = CommitRevealPool.make_commitment(tx)
        items.append((tx, c, r))
        p1.submit_commit(c, "u", 1); p2.submit_commit(c, "u", 1)
    for tx, c, r in items:
        p1.reveal(tx, r, "u", 2)
    for tx, c, r in reversed(items):
        p2.reveal(tx, r, "u", 2)
    o1 = [t.commit for t in p1.drain_for_batch()]
    o2 = [t.commit for t in p2.drain_for_batch()]
    assert o1 == o2 == sorted(c for _, c, _ in items)


def test_expiry():
    pool = CommitRevealPool(reveal_window_blocks=1)
    c, _ = pool.make_commitment(b"x"); pool.submit_commit(c, "u", 5)
    assert pool.expire(current_block=7) == 0
    assert pool.expire(current_block=8) == 1
