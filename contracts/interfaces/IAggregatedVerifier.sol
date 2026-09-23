// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Verifier of the aggregated batch proof posted to L1.
///         Public signals: [prevRoot, stateRoot, orderRoot, valid, invalid].
///         The aggregator circuit is not part of this release (see README).
interface IAggregatedVerifier {
    function verifyProof(
        uint[2] calldata a,
        uint[2][2] calldata b,
        uint[2] calldata c,
        uint[5] calldata pub
    ) external view returns (bool);
}
