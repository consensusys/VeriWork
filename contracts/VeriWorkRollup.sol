// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./interfaces/IGroth16Verifier.sol";
import "./PoAWStaking.sol";
import "./SequencerElection.sol";

/// @title VeriWork L1 anchor: batch commitments + aggregated ZK verification.
/// @notice Every N L2 blocks an elected sequencer posts
///         (stateRoot, orderRoot, blobVersionedHash, aggregated proof).
///         Data availability rides on EIP-4844 blobs (PeerDAS-sampled since
///         Fusaka); the versioned hash is read from the blob tx via BLOBHASH.
///         Tasks without a circuit use the 7-day optimistic fallback.
contract VeriWorkRollup {
    uint256 public constant WAD = 1e18;
    uint256 public constant FRAUD_WINDOW = 7 days;

    IGroth16Verifier public immutable aggVerifier;   // PLONK/Groth16 aggregator verifier
    PoAWStaking public immutable staking;
    SequencerElection public immutable election;

    struct BatchHeader {
        bytes32 prevRoot;
        bytes32 stateRoot;
        bytes32 orderRoot;
        bytes32 blobHash;
        uint64  postedAt;
        address sequencer;
        uint32  validTasks;
        uint32  invalidTasks;
        bool    optimistic;      // true if any task used the fraud-proof fallback
        bool    reverted;
    }
    BatchHeader[] public batches;
    bytes32 public latestRoot;

    event BatchPosted(uint256 indexed id, bytes32 stateRoot, bytes32 orderRoot, bytes32 blobHash, address sequencer);
    event BatchReverted(uint256 indexed id, address challenger);
    event EpsilonReported(address indexed node, uint256 epsilonWad);

    constructor(IGroth16Verifier _agg, PoAWStaking _staking, SequencerElection _election, bytes32 genesis) {
        aggVerifier = _agg; staking = _staking; election = _election; latestRoot = genesis;
    }

    /// @dev Public inputs of the aggregated proof are derived on-chain:
    ///        [prevRoot, stateRoot, orderRoot, validCount, invalidCount] as field elements.
    function postBatch(
        bytes32 stateRoot,
        bytes32 orderRoot,
        uint32 validTasks,
        uint32 invalidTasks,
        bool optimistic,
        uint[2] calldata a, uint[2][2] calldata b, uint[2] calldata c
    ) external {
        require(election.isSequencer(msg.sender), "not elected sequencer");
        bytes32 blobHash;
        assembly { blobHash := blobhash(0) }     // EIP-4844; zero if no blob attached
        uint[] memory pub = new uint[](5);
        pub[0] = uint256(latestRoot) % _R();
        pub[1] = uint256(stateRoot) % _R();
        pub[2] = uint256(orderRoot) % _R();
        pub[3] = validTasks;
        pub[4] = invalidTasks;
        require(aggVerifier.verifyProof(a, b, c, pub), "invalid aggregated proof");

        batches.push(BatchHeader({
            prevRoot: latestRoot, stateRoot: stateRoot, orderRoot: orderRoot, blobHash: blobHash,
            postedAt: uint64(block.timestamp), sequencer: msg.sender,
            validTasks: validTasks, invalidTasks: invalidTasks, optimistic: optimistic, reverted: false
        }));
        latestRoot = stateRoot;
        emit BatchPosted(batches.length - 1, stateRoot, orderRoot, blobHash, msg.sender);
    }

    /// @notice Fraud-proof fallback for optimistic tasks: a challenger re-executes
    ///         and supplies a Groth16 proof of the *correct* result that contradicts
    ///         the posted state root. Verified by the same aggregator verifier over
    ///         [prevRoot, claimedRoot, orderRoot, 0, 1].
    function challenge(uint256 id, bytes32 correctRoot,
                       uint[2] calldata a, uint[2][2] calldata b, uint[2] calldata c) external {
        BatchHeader storage h = batches[id];
        require(h.optimistic && !h.reverted, "not challengeable");
        require(block.timestamp <= h.postedAt + FRAUD_WINDOW, "window closed");
        require(correctRoot != h.stateRoot, "same root");
        uint[] memory pub = new uint[](5);
        pub[0] = uint256(h.prevRoot) % _R(); pub[1] = uint256(correctRoot) % _R();
        pub[2] = uint256(h.orderRoot) % _R(); pub[3] = 0; pub[4] = 1;
        require(aggVerifier.verifyProof(a, b, c, pub), "bad fraud proof");
        h.reverted = true;
        if (id == batches.length - 1) latestRoot = h.prevRoot;
        staking.slashOrderingViolation(h.sequencer);
        emit BatchReverted(id, msg.sender);
    }

    /// @notice Report a node's epoch invalid rate to staking (objective slashing)
    ///         and its outcome to the election contract. Callable by the current
    ///         committee (aggregated results are attested inside batch proofs).
    function reportEpoch(address node, uint64 valid, uint64 submitted, uint128 volume, uint64 uptimeWad) external {
        require(election.isSequencer(msg.sender), "not sequencer");
        uint256 eps = submitted == 0 ? 0 : ((submitted - valid) * WAD) / submitted;
        if (eps > 0) staking.slash(node, eps);
        for (uint64 i = 0; i < submitted; i++) {
            election.recordOutcome(node, i < valid, i < valid ? volume / (valid == 0 ? 1 : valid) : 0, uptimeWad);
        }
        emit EpsilonReported(node, eps);
    }

    function advanceEpoch(bytes32 seed) external {
        require(election.isSequencer(msg.sender) || election.committeeSizeNow() == 0, "not sequencer");
        election.advanceEpoch(seed);
    }

    function batchCount() external view returns (uint256) { return batches.length; }

    function _R() internal pure returns (uint256) {
        return 21888242871839275222246405745257275088548364400416034343698204186575808495617;
    }
}
