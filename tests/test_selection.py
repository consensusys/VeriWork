from collections import Counter
import pytest
from veriwork.consensus import selection_probabilities, select_committee, bft_liveness_threshold, bft_max_faulty


def test_probabilities_multiplicative():
    stake = {"a": 100.0, "b": 100.0, "c": 1000.0}
    score = {"a": 1.0, "b": 0.5, "c": 0.0}   # c: capital but no useful work
    p = selection_probabilities(stake, score)
    assert "c" not in p
    assert p["a"] == pytest.approx(2 * p["b"])
    assert sum(p.values()) == pytest.approx(1.0)


def test_reputation_multiplier_bounded():
    stake = {"a": 1.0, "b": 1.0}; score = {"a": 1.0, "b": 1.0}
    p = selection_probabilities(stake, score, reputation={"a": 10.0}, reputation_cap=1.25)
    assert p["a"] / p["b"] == pytest.approx(1.25)


def test_committee_is_deterministic_and_without_replacement():
    stake = {f"n{i}": 1000.0 for i in range(30)}
    score = {f"n{i}": 0.5 + i / 60 for i in range(30)}
    c1 = select_committee(stake, score, 20, 7, b"seed-1")
    c2 = select_committee(stake, score, 20, 7, b"seed-1")
    assert c1 == c2 and len(set(c1)) == 7
    # only top-20 by score are eligible
    top20 = sorted(score, key=lambda n: -score[n])[:20]
    assert set(c1) <= set(top20)


def test_stake_weighted_frequency():
    stake = {"rich": 900.0, "poor": 100.0}; score = {"rich": 1.0, "poor": 1.0}
    cnt = Counter(select_committee(stake, score, 2, 1, str(i).encode())[0] for i in range(2000))
    assert 0.85 < cnt["rich"] / 2000 < 0.95


def test_bft_threshold():
    # k = 7 tolerates f = 2 Byzantine sequencers and needs k - f = 5 honest ones
    assert bft_max_faulty(7) == 2 and bft_liveness_threshold(7) == 5
    assert bft_max_faulty(4) == 1 and bft_liveness_threshold(4) == 3
    for k in range(1, 50):
        f = bft_max_faulty(k)
        assert k >= 3 * f + 1 and bft_liveness_threshold(k) >= 2 * f + 1
