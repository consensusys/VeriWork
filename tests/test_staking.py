import pytest
from veriwork.consensus import StakingLedger, SlashingParams


def test_dynamic_slashing_formula():
    L = StakingLedger(SlashingParams(lam=0.5))
    L.bond("n1", 1000.0); L.delegate("alice", "n1", 500.0)
    slashed = L.slash_epoch("n1", epsilon=0.2)        # sigma * lambda * eps = 1500*0.1
    assert slashed == pytest.approx(150.0)
    assert L.stake_of("n1") == pytest.approx(1350.0)
    # pro-rata across own and delegated
    assert L.nodes["n1"].own == pytest.approx(900.0)
    assert L.nodes["n1"].delegations["alice"] == pytest.approx(450.0)


def test_slash_capped_per_epoch():
    L = StakingLedger(SlashingParams(lam=2.0, max_epoch_slash=0.5))
    L.bond("n1", 1000.0)
    assert L.slash_epoch("n1", 1.0) == pytest.approx(500.0)


def test_restake_cap():
    L = StakingLedger(SlashingParams(restake_cap_per_service=0.25))
    L.bond("n1", 1000.0)
    L.restake("n1", "bridge:polygon", 250.0)
    with pytest.raises(ValueError):
        L.restake("n1", "bridge:polygon", 1.0)
    L.restake("n1", "bridge:arbitrum", 250.0)     # separate service, separate cap


def test_restake_shrinks_after_slash():
    L = StakingLedger()
    L.bond("n1", 1000.0); L.restake("n1", "svc", 250.0)
    L.slash_for_ordering_violation("n1")
    assert L.nodes["n1"].restaked["svc"] <= 0.25 * L.nodes["n1"].own + 1e-9


def test_eligibility_requires_min_bond():
    L = StakingLedger(SlashingParams(min_bond=1000.0))
    L.bond("small", 999.0); L.bond("big", 1000.0)
    assert not L.is_eligible("small") and L.is_eligible("big")
