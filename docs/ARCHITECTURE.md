# Architecture notes

This document maps the paper's design to the code and records the design
decisions that are *not* obvious from the paper text.

## 1. Data flow for one telemetry window (Algorithm 1)

```
edge gateway (operator)                         VeriWork L2                              L1
-----------------------                         -----------                              --
window = 60 s × 8 sensors × 60 samples
health = model(window)                    (off-circuit, committed below)
π, [H_twin, H_in, H_bounds, ...] = Groth16.Prove(C_twin,
      public: prevStateRoot, health, cycles, machineId, submitter;
      private: samples, lo[], hi[], flags)
CID = ipfs.add(attestation)
updateTwinState(machineId, H_twin, H_in, health, cycles, π) ─► FlexFactoryTwinRegistry
                                        rebuilds the 8 public signals from its own state
                                        (prior root, registered bounds hash, machineId,
                                        msg.sender); verify(π) → update twin, or
                                        badProofs[operator]++ and InvalidProof
                                        ... every N blocks ...
                                        sequencer posts (stateRoot, orderRoot, π_agg) ─────► VeriWorkRollup.postBatch
                                                                                             advanceEpoch(): ε_i from
                                                                                             the registries' counters
```

The operator (edge gateway) is the only address allowed to update its machine;
cycle counts may not decrease, and because each proof binds the prior state
root, an old proof cannot be re-applied.  Stand-alone tasks (`TaskRegistry`)
reuse C_twin with the task id in the `machineId` slot and `prevStateRoot = 0`;
binding `submitter` means a proof copied from the mempool fails for the copier.

## 2. Why the election is "top-k, then sortition"

Pure `P(n_i) ∝ σ_i S_i` (Eq. 2) is what the paper analyses.  Implemented
literally on-chain it is O(n) per seat and lets a node with enormous stake but
mediocre S_i buy a seat in every epoch.  The hybrid form in
`SequencerElection.advanceEpoch()`:

1. filters to nodes with `own ≥ minBond` and `S_i > 0`;
2. keeps the top `kEligible` by S_i (partial selection sort, O(n·k));
3. draws `committeeSize` seats from that set with weight `σ_i S_i · rep_i`,
   without replacement, seeded by `keccak(prevrandao, epoch, rollup)` — the
   caller cannot choose the seed.

Step 2 is what makes capital insufficient: no amount of stake gets a node with
a low PoUW score into the eligibility set.  Off-chain nodes recompute the draw
(`node/veriwork/consensus/selection.py`) with the same eligibility and
weighting rules; the sortition PRNGs differ (SHA-256 vs keccak) and the
on-chain result is canonical.

## 3. Slashing is objective

`FlexFactoryTwinRegistry` and `TaskRegistry` verify proofs on-chain and count,
per submitting address, how many verified and how many failed
(`IOutcomeSource.outcomes`).  At each epoch boundary
`VeriWorkRollup.advanceEpoch()` reads those counters for every bonded node,
takes the counts not yet settled, computes `ε_i = invalid / (valid + invalid)`
and calls `PoAWStaking.slash()`.  No party reports ε_i, so neither a single
sequencer nor a majority can slash a node whose proofs verified.
Delegations are share-based, so a slash reduces each delegator's position pro
rata; restaked positions are re-capped to `restakeCapBps` of the reduced bond.

Limits: ε_i counts invalid proofs only (deadline misses are not tracked
on-chain), and uptime `U_i` is committee-reported — bounded to [0, 1], used
only in the γ term of S_i, never for slashing.  If the committee stalls,
anyone may advance the epoch after `EPOCH_TIMEOUT` (1 day).

## 4. MEV resistance and what it does not cover

`CommitRevealOrdering` hides transaction contents from the sequencer for one
block and fixes the intra-batch order to ascending `(blockCommitted, c)`.
`orderRoot` over that sequence is a public input of the batch proof, so a
sequencer that reorders produces a proof for a different root and is caught by
`challenge()`.  Residual leakage: commitment *timing* and *sender* remain
visible.  This complements Ethereum's ePBS (Glamsterdam) and FOCIL (Hegotá),
which discipline L1 proposers rather than L2 sequencers.

## 5. Credit market maths on-chain

Bancor's fractional powers are expensive in the EVM.  `CreditMarket` supports
`W_a ∈ {¼, ½, 1}` exactly using integer square roots; other weights are served
by the L2 execution client (`market/bonding_curve.py`) with the on-chain
contract acting as settlement.  The adaptive multiplier is a 4-sample EMA of
`demand/supply` reported by governance each pricing window; the surcharge under
congestion accrues to the reserve, which raises the curve price for all holders.

## 6. Proof aggregation (not included)

`ProverBackend.aggregate()` is the seam.  The mock backend hashes proof tags;
the design in the paper (Sec. IV-E) recursively verifies M Groth16 proofs
inside a PLONK circuit.  **No aggregator circuit ships with this release.**
`VeriWorkRollup` only requires the aggregated proof's public inputs to be
`[prevRoot, stateRoot, orderRoot, valid, invalid]` and uses whatever verifier
it is deployed with (the mock by default).

## 7. Agent safety rails (Sec. VIII-D)

| Rail | On-chain (`FlexFactoryTwinRegistry`) | Client-side mirror (`AgentSafetyRails`) |
|---|---|---|
| capability whitelist | `whitelistAgent()` (with ERC-8004 id); escrow only to `setProvider()`-approved providers | `whitelisted_providers`, `whitelisted_suppliers` |
| human review above a threshold | `humanReviewThreshold` → `PendingReview` → governance `reviewOrder()` approves or refunds | `max_escrow_per_order` → `pending_human_review` |
| daily spending limit | `dailyAgentCap` per agent per UTC day | `daily_budget_vwc` |
| DAO veto | `suspendAgent()` (governance) | — |
| portable reputation | ERC-8004 registries via `AgentRegistry8004Adapter.screen()` | — |

Maintenance is paid out only after a fresh ZK-attested twin update, made after
scheduling, shows health back at or above `CRITICAL_HEALTH` (40).

## 8. 2026 protocol context

- Batch data is posted as EIP-4844 blobs (`blobhash(0)` in `postBatch`), whose
  cost fell after PeerDAS (Fusaka, Dec 2025).
- For tasks without a hand-written circuit the optimistic 7-day fallback
  applies; a zkVM backend (SP1 / RISC Zero) can replace it by compiling the task
  binary to RISC-V and implementing `ProverBackend`.
