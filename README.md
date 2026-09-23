# VeriWork

**A Layer 2 Proof-of-Adaptive-Work Platform for Web4 dApps with Zero-Knowledge Verification**

Reference implementation of VeriWork. It builds on the PoUW protocol of
[Proofware (arXiv:1903.09276)](https://arxiv.org/abs/1903.09276) but is fully self-contained.

VeriWork repositions Proof of Useful Work (PoUW) as an Ethereum L2 rollup governed by
**Proof-of-Adaptive-Work (PoAW)**: multi-layer staking (bonds, delegation, restaking),
objective ZK-derived slashing, stake-and-contribution-weighted sequencer election, and
commit–reveal transaction ordering against MEV. Worker computation is attested by Groth16
proofs, and a Web4 AI-agent layer (MCP · A2A · x402 · ERC-8004) operates dApps autonomously.
**FlexFactory**, an Industry 4.0 digital-twin dApp, is the flagship demonstration.

```
                 ┌────────────────────────── Tier 1: Ethereum L1 ──────────────────────────┐
                 │  VWCToken · VeriWorkRollup (batch roots, blob DA, aggregated verifier)   │
                 │  PoAWStaking (σ_i, slashing) · SequencerElection (S_i, P(n_i))            │
                 └───────────────────────────────────▲───────────────────────────────────────┘
                                                     │ every N blocks: (stateRoot, orderRoot, π_agg)
                 ┌────────────────────────── Tier 2: VeriWork L2   ─────────────────────────┐
                 │  TaskRegistry · CreditMarket (Bancor + adaptive P_c) · CommitReveal       │
                 │  FlexFactoryTwinRegistry · node/ (batcher, prover, PoAW election)         │
                 └───────────────────────────────────▲───────────────────────────────────────┘
                                                     │ (CID, H_twin, π) per 60 s telemetry window
                 ┌──────────────────────── Tier 3: Edge + Web4 agents ─────────────────────┐
                 │  flexfactory/ simulator (50 machines × 8 sensors) · circuits/ (C_twin, C_qc) │
                 │  agents/ (planner, MCP server, A2A card, x402 client, ERC-8004 identity)  │
                 └──────────────────────────────────────────────────────────────────────────┘
```

## Repository layout

| Path | What it is | Paper section |
|---|---|---|
| `contracts/` | Solidity 0.8.24 (Cancun): `VWCToken`, `PoAWStaking`, `SequencerElection`, `VeriWorkRollup`, `CommitRevealOrdering`, `CreditMarket`, `TaskRegistry`, `FlexFactoryTwinRegistry` (Code Snippet 1), `AgentRegistry8004Adapter` | III, IV, V, VI-D, VII |
| `circuits/` | Circom 2.1 circuits `telemetry_attest` (C_twin) and `qc_attest` (C_qc), shared template in `lib/`, small test instance in `test/`, Groth16 build scripts, `bounds_hash.js` | IV-E |
| `node/veriwork/` | Python node library: PoUW score (Eq. 3), hybrid committee election (Eq. 2), multi-layer staking + dynamic slashing (Eq. 1), adaptive work controller, commit–reveal pool, batcher, prover backends, Bancor curve (Eq. 4–7) + adaptive pricing, SDK with in-process `LocalL2` | III, IV, V |
| `agents/` | Web4 agent layer: model-agnostic planner, `FactoryAgent` loop (Code Snippet 2, Algorithm 2), MCP server, A2A agent card, x402 payment client, ERC-8004 registration | IV-D, V-D, VIII-D |
| `flexfactory/` | Factory-floor simulator, health model, end-to-end run script | VI-D |
| `experiments/` | Consensus scaling *model* (Figs. 3–4), ZK benchmark, Caliper testbed config, `run_all.sh` | VI |
| `tests/`, `test/` | pytest (35) and Hardhat (35; 6 of them prove and verify real Groth16 proofs) | — |

## Quick start

```bash
git clone https://github.com/andrewdong14/veriwork && cd veriwork

# Python: node library, simulator, agents
pip install -e "node[dev]"
python -m pytest tests -q                  # 34 passed, 1 skipped (35 once circuits/build_test.sh has run)

# End-to-end FlexFactory run on the in-process L2 (mock proofs; wear accelerated so the
# agent schedules maintenance inside a 3-hour simulated window)
python -m flexfactory.run_simulation --minutes 180 --machines 50 --accel 50

# Solidity
npm install
npx hardhat test                           # 29 passing, 6 pending (the real-proof suite)
npx hardhat run scripts/deploy.js          # in-memory deployment with mock verifiers

# Circuits + real-proof tests (circom >= 2.1.6 on PATH, or CIRCOM=/path/to/circom)
(cd circuits && npm install && ./build_test.sh)   # small C_twin instance + verifier, 1-3 min
npx hardhat test                           # 35 passing, incl. real Groth16 proofs on-chain
python experiments/zk_benchmark.py --test  # prove/verify timing with snarkjs

# Consensus scaling model (see caveats) + plot
python experiments/consensus_benchmark.py --plot
```

Offline Solidity builds: set `LOCAL_SOLCJS` to an npm `solc@0.8.24` package directory
(see `hardhat.config.js`).

Sample output of the FlexFactory run (180 min, 50 machines, 10 edge workers, 2 % tampered windows):

```
verified=8833 rejected=167  tampered_injected=167  rejection_matches_tampering=true
maintenance_orders=123  flag_recall=0.63  flag_precision=1.00  flag_accuracy=0.97
window_start_to_attestation_s=67.3  mean_event_to_attestation_s=37.8   (modelled, see caveats)
epoch 3: committee=['edge-09','edge-06','edge-01',...]   stake_after_slashing: {...}
```

Every tampered window fails verification and is recorded against its operator, whose stake
is slashed at the epoch boundary; the agent escrows VWC for maintenance whenever a twin's
attested health drops below 40.

## How the pieces map to the paper

**PoAW consensus (Sec. III).**
`node/veriwork/consensus/` and `contracts/PoAWStaking.sol` + `SequencerElection.sol` + `VeriWorkRollup.sol`.

- PoUW score `S_i = α·T_valid/T_sub + β·V_i/V_max + γ·U_i/U_max`, with the validity ratio taken
  as 0 for a node with no submissions — `pouw_score.py`, `SequencerElection.score()`
- Election `P(n_i) = σ_i S_i / Σ σ_j S_j`, instantiated as *top-k by S_i, then sortition without
  replacement weighted by σ_i·S_i*, seeded from the RANDAO beacon (not by the caller) —
  `selection.py`, `SequencerElection.advanceEpoch()`
- Dynamic slashing `σ_i ← σ_i (1 − λ ε_i)`. At each epoch boundary `VeriWorkRollup.advanceEpoch()`
  computes ε_i from the valid/invalid counters that `FlexFactoryTwinRegistry` and `TaskRegistry`
  record per submitter; no party reports ε_i. Applied pro rata to the own bond and to share-based
  delegations; restaked positions are re-capped after a slash — `staking.py`, `PoAWStaking.slash()`
- BFT committee: k sequencers tolerate f = ⌊(k−1)/3⌋ faults and need k − f honest ones
  (5 of 7) — `bft_max_faulty()`, `bft_liveness_threshold()`
- Adaptive work quotas — `adaptive_work.py`
- MEV resistance: `c = H(tx‖r)` committed in block N, revealed in N+1, canonical order over
  commitments — `sequencer/commit_reveal.py`, `CommitRevealOrdering.sol`

**ZK layer (Sec. IV-E).** `circuits/telemetry_attest.circom` (C_twin, 68,806 constraints) proves,
for one 60 s window of 8 sensors × 60 samples, that every reading lies within its sensor's
registered bounds, and outputs `H_in` (Poseidon hash tree over the readings), `H_twin` (Poseidon over
per-sensor sums and peaks, cycle count, error flags and health score) and the bounds hash. Its eight
public signals `[twinHash, inputHash, boundsHash, prevStateRoot, healthScore, cycleCount, machineId,
submitter]` are rebuilt on-chain, so a proof is bound to one machine (or task), one prior state, the
registered bounds and one sender. `qc_attest.circom` proves tolerance compliance without revealing
measurements. `node/veriwork/zk/prover.py` wraps snarkjs (`SnarkJSProver`) and ships an explicitly
*insecure* `MockProver` with the same interface.

**Credit market (Sec. V-C).** Bancor curve `P = B_a/(O_m W_a)` with buy (Eq. 6), sell (Eq. 7) and
swap in `market/bonding_curve.py` and `CreditMarket.sol` (W ∈ {¼, ½, 1} evaluated exactly on-chain).
Adaptive layer `P_c = P_0 (1 + α_p D_r/S_r)` over a TWAP of excess demand in
`market/adaptive_pricing.py` / `adaptiveMultiplier()`.

**Web4 agents (Sec. IV-D, V-D, VIII-D).** `agents/factory_agent.py` implements the
sense → plan → act loop of Code Snippet 2. `FlexFactoryTwinRegistry` enforces the agent safety
rails on-chain: whitelisted agents (with ERC-8004 identity) and approved service providers, a
per-agent daily spending cap, governance (human) review of orders above a threshold, and DAO
suspension of an agent; `AgentSafetyRails` mirrors them client-side for the in-process L2.
Agents expose tools over **MCP** (`mcp_server.py`), advertise skills via an **A2A** agent card,
settle machine-to-machine payments over **x402**, and register identity/reputation under
**ERC-8004** (`erc8004.py`, `AgentRegistry8004Adapter.sol`).

## Status and honest caveats

- **Mock verifier by default.** `MockGroth16Verifier` / `MockProver` are test doubles. For a real
  deployment run `circuits/build.sh` and deploy with `TWIN_VERIFIER=TelemetryAttestVerifier`. Both
  build scripts make a single development phase-2 contribution: they are not a trusted setup.
- **No proof aggregation.** Recursive aggregation of Groth16 proofs is specified in the paper but
  not included: there is no aggregator circuit, and `VeriWorkRollup` accepts whatever aggregated
  proof its configured verifier accepts (the mock, by default).
- **The health score is committed, not proven.** It is computed by the edge model outside the
  circuit; C_twin commits it inside `H_twin`, binds it as a public input and range-checks it.
- **Latency in `run_simulation.py` is modelled, not measured.** Readings arrive at 1 Hz inside a
  60 s window; proving + L2 commitment time is an input (`--proof-latency-s`, default 7.3 s). The run
  reports latency from window start (67.3 s) and the mean over readings (37.8 s).
- **Figs. 3–4 come from a model.** `experiments/consensus_benchmark.py` is a cost model whose
  constants are fitted to the testbed values reported in the paper; energy uses assumed per-node
  power (190/42/21 W), not RAPL readings. `experiments/testbed/` holds the Caliper configuration only.
- **Proving runs on the CPU.** snarkjs does not use a GPU; `zk_benchmark.py` reports what the local
  machine does. With a real verifier, `updateTwinState` costs ≈408k gas for a machine's first update
  and ≈299k afterwards (Hardhat, solc 0.8.24, optimizer 200 runs).
- **Election RNG.** Python and Solidity apply the same eligibility and weighting rules with
  different PRNGs; the on-chain result is canonical. The RANDAO seed stops caller grinding but keeps
  RANDAO's residual proposer bias.
- **Subjective inputs.** Uptime U_i is reported by the committee (bounded to [0, 1]; it only
  affects the γ term and never slashes). ε_i counts invalid proofs; deadline misses are not tracked
  on-chain.
- The FlexFactory health model is a small feature-based regressor; swap in your own
  LSTM/transformer behind `HealthModel.health_score()`.
- `experiments/run_all.sh` simulates 12 hours with wear accelerated ×20.
- Cross-chain VWC bridging via LayerZero v2 is configured at deployment and not part of this repo.


## License

MIT — see [LICENSE](LICENSE).
