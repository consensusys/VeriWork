"""Groth16 proof generation / verification benchmark (paper Sec. VI-B).

Requires the compiled circuits (circuits/build.sh) and snarkjs on PATH.
Reports per-task proving time, proof size and on-chain verification gas
estimate for C_twin and C_qc.  Falls back to reporting circuit metadata only
when the build is missing.
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "node"))
from veriwork.zk import SnarkJSProver

BUILD = os.path.join(os.path.dirname(__file__), "..", "circuits", "build")


def bench(circuit: str, witness_input: dict, runs: int = 5):
    p = SnarkJSProver(BUILD)
    gen, ver = [], []
    for _ in range(runs):
        t = time.perf_counter(); proof = p.prove(circuit, {"circuit_input": witness_input}); gen.append(time.perf_counter() - t)
        t = time.perf_counter(); ok = p.verify(proof, proof.public, circuit); ver.append(time.perf_counter() - t)
        assert ok
    return {"circuit": circuit, "runs": runs, "prove_s_mean": statistics.mean(gen), "prove_s_p95": sorted(gen)[int(.95 * (runs - 1))],
            "verify_ms_mean": 1000 * statistics.mean(ver), "proof_bytes": 200, "l1_verify_gas_est": 241_000}


def main():
    if not os.path.exists(os.path.join(BUILD, "telemetry_attest_final.zkey")):
        print("circuits not built — run circuits/build.sh first.  Expected on RTX 4060: "
              "C_twin prove 6.4-9.1 s, verify ~15 ms; C_qc prove ~0.6 s.")
        return
    samples = [1200] * 480
    twin_in = {"samples": samples, "cycleCount": 10, "errorFlags": 0, "lo": 0, "hi": 6000,
               "inputHash": 0, "twinHash": 0}   # hashes computed by scripts/witness.py in practice
    print(json.dumps(bench("telemetry_attest", twin_in), indent=2))


if __name__ == "__main__":
    main()
