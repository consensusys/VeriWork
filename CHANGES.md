# VeriWork v2.1.0 — code aligned with the corrected paper

This release makes the code match the corrected paper and fixes defects found while
wiring the contracts to the real circuit.  Everything below was run:
`npx hardhat test` (35 passing, 6 with real Groth16 proofs), `pytest` (35 passed),
`pip install -e "node[dev]"`, `scripts/deploy.js`, the 180-minute FlexFactory run and
`experiments/zk_benchmark.py --test`.

## The main defect

In v2.0 the twin registry passed three public signals `[prevRoot, newRoot, health]`
through a `uint[]` verifier interface, while `telemetry_attest.circom` exposed four
different ones `[inputHash, twinHash, lo, hi]`, and snarkjs verifiers take a fixed-size
`uint[N]` (a different ABI selector).  No real proof could have been verified; only
`MockGroth16Verifier` worked.  `TaskRegistry` and `VeriWorkRollup` used the same
`uint[]` interface.

## Circuits
- C_twin rewritten (`circuits/lib/telemetry.circom`, main in `telemetry_attest.circom`):
  per-sensor bounds and per-sensor sums/peaks (8 sensors x 60 samples); outputs
  `twinHash`, `inputHash`, `boundsHash`; public inputs `prevStateRoot, healthScore,
  cycleCount, machineId, submitter`; the health score is range-checked (<= 100) and
  committed in H_twin.  Public-signal order verified from a real witness.
  **68,806 constraints** (v2.0: 67,080 — not the "~2.3M" stated in the old comments and
  README).  H_in keeps the v2.0 Poseidon tree.
- Small test instance `circuits/test/telemetry_attest_test.circom` (3,182 constraints)
  and `build_test.sh` (local dev ptau, exports `contracts/generated/TwinVerifierTest.sol`).
- `build.sh`: 2^17 Hermez ptau (was 2^22) and unique verifier names
  (`TelemetryAttestVerifier`, `QcAttestVerifier`); v2.0 exported two contracts that were
  both named `Groth16Verifier`.
- `bounds_hash.js` computes the bounds hash a machine is registered with (circomlibjs;
  tested equal to the circuit's output).  C_qc measured at 1,311 constraints.

## Contracts
- `IGroth16Verifier` (`uint[]`) replaced by `ITwinVerifier` (`uint[8]`) and
  `IAggregatedVerifier` (`uint[5]`); new `IOutcomeSource`.
- `FlexFactoryTwinRegistry` (its core is paper Code Snippet 1) rebuilds the eight public
  signals from its own state; only the registered operator may update a machine; cycle
  counts may not decrease (old proofs fail because each proof binds the prior root);
  invalid proofs are recorded (`badProofs`, `InvalidProof`) instead of reverting;
  `CRITICAL_HEALTH = 40` (was 30); registration rejects ids and bounds hashes outside the
  BN254 field.  Extensions implement the agent rails of paper Sec. VIII-D on-chain:
  whitelisted agents and approved providers, a per-agent daily cap, a human-review queue
  above the threshold (`reviewOrder`), and `suspendAgent` (DAO veto).  The v2.0 test
  "orders above the human-review threshold are refused" was actually hitting "order open".
- `TaskRegistry` binds the task id and the worker into the proof (in v2.0 anyone copying
  a proof from the mempool was paid), records valid/invalid counts, and stores a bounds
  hash per task.
- `VeriWorkRollup`: `reportEpoch` is removed — it let any single sequencer slash any node
  with self-reported numbers, repeatedly.  `advanceEpoch()` settles epsilon_i from the
  registries' counters, seeds the election from `block.prevrandao` (v2.0 took a
  caller-chosen, grindable seed) and may be called by anyone after a one-day timeout.
  Uptime is reported separately and bounded (`reportUptime`).
- `SequencerElection`: the validity ratio is 0 for a node with no submissions (v2.0 set
  the whole score to 0, disagreeing with the Python reference); batched
  `recordOutcomes`; bounded `setUptime`.
- `PoAWStaking`: share-based delegations (v2.0 left per-delegator balances unchanged
  after a slash); restaked positions are re-capped after a slash.
- `MockGroth16Verifier` implements both arities and, like snarkjs, rejects signals >= r.
- `scripts/deploy.js`: new constructor arguments, outcome-source wiring,
  `TWIN_VERIFIER=...` to deploy a real verifier.

## Python
- `LocalL2` mirrors the contracts: machines registered with operator and bounds, public
  inputs rebuilt from L2 state, operator check, non-decreasing cycles, failed proofs
  recorded against the submitter, threshold 40, election eligibility by minimum bond.
  API: `register_machine`, `expected_twin_signals`, `submit_twin_update`;
  `submit_task` / `submit_result` now follow `TaskRegistry`.
- `zk/prover.py`: circuit-order signal helpers, `telemetry_circuit_input` for snarkjs,
  per-sensor checks in `MockProver`; `SnarkJSProver.verify` raised on an invalid proof
  instead of returning `False`.
- `bft_liveness_threshold` returned floor((k-1)/3)+1 (3 of 7); it now returns k - f
  (5 of 7), with `bft_max_faulty` alongside.
- Agents: planner/model threshold 40; section references fixed (Algorithm 2,
  Sec. VIII-D); `tick` / `_schedule_maintenance` reformatted without behaviour change so
  they can be quoted verbatim as Code Snippet 2.
- `pip install -e "node[dev]"` failed because `readme = "../README.md"` lies outside the
  project; `node/README.md` added.

## Experiments
- `run_simulation.py`: v2.0 appended a constant `60.0 + 7.3` s per window as the
  event-to-attestation latency.  Latency is now explicitly modelled (1 Hz readings,
  60 s window, `--proof-latency-s`, default 7.3) and reported both from window start
  (67.3 s) and as the mean over readings (37.8 s).  Per-sensor bounds; recall, precision
  and accuracy of the maintenance flag (180-minute run: recall 0.63, precision 1.00).
- `zk_benchmark.py`: v2.0 hard-coded `proof_bytes=200` and `241,000` gas, printed
  RTX 4060 timings although snarkjs is CPU-only, and its input (hashes = 0) could not
  produce a witness.  It now proves a simulator window and reports measured times.
- `run_all.sh`: the "30-day run" label described a 720-minute run with wear accel 20.

## Tests and CI
Hardhat 15 -> 35 (6 real-Groth16 tests, skipped when the artifacts are missing);
pytest 29 -> 35 (one real-proof test, skipped likewise).  CI gains a `circuits` job that
installs circom 2.1.9, runs `build_test.sh` and both suites.

## Still open (needs the authors, not code)
- `node/veriwork/__init__.py` still cites the paper as PDCAT 2026 (the README citation was removed).
- The README clone URL (`github.com/andrewdong14/veriwork`) differs from the paper's.
- Paper claims this repository cannot reproduce: 67.3 s as a measurement, Figs. 3-4 as
  testbed measurements with RAPL energy, GPU proving times, 11,780 gas per twin update
  (a real update costs about 299k-408k gas), 91.3 % maintenance recall, the Next.js dApp,
  a TypeScript SDK, and PLONK proof aggregation.
