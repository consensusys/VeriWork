# veriwork (Python reference node)

Python library of the VeriWork reference implementation: PoUW scoring (Eq. 3),
PoAW committee election (Eq. 2), multi-layer staking and dynamic slashing
(Eq. 1), the adaptive work controller, commit-reveal sequencing, batching,
prover backends (`SnarkJSProver`, insecure `MockProver`), the Bancor credit
market (Eq. 4-7) and `LocalL2`, the in-process L2 used by the FlexFactory
simulator and agents.  See the repository README for the full picture.

    pip install -e "node[dev]"      # from the repository root
