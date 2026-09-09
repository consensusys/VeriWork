import pytest
from veriwork.market import BancorCurve, AdaptivePricer


def test_spot_price_formula():
    c = BancorCurve(reserve_balance=1000.0, supply=2000.0, weight=0.5)
    assert c.price == pytest.approx(1000.0 / (2000.0 * 0.5))


def test_buy_then_sell_roundtrip_is_lossless_up_to_fp():
    c = BancorCurve(1000.0, 2000.0, 0.5)
    minted = c.buy(100.0)
    paid = c.sell(minted)
    assert paid == pytest.approx(100.0, rel=1e-9)
    assert c.reserve_balance == pytest.approx(1000.0) and c.supply == pytest.approx(2000.0)


def test_buying_raises_price():
    c = BancorCurve(1000.0, 2000.0, 0.5)
    p0 = c.price; c.buy(500.0)
    assert c.price > p0


def test_higher_weight_is_flatter():
    a = BancorCurve(1000.0, 2000.0, 0.2); b = BancorCurve(1000.0, 2000.0, 0.8)
    pa, pb = a.price, b.price
    a.buy(500.0); b.buy(500.0)
    assert (a.price / pa) > (b.price / pb)


def test_swap_conserves_vwc_reserves():
    a = BancorCurve(1000.0, 2000.0, 0.5); b = BancorCurve(3000.0, 1000.0, 0.3)
    total_before = a.reserve_balance + b.reserve_balance
    a.swap(b, 100.0)
    assert a.reserve_balance + b.reserve_balance == pytest.approx(total_before)


def test_adaptive_pricing_neutral_surge_and_cap():
    p = AdaptivePricer(alpha_p=0.35, surge_cap=3.0)
    for t in range(0, 600, 60):
        p.observe(float(t), demand=100.0, supply=100.0)
    assert p.price(1.0) == pytest.approx(1.0)          # balanced market = curve price
    for t in range(600, 1200, 60):
        p.observe(float(t), demand=200.0, supply=100.0)
    assert 1.0 < p.price(1.0) <= 1.35
    for t in range(600, 1200, 60):
        p.observe(float(t), demand=10_000.0, supply=100.0)
    assert p.price(1.0) <= 3.0


def test_adaptive_pricing_deflation_floor():
    p = AdaptivePricer(alpha_p=0.35, deflation_floor=0.8)
    for t in range(0, 600, 60):
        p.observe(float(t), demand=0.0, supply=100.0)
    assert 0.8 <= p.price(1.0) < 1.0
