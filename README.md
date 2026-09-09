# VeriWork

**A Layer 2 Proof-of-Adaptive-Work Platform for Web4 dApps with Zero-Knowledge Verification**

Reference implementation accompanying the paper:

> Z. Dong, Y. C. Lee, A. Y. Zomaya. *VeriWork: A Layer 2 Proof-of-Adaptive-Work Platform for Web4 dApps with Zero-Knowledge Verification.* PDCAT 2026.
> (Builds on the PoUW protocol of [Proofware, arXiv:1903.09276](https://arxiv.org/abs/1903.09276), but is fully self-contained.)

VeriWork repositions Proof of Useful Work (PoUW) as an Ethereum L2 rollup governed by
**Proof-of-Adaptive-Work (PoAW)**: multi-layer staking (bonds, delegation, restaking),
objective ZK-derived slashing, stake-and-contribution-weighted sequencer election, and
MEV-resistant commit–reveal ordering. Worker computation is attested by Groth16 proofs,
and a Web4 AI-agent layer (MCP · A2A · x402 · ERC-8004) operates dApps autonomously.
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
| `contracts/` | Solidity 0.8.24 (Cancun): `VWCToken`, `PoAWStaking`, `SequencerElection`, `VeriWorkRollup`, `CommitRevealOrdering`, `CreditMarket`, `TaskRegistry`, `FlexFactoryTwinRegistry`, `AgentRegistry8004Adapter` | III, IV, V, VII |
| `circuits/` | Circom 2.1 circuits `telemetry_attest` (C_twin) and `qc_attest` (C_qc) + Groth16 build script | IV-D |
| `node/veriwork/` | Python node library: PoUW score (Eq. 3), hybrid committee election (Eq. 2), multi-layer staking + dynamic slashing (Eq. 1), adaptive work controller, commit–reveal pool, batcher, prover backends, Bancor curve + adaptive pricing (Eq. 4–5), SDK with in-process `LocalL2` | III, V |
| `agents/` | Web4 agent layer: model-agnostic planner, `FactoryAgent` loop (Listing 2), MCP server, A2A agent card, x402 payment client, ERC-8004 registration | IV-C, V-D |
| `flexfactory/` | Factory-floor simulator, health model, end-to-end run script | VI-D |
| `experiments/` | Consensus scaling model (Fig. 3–4), ZK benchmark, Caliper testbed config, `run_all.sh` | VI |
| `tests/`, `test/` | pytest (29) and Hardhat (15) suites | — |

## Quick start

```bash
git clone https://github.com/andrewdong14/veriwork && cd veriwork

# Python: node library, simulator, agents
pip install -e "node[dev]"
python -m pytest tests -q                          # 29 passed

# End-to-end FlexFactory run on the in-process L2 (accelerated wear so
# the agent schedules maintenance inside a 3-hour simulated window)
python -m flexfactory.run_simulation --minutes 180 --machines 50 --accel 50

# Solidity
npm install
npx hardhat test                                   # 15 passing
npx hardhat run scripts/deploy.js                  # in-memory deployment

# Consensus scaling model + plot
python experiments/consensus_benchmark.py --plot
```

Sample output of the FlexFactory run (180 min, 50 machines, 10 edge workers, 2 % tampered windows):

```
verified=8833 rejected=167  tampered_injected=167  rejection_matches_tampering=true
maintenance_orders=126  maintenance_flag_accuracy=0.986  mean_event_to_attestation_s=67.3
epoch 3: committee=['edge-09','edge-06','edge-01',...]   stake_after_slashing: {...}
```

Every tampered telemetry window is rejected by proof verification, the offending worker's
stake is slashed at the epoch boundary, and the agent escrows VWC for maintenance whenever a
ZK-attested twin drops below the health threshold.

## How the pieces map to the paper

**PoAW consensus (Sec. III).**
`node/veriwork/consensus/` and `contracts/PoAWStaking.sol` + `SequencerElection.sol`.

- PoUW score `S_i = α·T_valid/T_sub + β·V_i/V_max + γ·U_i/U_max` — `pouw_score.py`, `SequencerElection.score()`
- Election `P(n_i) = σ_i S_i / Σ σ_j S_j`, instantiated as *top-k by S_i, then stake-weighted
  sortition without replacement* seeded by the epoch beacon — `selection.py`, `SequencerElection.advanceEpoch()`
- Dynamic slashing `σ_i ← σ_i (1 − λ ε_i)` with ε_i taken from on-chain Groth16 outcomes, applied
  pro-rata over own bond and delegations; per-service restaking caps — `staking.py`, `PoAWStaking.slash()`
- Adaptive work quotas — `adaptive_work.py`
- MEV resistance: `c = H(tx‖r)` committed in block N, revealed in N+1, canonical order attested
  in the batch proof; deviation is slashable — `sequencer/commit_reveal.py`, `CommitRevealOrdering.sol`

**ZK layer (Sec. IV-D).** `circuits/telemetry_attest.circom` proves 480 readings lie in `[lo,hi]`
and hashes to the public `H_twin`; `qc_attest.circom` proves tolerance compliance without revealing
measurements. `node/veriwork/zk/prover.py` wraps snarkjs (`SnarkJSProver`) and ships an
explicitly *insecure* `MockProver` with the same interface for tests and the simulator.

**Credit market (Sec. V-C).** Bancor curve `P = B_a/(O_m W_a)` with buy/sell/swap in
`market/bonding_curve.py` and `CreditMarket.sol` (W ∈ {¼, ½, 1} evaluated exactly on-chain).
Adaptive layer `P_c = P_0 (1 + α_p D_r/S_r)` over a TWAP of excess demand in
`market/adaptive_pricing.py` / `adaptiveMultiplier()`.

**Web4 agents (Sec. IV-C, V-D, VII-E).** `agents/factory_agent.py` implements the
sense → plan → act loop of Listing 2 behind safety rails (whitelists, per-order cap with
human review above threshold, daily budget). Agents expose tools over **MCP**
(`mcp_server.py`), advertise skills via an **A2A** agent card, settle machine-to-machine
payments over **x402**, and register identity/reputation under **ERC-8004**
(`erc8004.py`, `AgentRegistry8004Adapter.sol`).

## Status and honest caveats

- `MockGroth16Verifier` / `MockProver` are test doubles. Run `circuits/build.sh` and wire the
  generated verifiers into `scripts/deploy.js` before any real deployment.
- PLONK recursive aggregation of Groth16 proofs is specified (`ProverBackend.aggregate`) but the
  aggregator circuit is not included in this release; the rollup contract verifies whatever
  aggregated proof the configured verifier accepts.
- `experiments/consensus_benchmark.py` is a **calibrated scaling model** documenting the
  mechanisms behind Fig. 3–4; the raw 100-node testbed harness (Caliper/Locust configs) is in
  `experiments/testbed/`.
- The FlexFactory health model is a small feature-based regressor; swap in your own LSTM/transformer
  behind `HealthModel.health_score()`.
- Cross-chain VWC bridging via LayerZero v2 is configured at deployment and not part of this repo.

## Cite

```bibtex
@inproceedings{dong2026veriwork,
  title     = {VeriWork: A Layer 2 Proof-of-Adaptive-Work Platform for Web4 dApps with Zero-Knowledge Verification},
  author    = {Dong, Zhongli and Lee, Young Choon and Zomaya, Albert Y.},
  booktitle = {Proc. 27th International Conference on Parallel and Distributed Computing, Applications and Technologies (PDCAT)},
  year      = {2026}
}
```

## License

MIT — see [LICENSE](LICENSE).
