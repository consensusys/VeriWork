// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "../interfaces/ITwinVerifier.sol";
import "../interfaces/IAggregatedVerifier.sol";

/// @dev Test double. Accepts a proof iff a[0] == keccak(abi.encode(pub)) % R,
///      and, like snarkjs verifiers, rejects any public signal >= R.
///      NEVER deploy: use the verifiers exported by circuits/build.sh.
contract MockGroth16Verifier is ITwinVerifier, IAggregatedVerifier {
    uint256 internal constant R = 21888242871839275222246405745257275088548364400416034343698204186575808495617;
    bool public alwaysAccept;

    constructor(bool _alwaysAccept) { alwaysAccept = _alwaysAccept; }

    function verifyProof(uint[2] calldata a, uint[2][2] calldata, uint[2] calldata, uint[8] calldata pub)
        external view override returns (bool)
    {
        for (uint256 i = 0; i < 8; i++) if (pub[i] >= R) return false;
        if (alwaysAccept) return true;
        return a[0] == uint256(keccak256(abi.encode(pub))) % R;
    }

    function verifyProof(uint[2] calldata a, uint[2][2] calldata, uint[2] calldata, uint[5] calldata pub)
        external view override returns (bool)
    {
        for (uint256 i = 0; i < 5; i++) if (pub[i] >= R) return false;
        if (alwaysAccept) return true;
        return a[0] == uint256(keccak256(abi.encode(pub))) % R;
    }
}
