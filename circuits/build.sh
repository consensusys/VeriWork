#!/usr/bin/env bash
# Compile circuits, run a Groth16 trusted setup (dev ceremony), export the
# Solidity verifiers.  Requires: circom >= 2.1.6, snarkjs >= 0.7, circomlib.
#   npm i -g snarkjs && npm i circomlib
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p build
PTAU=build/pot22_final.ptau
if [ ! -f "$PTAU" ]; then
  echo ">> downloading powers-of-tau (2^22) — enough for the 480-sample telemetry circuit"
  curl -L -o "$PTAU" https://storage.googleapis.com/zkevm/ptau/powersOfTau28_hez_final_22.ptau
fi
for C in telemetry_attest qc_attest; do
  echo ">> compiling $C"
  circom "$C.circom" --r1cs --wasm --sym -o build -l node_modules
  snarkjs r1cs info "build/$C.r1cs"
  snarkjs groth16 setup "build/$C.r1cs" "$PTAU" "build/${C}_0000.zkey"
  echo "dev-entropy-$C" | snarkjs zkey contribute "build/${C}_0000.zkey" "build/${C}_final.zkey" --name="dev" -v
  snarkjs zkey export verificationkey "build/${C}_final.zkey" "build/${C}_vkey.json"
  snarkjs zkey export solidityverifier "build/${C}_final.zkey" "../contracts/generated/${C^}Verifier.sol"
done
echo ">> done. Verifiers in contracts/generated/. Replace MockGroth16Verifier in scripts/deploy.js."
