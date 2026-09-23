import pytest
from veriwork.sdk import LocalL2, CRITICAL_HEALTH
from veriwork.zk import mock_twin_hash, mock_input_hash, mock_bounds_hash

BOUNDS = [(0, 5), (0, 50)]                      # two sensors


def _update(l2, operator, machine, samples, health, cycle=10, prove_as=None):
    """Prove like an honest operator would (for `prove_as` machine if given) and submit."""
    th, ih = mock_twin_hash(samples, cycle, 0, health), mock_input_hash(samples)
    target = prove_as or machine
    pub = l2.expected_twin_signals(target, operator, th, ih, health, cycle)
    proof = l2.prover.prove("telemetry_attest", {"samples": samples, "bounds": l2.machines[target].bounds,
                                                 "health_score": health, "public": pub})
    return l2.submit_twin_update(machine, operator, "bafy...", th, ih, health, cycle, proof, reward_vwc=1.0)


def _l2(*machines):
    l2 = LocalL2(batch_window_s=0)
    for mid, op in machines:
        l2.register_machine(mid, op, BOUNDS)
        if op not in l2.staking.nodes:
            l2.staking.bond(op, 5000.0)
    return l2


def test_end_to_end_verify_reward_and_reject():
    l2 = _l2(("cnc-01", "w1"), ("cnc-02", "w2"))
    _update(l2, "w1", "cnc-01", [[1, 2, 1], [10, 12, 9]], health=85)
    _update(l2, "w2", "cnc-02", [[1, 9], [10, 12]], health=20)          # 9 > hi of sensor 0
    batch = l2.close_batch()
    assert l2.twin_state("cnc-01").health_score == 85
    assert l2.twin_state("cnc-02") is None
    assert l2.balances["w1"] == 1.0 and "w2" not in l2.balances
    assert l2.bad_proofs["w2"] == 1 and any(e["event"] == "InvalidProof" for e in l2.events)
    assert batch.aggregated_proof is not None


def test_per_sensor_bounds_are_enforced():
    l2 = _l2(("m", "w1"))
    _update(l2, "w1", "m", [[1, 2], [40, 49]], health=90)               # 49 is fine for sensor 1 ...
    l2.close_batch()
    assert l2.twin_state("m") is not None
    _update(l2, "w1", "m", [[1, 2], [51, 3]], health=90, cycle=11)      # ... 51 is not
    l2.close_batch()
    assert l2.bad_proofs["w1"] == 1


def test_proof_replayed_on_another_machine_is_recorded_invalid():
    l2 = _l2(("a", "w1"), ("b", "w1"))
    _update(l2, "w1", "b", [[1], [1]], health=90, prove_as="a")          # proof made for machine a
    l2.close_batch()
    assert l2.twin_state("b") is None and l2.bad_proofs["w1"] == 1


def test_only_the_operator_updates_and_cycles_do_not_go_back():
    l2 = _l2(("a", "w1"))
    with pytest.raises(PermissionError):
        _update(l2, "w2", "a", [[1], [1]], health=90)
    _update(l2, "w1", "a", [[1], [1]], health=90, cycle=10)
    l2.close_batch()
    with pytest.raises(ValueError, match="Stale"):
        _update(l2, "w1", "a", [[1], [1]], health=90, cycle=9)
    _update(l2, "w1", "a", [[2], [2]], health=90, cycle=10)             # idle window is fine
    l2.close_batch()
    assert l2.good_proofs["w1"] == 2


def test_maintenance_threshold_is_40():
    assert CRITICAL_HEALTH == 40
    l2 = _l2(("press-07", "w1"), ("press-08", "w1"))
    _update(l2, "w1", "press-07", [[1], [1]], health=39)
    _update(l2, "w1", "press-08", [[1], [1]], health=40)
    l2.close_batch()
    assert l2.twin_state("press-07").maintenance_flag and not l2.twin_state("press-08").maintenance_flag
    assert [e["machine_id"] for e in l2.events if e["event"] == "MaintenanceTriggered"] == ["press-07"]


def test_task_proof_is_bound_to_its_worker():
    l2 = LocalL2(batch_window_s=0)
    ih, bh = 111, mock_bounds_hash(BOUNDS)
    task = l2.submit_task(ih, bh, reward_vwc=2.0)
    pub = l2.expected_task_signals(task.task_id, "w1", 777, 88, 5)
    proof = l2.prover.prove("telemetry_attest", {"samples": [[1], [1]], "bounds": BOUNDS,
                                                 "health_score": 88, "public": pub})
    l2.submit_result(task.task_id, "thief", "bafy", 777, 88, 5, proof)     # copied proof
    l2.close_batch()
    assert l2.task_status(task.task_id) == "open" and l2.bad_proofs["thief"] == 1
    l2.submit_result(task.task_id, "w1", "bafy", 777, 88, 5, proof)
    l2.close_batch()
    assert l2.task_status(task.task_id) == "verified" and l2.balances["w1"] == 2.0


def test_epoch_slashes_faulty_worker_and_elects_committee():
    l2 = LocalL2(batch_window_s=0, k_eligible=2, committee_size=2)   # top-2 by S_i are eligible
    for w in ("w1", "w2", "w3"):
        l2.staking.bond(w, 5000.0)
        for i in range(5):
            l2.register_machine(f"{w}-m{i}", w, BOUNDS)
    for i in range(5):
        _update(l2, "w1", f"w1-m{i}", [[1], [1]], 90)
        _update(l2, "w2", f"w2-m{i}", [[1], [1]], 90)
        _update(l2, "w3", f"w3-m{i}", [[99], [1]], 90)                   # always out of bounds
    l2.close_batch()
    committee = l2.advance_epoch()
    assert l2.staking.stake_of("w3") < 5000.0
    assert "w3" not in committee and len(committee) == 2


def test_credit_swap_quote_matches_execution():
    l2 = LocalL2(batch_window_s=0)
    l2.register_credit("FXA", 1000.0, 2000.0, 0.5)
    l2.register_credit("FXB", 3000.0, 1000.0, 0.3)
    q = l2.quote("FXA", "FXB", 100.0)
    got = l2.swap_credits("FXA", "FXB", 100.0)
    assert abs(q - got) < 1e-9
