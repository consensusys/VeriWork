// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title MEV-resistant commit–reveal ordering (paper Sec. III-D).
/// @notice c = keccak256(tx || r) is committed in block N and revealed in
///         block N+1..N+1+window.  The canonical batch order is ascending
///         (blockCommitted, commitment), which the batch proof attests.
contract CommitRevealOrdering {
    uint256 public immutable revealWindow;   // in L2 blocks

    struct Commit { address sender; uint64 blockCommitted; bool revealed; }
    mapping(bytes32 => Commit) public commits;

    event Committed(bytes32 indexed c, address indexed sender, uint64 blockNo);
    event Revealed(bytes32 indexed c, address indexed sender, bytes tx);

    constructor(uint256 _revealWindow) { revealWindow = _revealWindow; }

    function commitTx(bytes32 c) external {
        require(commits[c].sender == address(0), "dup");
        commits[c] = Commit(msg.sender, uint64(block.number), false);
        emit Committed(c, msg.sender, uint64(block.number));
    }

    function revealTx(bytes calldata txData, bytes32 r) external returns (bytes32 c) {
        c = keccak256(abi.encodePacked(txData, "||", r));
        Commit storage k = commits[c];
        require(k.sender == msg.sender, "sender/commit mismatch");
        require(!k.revealed, "already");
        require(block.number >= k.blockCommitted + 1, "too early");
        require(block.number <= k.blockCommitted + 1 + revealWindow, "expired");
        k.revealed = true;
        emit Revealed(c, msg.sender, txData);
    }

    /// @dev Order root over the canonical sequence; sequencers must post the
    ///      same root inside the batch proof's public inputs.
    function orderRoot(bytes32[] calldata orderedCommits) external pure returns (bytes32 h) {
        h = keccak256("veriwork-order-v1");
        for (uint256 i = 0; i < orderedCommits.length; i++) {
            if (i > 0) require(orderedCommits[i] > orderedCommits[i - 1], "not canonical");
            h = keccak256(abi.encodePacked(h, orderedCommits[i]));
        }
    }
}
