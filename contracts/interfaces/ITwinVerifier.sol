// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice snarkjs-exported Groth16 verifier for C_twin (BN254).
///         Public signals, in circuit order:
///         [twinHash, inputHash, boundsHash, prevStateRoot,
///          healthScore, cycleCount, machineId, submitter]
///         snarkjs verifiers take a fixed-size array, so the arity is part
///         of the ABI selector.
interface ITwinVerifier {
    function verifyProof(
        uint[2] calldata a,
        uint[2][2] calldata b,
        uint[2] calldata c,
        uint[8] calldata pub
    ) external view returns (bool);
}
