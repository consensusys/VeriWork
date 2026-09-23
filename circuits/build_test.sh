#!/usr/bin/env bash
# Build the small C_twin test instance (2 sensors x 4 samples) with a local
# *development* powers-of-tau, and export its Solidity verifier so the Hardhat
# Groth16 tests (test/TwinRegistry.groth16.test.js) run against real proofs.
# Requires: circom >= 2.1.6 on PATH (or CIRCOM=/path/to/circom) and
# `npm install` in this directory (circomlib, snarkjs).  Takes ~1 minute.
# NOT a trusted setup -- test use only.
set -euo pipefail
cd "$(dirname "$0")"
CIRCOM="${CIRCOM:-circom}"
SNARKJS="npx --no-install snarkjs"
OUT=build/test
mkdir -p "$OUT" ../contracts/generated

echo ">> compiling test circuit"
"$CIRCOM" test/telemetry_attest_test.circom --r1cs --wasm --sym --O2 -l node_modules -o "$OUT"
$SNARKJS r1cs info "$OUT/telemetry_attest_test.r1cs"

if [ ! -f "$OUT/pot12_final.ptau" ]; then
  echo ">> development powers of tau (2^12)"
  $SNARKJS powersoftau new bn128 12 "$OUT/pot12_0000.ptau"
  $SNARKJS powersoftau contribute "$OUT/pot12_0000.ptau" "$OUT/pot12_0001.ptau" --name="dev" -e="veriwork-test-phase1"
  $SNARKJS powersoftau prepare phase2 "$OUT/pot12_0001.ptau" "$OUT/pot12_final.ptau"
fi

echo ">> Groth16 setup (circuit-specific phase 2)"
$SNARKJS groth16 setup "$OUT/telemetry_attest_test.r1cs" "$OUT/pot12_final.ptau" "$OUT/telemetry_attest_test_0000.zkey"
$SNARKJS zkey contribute "$OUT/telemetry_attest_test_0000.zkey" "$OUT/telemetry_attest_test_final.zkey" \
  --name="dev" -e="veriwork-test-phase2"
$SNARKJS zkey export verificationkey "$OUT/telemetry_attest_test_final.zkey" "$OUT/telemetry_attest_test_vkey.json"
$SNARKJS zkey export solidityverifier "$OUT/telemetry_attest_test_final.zkey" ../contracts/generated/TwinVerifierTest.sol
sed -i 's/contract Groth16Verifier/contract TwinVerifierTest/' ../contracts/generated/TwinVerifierTest.sol
echo ">> done: $OUT/ and contracts/generated/TwinVerifierTest.sol"
