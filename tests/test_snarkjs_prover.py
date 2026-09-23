"""Python <-> circuit interface check with REAL Groth16 proofs.

Uses the small C_twin instance built by circuits/build_test.sh; skipped when
the artifacts or snarkjs are missing.
"""
import os
import shutil

import pytest

from veriwork.zk import SnarkJSProver, telemetry_circuit_input, TWIN_SIGNALS, machine_field

ROOT = os.path.join(os.path.dirname(__file__), "..")
BUILD = os.path.join(ROOT, "circuits", "build", "test")
SNARKJS = os.path.join(ROOT, "circuits", "node_modules", ".bin", "snarkjs")
READY = (os.path.exists(os.path.join(BUILD, "telemetry_attest_test_final.zkey"))
         and os.path.exists(SNARKJS) and shutil.which("node"))

pytestmark = pytest.mark.skipif(not READY, reason="run circuits/build_test.sh first")
CIRCUIT = "telemetry_attest_test"


def test_public_signal_layout_and_verification():
    prover = SnarkJSProver(BUILD, snarkjs_bin=SNARKJS)
    mid = machine_field("press-07")
    submitter = 0x70997970C51812dc3A010C7d01b50e0d17dc79C8
    inp = telemetry_circuit_input([[10, 20, 30, 40], [100, 200, 150, 50]], [(0, 60), (0, 300)],
                                  error_flags=0, prev_root=12345, health_score=37, cycle_count=900,
                                  machine_id=mid, submitter=submitter)
    proof = prover.prove(CIRCUIT, {"circuit_input": inp})
    assert len(proof.public) == len(TWIN_SIGNALS) == 8
    # outputs (twinHash, inputHash, boundsHash) first, then the public inputs in order
    assert list(proof.public[3:]) == [12345, 37, 900, mid, submitter]
    assert prover.verify(proof, proof.public, CIRCUIT)
    edited = list(proof.public); edited[4] = 95                  # health score changed after proving
    assert not prover.verify(proof, edited, CIRCUIT)
