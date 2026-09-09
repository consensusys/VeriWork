# Contributing

1. `pip install -e "node[dev]"` and `npm install`.
2. Run `python -m pytest tests -q` and `npx hardhat test` before opening a PR.
3. Keep the Python and Solidity implementations of PoUW scoring, election,
   slashing and pricing in lock-step — both are covered by tests that encode
   the paper's equations; change the equation in one place and the tests will
   tell you where else to update.
4. Never commit real keys. Deployment credentials come from environment
   variables (`SEPOLIA_RPC_URL`, `DEPLOYER_KEY`, `GOVERNANCE`).
