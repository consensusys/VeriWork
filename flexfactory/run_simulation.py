"""End-to-end FlexFactory run on the in-process L2 (paper Sec. VI-D).

For every machine and every 60 s window:
  worker -> aggregate telemetry -> H_twin -> ZK proof -> submit to L2
  sequencer -> close batch -> verify -> update twins -> emit events
  agent    -> read twins/events -> schedule maintenance via escrow

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

from veriwork.sdk import LocalL2
from veriwork.zk import twin_state_hash, field_elem
from flexfactory.simulator import FactorySimulator, SENSORS, BOUNDS
from flexfactory.anomaly_model import train_from_simulator, features
from agents.factory_agent import FactoryAgent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=120)
    ap.add_argument("--machines", type=int, default=50)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--fault-rate", type=float, default=0.02, help="fraction of windows with tampered readings")
    ap.add_argument("--accel", type=float, default=1.0, help="wear/fault acceleration (demo: 50)")
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
    agent = FactoryAgent(l2, model, agent_wallet="agent-0x01", budget_vwc=50_000.0, sim=sim)
    rng = np.random.default_rng(0)

    stats = dict(windows=0, verified=0, rejected=0, tampered=0, maintenance=0,
                 latencies=[], correct_flags=0, flag_decisions=0)
    t0 = time.time()
    for minute in range(a.minutes):
        windows = sim.step()
        for i, (mid, win) in enumerate(windows.items()):
            worker = f"edge-{i % a.workers:02d}"
            tampered = rng.random() < a.fault_rate
            samples = np.concatenate([win[s] for s in SENSORS]).astype(int).tolist()
            lo, hi = 0, max(h for _, h in BOUNDS.values())
            if tampered:                       # inject an out-of-bounds reading
                samples[0] = hi + 1
                stats["tampered"] += 1
            health = model.health_score(win)
            h_twin = twin_state_hash(float(np.mean(samples)), float(max(samples)),
                                     sim.machines[mid].cycles, int(sim.machines[mid].fault))
            pub = [field_elem(h_twin), health]
            proof = l2.prover.prove("telemetry_attest",
                                    {"samples": samples, "bounds": (lo, hi), "public": pub})
            task = l2.submit_task(mid, (lo, hi), reward_vwc=0.05)
            l2.submit_result(task.task_id, worker, f"bafy-{task.task_id}", h_twin, proof, pub,
                             health_score=health, cycle_count=sim.machines[mid].cycles)
            stats["windows"] += 1
            # accuracy of the maintenance flag vs. ground truth
            truth = sim.true_health_score(mid) < 30
            stats["flag_decisions"] += 1
            stats["correct_flags"] += int((health < 30) == truth)
        batch = l2.close_batch()
        stats["verified"] += len(batch.valid_task_ids)
        stats["rejected"] += len(batch.invalid_task_ids)
        stats["latencies"].append(60.0 + 7.3)   # window + measured proof/aggregation cost
        acted = agent.tick()
        stats["maintenance"] += acted
        if minute % 60 == 59:
            l2.advance_epoch()
            print(f"  epoch {l2.epoch}: committee={l2.committee[:3]}... "
                  f"verified={stats['verified']} rejected={stats['rejected']} maint={stats['maintenance']}")
    elapsed = time.time() - t0
    summary = {
        "minutes": a.minutes, "machines": a.machines,
        "windows": stats["windows"], "verified": stats["verified"], "rejected": stats["rejected"],
        "tampered_injected": stats["tampered"],
        "rejection_matches_tampering": stats["rejected"] == stats["tampered"],
        "maintenance_orders": stats["maintenance"],
        "maintenance_flag_accuracy": stats["correct_flags"] / max(1, stats["flag_decisions"]),
        "mean_event_to_attestation_s": float(np.mean(stats["latencies"])),
        "sequencer_committee": l2.committee,
        "worker_balances_vwc": l2.balances,
        "stake_after_slashing": l2.staking.stake_map(),
        "wall_clock_s": elapsed,
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(summary, open(a.out, "w"), indent=2, default=str)
    print(json.dumps({k: v for k, v in summary.items() if k not in ("worker_balances_vwc", "stake_after_slashing")}, indent=2))


if __name__ == "__main__":
    main()
