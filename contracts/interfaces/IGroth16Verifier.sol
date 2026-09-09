// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Interface of the snarkjs-generated Groth16 verifier (BN254).
interface IGroth16Verifier {
    function verifyProof(
        uint[2] calldata a,
        uint[2][2] calldata b,
        uint[2] calldata c,
        uint[] calldata pub
    ) external view returns (bool);
}
