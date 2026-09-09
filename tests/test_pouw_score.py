import pytest
from veriwork.consensus import NodeStats, PoUWWeights, pouw_score, rank_nodes


def test_weights_must_sum_to_one():
    with pytest.raises(ValueError):
        PoUWWeights(0.5, 0.5, 0.5)


def test_perfect_node_scores_one():
    n = NodeStats("a", 100, 100, 50.0, 1.0)
    assert pouw_score(n, v_max=50.0, u_max=1.0) == pytest.approx(1.0)


def test_score_components():
    w = PoUWWeights(0.5, 0.3, 0.2)
    n = NodeStats("a", 50, 100, 25.0, 0.5)      # ratio .5, V/Vmax .5, U/Umax .5
    assert pouw_score(n, 50.0, 1.0, w) == pytest.approx(0.5)


def test_ranking_prefers_valid_work_over_volume():
    honest = NodeStats("honest", 90, 100, 60.0, 0.95)
    spammer = NodeStats("spam", 30, 100, 100.0, 0.99)
    ranked = rank_nodes([honest, spammer])
    assert ranked[0][0] == "honest"


def test_zero_submissions_zero_ratio():
    n = NodeStats("x", 0, 0, 10.0, 1.0)
    assert n.validity_ratio == 0.0 and n.invalid_rate == 0.0
