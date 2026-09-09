# Architecture notes

This document maps the paper's design to the code and records the design
decisions that are *not* obvious from the paper text.

## 1. Data flow for one telemetry window (Algorithm 1)

```
edge worker                 L2 sequencer (committee)              L1
-----------                 ------------------------              --
window = 60 s × 8 sensors
state  = f(samples)
H_twin = Poseidon(sum,N,peak,cycles,flags)
π      = Groth16.Prove(C_twin, [H_in,H_twin,lo,hi], w)
CID    = ipfs.add(attestation)
submit(CID, H_twin, π)  ──►  BatchBuilder.add()
                             ... window closes (60 s or 4096 tasks) ...
                             verify each π (15 ms) ─► valid / invalid
                             stateRoot = H(prev ‖ H_twin*)
                             orderRoot = H(chain of commitments)
                             π_agg = aggregate(valid π)
                             postBatch(stateRoot, orderRoot, π_agg) ─► VeriWorkRollup
                                                                        verifyProof(π_agg)
                                                                        blobhash(0) → DA
                             reward valid workers; ε_i for invalid ones
```

Rewards are credited on L2 immediately after local verification; L1 posting is
asynchronous.  A batch whose aggregated proof fails on L1 cannot advance
`latestRoot`, so an L2 sequencer that lied about local verification simply
cannot settle — and is slashed via `challenge()` if it used the optimistic path.

## 2. Why the election is "top-k, then sortition"

Pure `P(n_i) ∝ σ_i S_i` (Eq. 2) is what the paper analyses.  Implemented
literally on-chain it is O(n) per seat and lets a node with enormous stake but
mediocre S_i buy a seat in every epoch.  The hybrid form in
`SequencerElection.advanceEpoch()`:

1. filters to nodes with `own ≥ minBond` and `S_i > 0`;
2. keeps the top `kEligible` by S_i (partial selection sort, O(n·k));
3. draws `committeeSize` seats from that set with weight `σ_i S_i · rep_i`,
   without replacement, seeded by `keccak(seed, epoch, seat)`.

Step 2 is what makes capital insufficient: no amount of stake gets a node with
a low PoUW score into the eligibility set.  Off-chain nodes recompute the same
draw (`node/veriwork/consensus/selection.py`) — the two implementations use
the same ordering rules so they agree bit-for-bit on the eligibility set; the
sortition RNGs differ (SHA-256 vs keccak) and the on-chain result is canonical.

## 3. Slashing is objective

`VeriWorkRollup.reportEpoch()` derives `ε_i = (submitted − valid)/submitted`
from verification outcomes and calls `PoAWStaking.slash()`.  No vote, no
fraud-proof race, no subjective evidence.  Delegations are slashed pro-rata
with the operator's own bond (delegators share operator risk).  Restaked
positions are re-capped after every slash so they never exceed
`restakeCapBps` of the *current* bond.

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

## 6. Proof aggregation

`ProverBackend.aggregate()` is the seam.  The mock backend hashes proof tags;
the production path recursively verifies M Groth16 proofs inside a PLONK
circuit (paper Sec. IV-D).  Because verifier cost is independent of M, the
`VeriWorkRollup` contract does not care which aggregator is plugged in — it
only requires the aggregated proof's public inputs to be
`[prevRoot, stateRoot, orderRoot, valid, invalid]`.

## 7. Agent safety rails (Sec. VII-E)

| Rail | Where |
|---|---|
| whitelisted providers / suppliers | `AgentSafetyRails`, `FlexFactoryTwinRegistry.whitelistAgent()` |
| per-order escrow cap ⇒ human review | `AgentSafetyRails.max_escrow_per_order`, `humanReviewThreshold` |
| daily budget | `AgentSafetyRails.daily_budget_vwc` |
| DAO veto | `governance` role on every contract |
| portable reputation | ERC-8004 registries via `AgentRegistry8004Adapter.screen()` |

## 8. 2026 protocol context

- Batch data is posted as EIP-4844 blobs (`blobhash(0)` in `postBatch`), whose
  cost fell after PeerDAS (Fusaka, Dec 2025).
- For tasks without a hand-written circuit the optimistic 7-day fallback
  applies; a zkVM backend (SP1 / RISC Zero) can replace it by compiling the task
  binary to RISC-V and implementing `ProverBackend`.
