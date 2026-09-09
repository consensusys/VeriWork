// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "../interfaces/IGroth16Verifier.sol";

/// @dev Test double. Accepts a proof iff a[0] == keccak(pub) % R — mirrors the
///      Python MockProver so simulator-generated proofs verify in Hardhat tests.
///      NEVER deploy: replace with the snarkjs-generated verifier from circuits/build.
contract MockGroth16Verifier is IGroth16Verifier {
    uint256 internal constant R = 21888242871839275222246405745257275088548364400416034343698204186575808495617;
    bool public alwaysAccept;

    constructor(bool _alwaysAccept) { alwaysAccept = _alwaysAccept; }

    function verifyProof(uint[2] calldata a, uint[2][2] calldata, uint[2] calldata, uint[] calldata pub)
        external view override returns (bool)
    {
        if (alwaysAccept) return true;
        return a[0] == uint256(keccak256(abi.encode(pub))) % R;
    }
}
