// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice A contract that verifies ZK proofs on-chain and records, per
///         submitting node, how many verified and how many failed.  The
///         rollup reads these counters to compute epsilon_i (paper Eq. 1),
///         so slashing depends only on recorded verification outcomes.
interface IOutcomeSource {
    function outcomes(address node)
        external view returns (uint256 valid, uint256 invalid);
}
