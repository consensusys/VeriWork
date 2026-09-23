"""Groth16 proving / verification benchmark for C_twin (paper Sec. VI-B).

Builds a telemetry window with the FlexFactory simulator, proves it with the
snarkjs CLI (CPU; WASM witness generation) and verifies it locally.

  python experiments/zk_benchmark.py          # production C_twin, 8 x 60 (circuits/build.sh)
  python experiments/zk_benchmark.py --test   # 2 x 4 test instance      (circuits/build_test.sh)

Timings include snarkjs process start-up.  Proof size is reported as the
Solidity calldata of (a, b, c).  On-chain gas of updateTwinState with the real
verifier is measured by the Hardhat suite (test/TwinRegistry.groth16.test.js).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "node"))
sys.path.insert(0, os.path.join(HERE, ".."))
from veriwork.zk import SnarkJSProver, telemetry_circuit_input, machine_field, submitter_field  # noqa: E402

CIRC = os.path.join(HERE, "..", "circuits")
SNARKJS = os.path.join(CIRC, "node_modules", ".bin", "snarkjs")


def window(test: bool):
    from flexfactory.simulator import FactorySimulator, SENSORS, BOUNDS
    sim = FactorySimulator(n_machines=1, seed=3)
    win = next(iter(sim.step().values()))
    sensors, m = (SENSORS[:2], 4) if test else (SENSORS, 60)
    return [win[s][:m].astype(int).tolist() for s in sensors], [BOUNDS[s] for s in sensors]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="use the 2x4 test instance")
    ap.add_argument("--runs", type=int, default=5)
    a = ap.parse_args()
    circuit = "telemetry_attest_test" if a.test else "telemetry_attest"
    build = os.path.join(CIRC, "build", "test") if a.test else os.path.join(CIRC, "build")
    if not os.path.exists(os.path.join(build, f"{circuit}_final.zkey")):
        print(f"{circuit} is not built -- run circuits/{'build_test.sh' if a.test else 'build.sh'} first.")
        return
    samples, bounds = window(a.test)
    inp = telemetry_circuit_input(samples, bounds, error_flags=0, prev_root=0, health_score=87,
                                  cycle_count=1200, machine_id=machine_field("cnc-01"),
                                  submitter=submitter_field("edge-00"))
    prover = SnarkJSProver(build, snarkjs_bin=SNARKJS if os.path.exists(SNARKJS) else "snarkjs")
    gen, ver = [], []
    for _ in range(a.runs):
        t = time.perf_counter(); proof = prover.prove(circuit, {"circuit_input": inp}); gen.append(time.perf_counter() - t)
        t = time.perf_counter(); ok = prover.verify(proof, proof.public, circuit); ver.append(time.perf_counter() - t)
        assert ok, "proof failed to verify"
    pa, pb, pc, pub = proof.to_solidity_args()
    print(json.dumps({
        "circuit": circuit, "runs": a.runs, "prover": "snarkjs CLI (CPU)",
        "host": {"machine": platform.machine(), "cpus": os.cpu_count()},
        "prove_s_mean": round(statistics.mean(gen), 3), "prove_s_max": round(max(gen), 3),
        "verify_ms_mean": round(1000 * statistics.mean(ver), 1),
        "public_signals": len(pub),
        "proof_calldata_bytes": 32 * (len(pa) + 2 * len(pb) + len(pc)),
    }, indent=2))


if __name__ == "__main__":
    main()
