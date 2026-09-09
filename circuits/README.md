# Circuits

| Circuit | Statement | Public inputs | Approx. constraints |
|---|---|---|---|
| `telemetry_attest.circom` | C_twin: 480 readings in [lo,hi]; H_twin = Poseidon(sum, N, peak, cycles, flags) | `inputHash, twinHash, lo, hi` | ~2.3M |
| `qc_attest.circom` | C_qc: 12 measurements within tolerance, measurements hidden | `batchHash, lower[12], upper[12]` | ~5k |

Run `./build.sh` to compile, run a dev trusted setup, and export Solidity
verifiers to `contracts/generated/`.  The `MockGroth16Verifier` used by the
Hardhat tests is **not sound** and must be replaced before any deployment.

Proof aggregation (PLONK recursion over M Groth16 proofs, paper Sec. IV-D)
is implemented in a separate `aggregator/` workspace targeting a
recursion-capable proving stack; see `docs/ARCHITECTURE.md`.
