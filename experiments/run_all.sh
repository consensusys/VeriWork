#!/usr/bin/env bash
# Reproduce the software-level experiments of the paper.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "== unit tests =="; python3 -m pytest tests -q
echo "== consensus cost model (Figs. 3-4: modelled, not measured) =="; python3 experiments/consensus_benchmark.py --plot || python3 experiments/consensus_benchmark.py
echo "== FlexFactory 12-hour simulated run (wear accelerated x20) =="; python3 -m flexfactory.run_simulation --minutes 720 --machines 50 --accel 20 --out experiments/results/flexfactory_run.json
echo "== ZK benchmark (needs circuits/build.sh; --test uses circuits/build_test.sh) =="; python3 experiments/zk_benchmark.py
echo "results in experiments/results/"
