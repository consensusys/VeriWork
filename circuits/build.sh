#!/usr/bin/env bash
# Compile the production circuits, run a Groth16 setup on the public Hermez
# powers-of-tau (phase 1) with a *development* phase-2 contribution, and export
# the Solidity verifiers to ../contracts/generated/.
#   C_twin (8 sensors x 60 samples): 68,806 constraints -> 2^17 ptau suffices.
# Requires circom >= 2.1.6 (or CIRCOM=/path/to/circom) and `npm install` here.
# A production deployment needs a multi-party phase-2 ceremony instead of the
# single dev contribution below.
set -euo pipefail
cd "$(dirname "$0")"
CIRCOM="${CIRCOM:-circom}"
SNARKJS="npx --no-install snarkjs"
mkdir -p build ../contracts/generated
PTAU=build/powersOfTau28_hez_final_17.ptau
if [ ! -f "$PTAU" ]; then
  echo ">> downloading Hermez powers-of-tau (2^17, ~144 MB)"
  curl -L -o "$PTAU" https://storage.googleapis.com/zkevm/ptau/powersOfTau28_hez_final_17.ptau
fi
declare -A NAME=( [telemetry_attest]=TelemetryAttestVerifier [qc_attest]=QcAttestVerifier )
for C in telemetry_attest qc_attest; do
  echo ">> compiling $C"
  "$CIRCOM" "$C.circom" --r1cs --wasm --sym --O2 -l node_modules -o build
  $SNARKJS r1cs info "build/$C.r1cs"
  $SNARKJS groth16 setup "build/$C.r1cs" "$PTAU" "build/${C}_0000.zkey"
  $SNARKJS zkey contribute "build/${C}_0000.zkey" "build/${C}_final.zkey" --name="dev" -e="dev-entropy-$C-$RANDOM$RANDOM"
  $SNARKJS zkey export verificationkey "build/${C}_final.zkey" "build/${C}_vkey.json"
  $SNARKJS zkey export solidityverifier "build/${C}_final.zkey" "../contracts/generated/${NAME[$C]}.sol"
  # snarkjs names every verifier Groth16Verifier; give each a unique name
  sed -i "s/contract Groth16Verifier/contract ${NAME[$C]}/" "../contracts/generated/${NAME[$C]}.sol"
done
echo ">> done. Deploy with: TWIN_VERIFIER=TelemetryAttestVerifier npx hardhat run scripts/deploy.js"
