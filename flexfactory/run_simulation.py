"""End-to-end FlexFactory run on the in-process L2 (paper Sec. VI-D).

For every machine and every 60 s window:
  operator -> aggregate telemetry -> H_twin -> proof -> submit twin update
  sequencer -> close batch -> verify against rebuilt public inputs -> update twins
  agent    -> read twins/events -> schedule maintenance via escrow

Proofs come from the insecure MockProver (same public interface as C_twin), so
this run exercises the protocol logic, not proving cost.  Latency figures are
*modelled*: readings arrive at 1 Hz inside the window, which closes after 60 s,
and proving + L2 commitment takes --proof-latency-s (an input, not a
measurement).  Use experiments/zk_benchmark.py to measure proving time.

Usage:  python -m flexfactory.run_simulation --minutes 120 --machines 50
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "node"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from veriwork.sdk import LocalL2, CRITICAL_HEALTH
from veriwork.zk import mock_twin_hash, mock_input_hash
from flexfactory.simulator import FactorySimulator, SENSORS, BOUNDS
from flexfactory.anomaly_model import train_from_simulator
from agents.factory_agent import FactoryAgent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=120)
    ap.add_argument("--machines", type=int, default=50)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--fault-rate", type=float, default=0.02, help="fraction of windows with tampered readings")
    ap.add_argument("--accel", type=float, default=1.0, help="wear/fault acceleration (demo: 50)")
    ap.add_argument("--proof-latency-s", type=float, default=7.3,
                    help="assumed proving + L2 commitment time per window (not measured by this run)")
    ap.add_argument("--out", default="experiments/results/flexfactory_run.json")
    a = ap.parse_args()

    print(">> training health model on simulator data ...")
    model = train_from_simulator()
    sim = FactorySimulator(n_machines=a.machines, seed=1, accel=a.accel)
    l2 = LocalL2(batch_window_s=0)
    for w in range(a.workers):
        l2.staking.bond(f"edge-{w:02d}", 5000.0)
    l2.register_credit("FXC-A", 10_000.0, 20_000.0, 0.5)
    l2.register_credit("SUP-B", 30_000.0, 10_000.0, 0.25)
    bounds = [BOUNDS[s] for s in SENSORS]                         # per-sensor [lo, hi]
    operator_of = {}
    for i, mid in enumerate(sim.machines):
        operator_of[mid] = f"edge-{i % a.workers:02d}"
        l2.register_machine(mid, operator_of[mid], bounds)
    agent = FactoryAgent(l2, model, agent_wallet="agent-0x01", budget_vwc=50_000.0, sim=sim)
    rng = np.random.default_rng(0)

    stats = dict(windows=0, verified=0, rejected=0, tampered=0, maintenance=0, tp=0, fp=0, fn=0, tn=0)
    t0 = time.time()
    for minute in range(a.minutes):
        windows = sim.step()
        for mid, win in windows.items():
            worker = operator_of[mid]
            m = sim.machines[mid]
            samples = [win[s].astype(int).tolist() for s in SENSORS]    # sensor-major, as in C_twin
            if rng.random() < a.fault_rate:                             # inject an out-of-bounds reading
                samples[0][0] = bounds[0][1] + 1
                stats["tampered"] += 1
            health = model.health_score(win)
            twin_hash = mock_twin_hash(samples, m.cycles, int(m.fault), health)
            input_hash = mock_input_hash(samples)
            pub = l2.expected_twin_signals(mid, worker, twin_hash, input_hash, health, m.cycles)
            proof = l2.prover.prove("telemetry_attest", {"samples": samples, "bounds": bounds,
                                                         "health_score": health, "public": pub})
            l2.submit_twin_update(mid, worker, f"bafy-{mid}-{minute}", twin_hash, input_hash,
                                  health, m.cycles, proof, reward_vwc=0.05)
            stats["windows"] += 1
            # maintenance flag vs. ground truth (latent health)
            truth, pred = sim.true_health_score(mid) < CRITICAL_HEALTH, health < CRITICAL_HEALTH
            stats[("tp" if truth else "fp") if pred else ("fn" if truth else "tn")] += 1
        batch = l2.close_batch()
        stats["verified"] += len(batch.valid_task_ids)
        stats["rejected"] += len(batch.invalid_task_ids)
        stats["maintenance"] += agent.tick()
        if minute % 60 == 59:
            l2.advance_epoch()
            print(f"  epoch {l2.epoch}: committee={l2.committee[:3]}... "
                  f"verified={stats['verified']} rejected={stats['rejected']} maint={stats['maintenance']}")
    elapsed = time.time() - t0

    # modelled latency: 1 Hz readings in a 60 s window, attested proof_latency after close
    spm = sim.samples_per_min
    waits = [60.0 - j * 60.0 / spm for j in range(spm)]
    tp, fp, fn, tn = stats["tp"], stats["fp"], stats["fn"], stats["tn"]
    summary = {
        "minutes": a.minutes, "machines": a.machines, "maintenance_threshold": CRITICAL_HEALTH,
        "windows": stats["windows"], "verified": stats["verified"], "rejected": stats["rejected"],
        "tampered_injected": stats["tampered"],
        "rejection_matches_tampering": stats["rejected"] == stats["tampered"],
        "maintenance_orders": stats["maintenance"],
        "maintenance_flag_recall": tp / max(1, tp + fn),
        "maintenance_flag_precision": tp / max(1, tp + fp),
        "maintenance_flag_accuracy": (tp + tn) / max(1, tp + fp + fn + tn),
        "latency_is_modelled": True,
        "proof_latency_s_assumed": a.proof_latency_s,
        "window_start_to_attestation_s": 60.0 + a.proof_latency_s,
        "mean_event_to_attestation_s": float(np.mean(waits)) + a.proof_latency_s,
        "sequencer_committee": l2.committee,
        "worker_balances_vwc": l2.balances,
        "stake_after_slashing": l2.staking.stake_map(),
        "wall_clock_s": elapsed,
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(summary, open(a.out, "w"), indent=2, default=str)
    print(json.dumps({k: v for k, v in summary.items() if k not in ("worker_balances_vwc", "stake_after_slashing")},
                     indent=2))


if __name__ == "__main__":
    main()
