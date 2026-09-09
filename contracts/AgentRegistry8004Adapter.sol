// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Minimal surface of the ERC-8004 Identity and Reputation registries
///         (De Rossi, Crapis, Ellis, Reppel, 2025) used by FlexFactory agents.
interface IERC8004Identity {
    function register(string calldata agentURI) external returns (uint256 agentId);
    function ownerOf(uint256 agentId) external view returns (address);
}

interface IERC8004Reputation {
    function giveFeedback(uint256 agentId, int128 value, uint8 valueDecimals,
                          string calldata tag1, string calldata tag2, string calldata endpoint,
                          string calldata feedbackURI, bytes32 feedbackHash) external;
    function getSummary(uint256 agentId, address[] calldata clientAddresses,
                        string calldata tag1, string calldata tag2)
        external view returns (uint64 count, int128 summaryValue, uint8 summaryValueDecimals);
}

/// @title Adapter that lets FlexFactory operators post verifiable feedback for
///        service agents after each completed maintenance order, and lets the
///        twin registry screen agents against their ERC-8004 track record.
contract AgentRegistry8004Adapter {
    IERC8004Identity public immutable identity;
    IERC8004Reputation public immutable reputation;
    address public immutable twinRegistry;

    event FeedbackPosted(uint256 indexed agentId, int128 value, string tag1);

    constructor(IERC8004Identity _id, IERC8004Reputation _rep, address _twinRegistry) {
        identity = _id; reputation = _rep; twinRegistry = _twinRegistry;
    }

    /// @notice Post `successRate`-style feedback (0-100) with an x402 payment proof hash.
    function postMaintenanceFeedback(uint256 agentId, uint8 score, string calldata feedbackURI, bytes32 proofOfPaymentHash) external {
        require(score <= 100, "score");
        reputation.giveFeedback(agentId, int128(uint128(score)), 0, "starred", "maintenance", "", feedbackURI, proofOfPaymentHash);
        emit FeedbackPosted(agentId, int128(uint128(score)), "starred");
    }

    /// @notice Screening helper: average starred score from a trusted reviewer set.
    function screen(uint256 agentId, address[] calldata trustedReviewers, int128 minScore)
        external view returns (bool ok, int128 avg)
    {
        (uint64 n, int128 v, ) = reputation.getSummary(agentId, trustedReviewers, "starred", "");
        avg = n == 0 ? int128(0) : v;
        ok = n > 0 && avg >= minScore;
    }
}
