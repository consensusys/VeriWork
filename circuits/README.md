# Circuits

| Circuit | Statement | Public signals (in order) | Constraints |
|---|---|---|---|
| `telemetry_attest.circom` (C_twin) | For one 60 s window of 8 sensors x 60 samples: every reading lies in its sensor's registered bounds; `inputHash` = Poseidon fold of the readings (H_in); `twinHash` = Poseidon over per-sensor sums and peaks, cycle count, error flags and health score (H_twin); health score <= 100 | `twinHash, inputHash, boundsHash` (outputs), `prevStateRoot, healthScore, cycleCount, machineId, submitter` | 68,806 |
| `test/telemetry_attest_test.circom` | Same template, 2 sensors x 4 samples (for the Hardhat Groth16 tests) | same 8 signals | 3,182 |
| `qc_attest.circom` (C_qc) | 12 measurements within tolerance, measurements hidden | `batchHash, lower[12], upper[12]` | 1,311 |

Constraint counts were measured with circom 2.1.9 (`--O2`).  The v2.0 C_twin
(single global `[lo, hi]`, public `[inputHash, twinHash, lo, hi]`) had 67,080;
earlier comments quoting "~2.3M" were wrong.

`FlexFactoryTwinRegistry` and `TaskRegistry` rebuild the eight public signals
on-chain, so a proof is bound to the machine (or task), the previous state
root, the registered bounds and the submitting address.  `prevStateRoot`,
`machineId` and `submitter` carry no arithmetic; they are bound by the Groth16
setup and additionally by square constraints.

The health score is computed by the edge gateway's model outside the circuit.
C_twin commits to it (inside H_twin, bound as a public input) and range-checks
it; it does **not** prove the model output is correct.

## Build

```bash
npm install
./build.sh          # production circuits + verifiers (downloads 2^17 ptau)
./build_test.sh     # small test instance + TwinVerifierTest (~1-3 min, local dev ptau)
node bounds_hash.js "0,0,0,0,0,0,0,0" "6000,6000,6000,95000,20000,110000,250000,60000"
```

`bounds_hash.js` prints the `boundsHash` a machine is registered with
(`Poseidon(lo[0..7], hi[0..7])`, identical to the circuit's output).

Both build scripts use a **single development phase-2 contribution**.  They are
not a trusted setup; a deployment needs a multi-party ceremony.  The
`MockGroth16Verifier` used by most Hardhat tests is **not sound**.

snarkjs proves on the CPU; the Hardhat Groth16 suite measured about 408k gas
for a first `updateTwinState` with the real verifier and about 299k for
subsequent updates of the same machine.

## Not included

Recursive aggregation of Groth16 proofs into one batch proof (paper Sec. IV-E)
is **not** part of this repository: there is no aggregator circuit, and
`VeriWorkRollup` accepts whatever aggregated proof its configured verifier
accepts (the mock, by default).
