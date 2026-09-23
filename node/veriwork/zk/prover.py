"""ZK prover backends.

`SnarkJSProver` shells out to the snarkjs CLI against the compiled circuits in
`circuits/` (Groth16, BN254).  `MockProver` reproduces the same interface with
hash-based stand-ins so the node, simulator and tests run without a trusted
setup; it is *not* sound and must never be used in production.

C_twin public signals, in circuit order (outputs first, then public inputs);
the registry contracts and `LocalL2` rebuild exactly this vector:

    [twinHash, inputHash, boundsHash,
     prevStateRoot, healthScore, cycleCount, machineId, submitter]

With the mock backend, twinHash / inputHash / boundsHash are SHA-256 stand-ins
(`mock_twin_hash`, `mock_input_hash`, `mock_bounds_hash`); with snarkjs they are
the Poseidon outputs of the circuit (read them from the proof's public signals).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

BN254_FIELD = 21888242871839275222246405745257275088548364400416034343698204186575808495617


@dataclass(frozen=True)
class Proof:
    scheme: str                 # "groth16" | "plonk-agg" | "mock"
    pi_a: Sequence[int]
    pi_b: Sequence[Sequence[int]]
    pi_c: Sequence[int]
    public: Sequence[int]

    def to_solidity_args(self):
        """(a, b, c, pub) in the layout expected by snarkjs-generated verifiers."""
        a = [int(self.pi_a[0]), int(self.pi_a[1])]
        b = [[int(self.pi_b[0][1]), int(self.pi_b[0][0])],
             [int(self.pi_b[1][1]), int(self.pi_b[1][0])]]
        c = [int(self.pi_c[0]), int(self.pi_c[1])]
        return a, b, c, [int(x) for x in self.public]


TWIN_SIGNALS = ("twinHash", "inputHash", "boundsHash", "prevStateRoot",
                "healthScore", "cycleCount", "machineId", "submitter")


def field_elem(b: bytes) -> int:
    return int.from_bytes(b, "big") % BN254_FIELD


def _sha_field(tag: str, payload) -> int:
    return field_elem(hashlib.sha256(
        tag.encode() + json.dumps(payload, sort_keys=True, default=str).encode()).digest())


def _per_sensor(samples) -> List[List[int]]:
    """Accept per-sensor lists [[...], ...] or a flat list (treated as one sensor)."""
    if samples and isinstance(samples[0], (list, tuple)):
        return [[int(x) for x in row] for row in samples]
    return [[int(x) for x in samples]]


def mock_input_hash(samples) -> int:
    """Stand-in for H_in (Poseidon fold of the readings) -- MockProver only."""
    return _sha_field("H_in", _per_sensor(samples))


def mock_bounds_hash(bounds) -> int:
    """Stand-in for Poseidon(lo[], hi[]) -- MockProver only.
    `bounds` is [(lo, hi), ...] per sensor (or a single (lo, hi))."""
    b = [bounds] if not isinstance(bounds[0], (list, tuple)) else list(bounds)
    return _sha_field("bounds", [[int(lo), int(hi)] for lo, hi in b])


def window_aggregates(samples):
    """Per-sensor (sums, peaks), exactly as C_twin computes them."""
    rows = _per_sensor(samples)
    return [sum(r) for r in rows], [max(r) if r else 0 for r in rows]


def mock_twin_hash(samples, cycle_count: int, error_flags: int, health_score: int) -> int:
    """Stand-in for H_twin = Poseidon(Poseidon(sums, peaks), cycles, flags, health)."""
    sums, peaks = window_aggregates(samples)
    return _sha_field("H_twin", [sums, peaks, int(cycle_count), int(error_flags), int(health_score)])


def machine_field(machine_id: str) -> int:
    """Field-element machine id (what the registry's machineId holds)."""
    return field_elem(hashlib.sha256(b"machine:" + machine_id.encode()).digest())


def submitter_field(who: str) -> int:
    """uint160(address) for 0x-addresses, else a hashed stand-in for simulated nodes."""
    if who.startswith("0x") and len(who) == 42:
        return int(who, 16)
    return field_elem(hashlib.sha256(b"addr:" + who.encode()).digest()) >> 96


def telemetry_public_signals(twin_hash: int, input_hash: int, bounds_hash: int, prev_root: int,
                             health_score: int, cycle_count: int, machine_id: int,
                             submitter: int) -> List[int]:
    """C_twin public-signal vector in circuit order (see TWIN_SIGNALS)."""
    return [int(twin_hash), int(input_hash), int(bounds_hash), int(prev_root),
            int(health_score), int(cycle_count), int(machine_id), int(submitter)]


def telemetry_circuit_input(samples, bounds, error_flags: int, prev_root: int, health_score: int,
                            cycle_count: int, machine_id: int, submitter: int) -> Dict:
    """snarkjs input for C_twin (the three hashes are circuit outputs, not inputs)."""
    rows = _per_sensor(samples)
    b = [bounds] * len(rows) if not isinstance(bounds[0], (list, tuple)) else list(bounds)
    s = lambda x: str(int(x))
    return {"prevStateRoot": s(prev_root), "healthScore": s(health_score), "cycleCount": s(cycle_count),
            "machineId": s(machine_id), "submitter": s(submitter),
            "samples": [[s(x) for x in r] for r in rows],
            "lo": [s(lo) for lo, _ in b], "hi": [s(hi) for _, hi in b], "errorFlags": s(error_flags)}


class ProverBackend:
    def prove(self, circuit: str, witness: Dict) -> Proof:  # pragma: no cover
        raise NotImplementedError

    def verify(self, proof: Proof, public_inputs: Sequence[int]) -> bool:  # pragma: no cover
        raise NotImplementedError

    def aggregate(self, proofs: List[Proof]) -> Proof:  # pragma: no cover
        raise NotImplementedError


class MockProver(ProverBackend):
    """Insecure stand-in: 'proof' is a keyed hash over the public inputs.

    verify() returns True iff the proof was produced for exactly these public
    inputs by a MockProver *and* the witness satisfied the circuit's range
    constraints at prove() time (we re-check them here for the telemetry and
    QC circuits so the simulator exercises the reject path).
    """

    def __init__(self, key: bytes = b"mock-prover"):
        self.key = key

    @staticmethod
    def _constraints_ok(circuit: str, w: Dict) -> bool:
        if circuit == "telemetry_attest":
            rows = _per_sensor(w["samples"])
            b = w["bounds"]
            b = [b] * len(rows) if not isinstance(b[0], (list, tuple)) else list(b)
            in_range = all(lo <= x <= hi for row, (lo, hi) in zip(rows, b) for x in row)
            return in_range and 0 <= int(w.get("health_score", 0)) <= 100
        if circuit == "qc_attest":
            return all(l <= m <= u for m, l, u in zip(w["m"], w["lower"], w["upper"]))
        return True

    def _tag(self, public: Sequence[int], ok: bool) -> int:
        h = hashlib.sha256(self.key + json.dumps([int(x) for x in public]).encode()
                           + (b"1" if ok else b"0")).digest()
        return field_elem(h)

    def prove(self, circuit: str, witness: Dict) -> Proof:
        ok = self._constraints_ok(circuit, witness)
        public = [int(x) for x in witness["public"]]
        t = self._tag(public, ok)
        return Proof("mock", (t, 1), ((1, 2), (3, 4)), (t, 2), public)

    def verify(self, proof: Proof, public_inputs: Sequence[int]) -> bool:
        if proof.scheme not in ("mock", "mock-agg"):
            return False
        if [int(x) for x in public_inputs] != [int(x) for x in proof.public]:
            return False
        return int(proof.pi_a[0]) == self._tag(proof.public, True)

    def aggregate(self, proofs: List[Proof]) -> Proof:
        pub = [field_elem(hashlib.sha256(
            b"".join(int(p.pi_a[0]).to_bytes(32, "big") for p in proofs)).digest())]
        t = self._tag(pub, True)
        return Proof("mock-agg", (t, 1), ((1, 2), (3, 4)), (t, 2), pub)


class SnarkJSProver(ProverBackend):
    """Real Groth16 backend via the snarkjs CLI. Requires `circuits/build/`."""

    def __init__(self, build_dir: str = "circuits/build", snarkjs_bin: str = "snarkjs"):
        self.build_dir = build_dir
        self.snarkjs = snarkjs_bin

    def _run(self, *args: str) -> str:
        return subprocess.check_output([self.snarkjs, *args], text=True)

    def prove(self, circuit: str, witness: Dict) -> Proof:
        wasm = os.path.join(self.build_dir, f"{circuit}_js", f"{circuit}.wasm")
        zkey = os.path.join(self.build_dir, f"{circuit}_final.zkey")
        with tempfile.TemporaryDirectory() as d:
            inp, wtns = os.path.join(d, "input.json"), os.path.join(d, "witness.wtns")
            pf, pub = os.path.join(d, "proof.json"), os.path.join(d, "public.json")
            with open(inp, "w") as f:
                json.dump(witness["circuit_input"], f)
            self._run("wtns", "calculate", wasm, inp, wtns)
            self._run("groth16", "prove", zkey, wtns, pf, pub)
            p, pubv = json.load(open(pf)), json.load(open(pub))
        return Proof("groth16",
                     tuple(int(x) for x in p["pi_a"][:2]),
                     tuple(tuple(int(y) for y in row[:2]) for row in p["pi_b"][:2]),
                     tuple(int(x) for x in p["pi_c"][:2]),
                     tuple(int(x) for x in pubv))

    def verify(self, proof: Proof, public_inputs: Sequence[int],
               circuit: str = "telemetry_attest") -> bool:
        vkey = os.path.join(self.build_dir, f"{circuit}_vkey.json")
        with tempfile.TemporaryDirectory() as d:
            pf, pub = os.path.join(d, "proof.json"), os.path.join(d, "public.json")
            json.dump({"pi_a": [str(x) for x in proof.pi_a] + ["1"],
                       "pi_b": [[str(y) for y in r] for r in proof.pi_b] + [["1", "0"]],
                       "pi_c": [str(x) for x in proof.pi_c] + ["1"],
                       "protocol": "groth16", "curve": "bn128"}, open(pf, "w"))
            json.dump([str(x) for x in public_inputs], open(pub, "w"))
            try:
                out = self._run("groth16", "verify", vkey, pub, pf)
            except subprocess.CalledProcessError:
                return False          # snarkjs exits non-zero on an invalid proof
        return "OK!" in out

    def aggregate(self, proofs: List[Proof]) -> Proof:
        # Recursive aggregation is not part of this release: there is no
        # aggregator circuit (see README).  Production deployments plug a
        # recursion-capable backend in here.
        raise NotImplementedError("recursive proof aggregation is not included in this release")
