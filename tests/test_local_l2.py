from veriwork.sdk import LocalL2
from veriwork.zk import twin_state_hash, field_elem


def _attest(l2, worker, machine, samples, bounds, health):
    task = l2.submit_task(machine, bounds, reward_vwc=1.0)
    h = twin_state_hash(sum(samples) / len(samples), max(samples), 10, 0)
    pub = [field_elem(h), health]
    proof = l2.prover.prove("telemetry_attest",
                            {"samples": samples, "bounds": bounds, "public": pub})
    l2.submit_result(task.task_id, worker, "bafy...", h, proof, pub, health_score=health)
    return task


def test_end_to_end_verify_reward_and_reject():
    l2 = LocalL2(batch_window_s=0)
    l2.staking.bond("w1", 5000.0); l2.staking.bond("w2", 5000.0)
    good = _attest(l2, "w1", "cnc-01", [1.0, 1.2, 0.9], (0.0, 5.0), health=85)
    bad = _attest(l2, "w2", "cnc-02", [1.0, 9.9], (0.0, 5.0), health=20)   # out of bounds
    batch = l2.close_batch()
    assert l2.task_status(good.task_id) == "verified"
    assert l2.task_status(bad.task_id) == "rejected"
    assert l2.balances["w1"] == 1.0 and "w2" not in l2.balances
    assert l2.twin_state("cnc-01").health_score == 85
    assert l2.twin_state("cnc-02") is None
    assert batch.aggregated_proof is not None


def test_epoch_slashes_faulty_worker_and_elects_committee():
    l2 = LocalL2(batch_window_s=0, k_eligible=2, committee_size=2)   # top-2 by S_i are eligible
    for w in ("w1", "w2", "w3"):
        l2.staking.bond(w, 5000.0)
    for i in range(5):
        _attest(l2, "w1", f"m{i}", [1.0], (0.0, 5.0), 90)
        _attest(l2, "w2", f"n{i}", [1.0], (0.0, 5.0), 90)
        _attest(l2, "w3", f"o{i}", [99.0], (0.0, 5.0), 90)    # always invalid
    l2.close_batch()
    committee = l2.advance_epoch()
    assert l2.staking.stake_of("w3") < 5000.0
    assert "w3" not in committee and len(committee) == 2


def test_credit_swap_and_maintenance_event():
    l2 = LocalL2(batch_window_s=0)
    l2.register_credit("FXA", 1000.0, 2000.0, 0.5)
    l2.register_credit("FXB", 3000.0, 1000.0, 0.3)
    q = l2.quote("FXA", "FXB", 100.0)
    got = l2.swap_credits("FXA", "FXB", 100.0)
    assert abs(q - got) < 1e-9
    l2.staking.bond("w1", 5000.0)
    _attest(l2, "w1", "press-07", [1.0], (0.0, 5.0), health=12)
    l2.close_batch()
    assert any(e["event"] == "MaintenanceTriggered" for e in l2.events)
