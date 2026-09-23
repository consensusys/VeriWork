// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./interfaces/IAggregatedVerifier.sol";
import "./interfaces/IOutcomeSource.sol";
import "./PoAWStaking.sol";
import "./SequencerElection.sol";

/// @title VeriWork L1 anchor: batch commitments, aggregated ZK verification
///        and objective epoch settlement.
/// @notice Every N L2 blocks an elected sequencer posts
///         (stateRoot, orderRoot, blobVersionedHash, aggregated proof).
///         Data availability rides on EIP-4844 blobs (PeerDAS-sampled since
///         Fusaka); the versioned hash is read from the blob tx via BLOBHASH.
///         Tasks without a circuit use the 7-day optimistic fallback.
///
///         Slashing is objective: at each epoch boundary epsilon_i is computed
///         from the valid/invalid counters that the verifying contracts
///         (FlexFactoryTwinRegistry, TaskRegistry) record per submitter.  No
///         party reports epsilon, so no committee member -- or majority -- can
///         slash a node for proofs that verified.
contract VeriWorkRollup {
    uint256 public constant WAD = 1e18;
    uint256 public constant FRAUD_WINDOW = 7 days;
    uint256 public constant EPOCH_TIMEOUT = 1 days;   // liveness: anyone may advance after this

    IAggregatedVerifier public immutable aggVerifier;   // PLONK/Groth16 aggregator verifier
    PoAWStaking public immutable staking;
    SequencerElection public immutable election;
    address public immutable governance;

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

    IOutcomeSource[] public outcomeSources;
    struct Settled { uint128 valid; uint128 invalid; }
    mapping(address => Settled) public settled;          // counters already settled
    uint256 public lastEpochAt;

    event BatchPosted(uint256 indexed id, bytes32 stateRoot, bytes32 orderRoot, bytes32 blobHash, address sequencer);
    event BatchReverted(uint256 indexed id, address challenger);
    event OutcomeSourceAdded(address source);
    event EpsilonReported(address indexed node, uint256 valid, uint256 invalid, uint256 epsilonWad);

    modifier onlyGov() { require(msg.sender == governance, "not gov"); _; }

    constructor(IAggregatedVerifier _agg, PoAWStaking _staking, SequencerElection _election,
                bytes32 genesis, address _gov) {
        aggVerifier = _agg; staking = _staking; election = _election; latestRoot = genesis;
        governance = _gov;
    }

    function addOutcomeSource(IOutcomeSource s) external onlyGov {
        outcomeSources.push(s);
        emit OutcomeSourceAdded(address(s));
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
        uint[5] memory pub = [
            uint256(latestRoot) % _R(), uint256(stateRoot) % _R(), uint256(orderRoot) % _R(),
            uint256(validTasks), uint256(invalidTasks)
        ];
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
    ///         and supplies a proof of the *correct* result that contradicts
    ///         the posted state root. Verified by the same aggregator verifier over
    ///         [prevRoot, claimedRoot, orderRoot, 0, 1].
    function challenge(uint256 id, bytes32 correctRoot,
                       uint[2] calldata a, uint[2][2] calldata b, uint[2] calldata c) external {
        BatchHeader storage h = batches[id];
        require(h.optimistic && !h.reverted, "not challengeable");
        require(block.timestamp <= h.postedAt + FRAUD_WINDOW, "window closed");
        require(correctRoot != h.stateRoot, "same root");
        uint[5] memory pub = [
            uint256(h.prevRoot) % _R(), uint256(correctRoot) % _R(), uint256(h.orderRoot) % _R(),
            uint256(0), uint256(1)
        ];
        require(aggVerifier.verifyProof(a, b, c, pub), "bad fraud proof");
        h.reverted = true;
        if (id == batches.length - 1) latestRoot = h.prevRoot;
        staking.slashOrderingViolation(h.sequencer);
        emit BatchReverted(id, msg.sender);
    }

    /// @notice Close the epoch: settle every bonded node's verification outcomes
    ///         (dynamic slashing, Eq. 1) and elect the next committee (Eq. 2).
    ///         Callable by a sequencer, by anyone while there is no committee,
    ///         or by anyone once EPOCH_TIMEOUT has passed (liveness).
    function advanceEpoch() external {
        require(
            election.isSequencer(msg.sender) || election.committeeSizeNow() == 0
                || block.timestamp >= lastEpochAt + EPOCH_TIMEOUT,
            "not sequencer"
        );
        uint256 n = staking.nodeCount();
        for (uint256 i = 0; i < n; i++) _settle(staking.nodeList(i));
        // Seed from the RANDAO beacon rather than from the caller, so the
        // caller cannot grind the committee; residual bias is that of RANDAO.
        election.advanceEpoch(keccak256(abi.encode(block.prevrandao, election.epoch(), address(this))));
        lastEpochAt = block.timestamp;
    }

    /// @notice Committee-reported uptime (gamma term of S_i only; never slashes).
    function reportUptime(address node, uint64 uptimeWad) external {
        require(election.isSequencer(msg.sender), "not sequencer");
        election.setUptime(node, uptimeWad);
    }

    function batchCount() external view returns (uint256) { return batches.length; }
    function outcomeSourceCount() external view returns (uint256) { return outcomeSources.length; }

    /// @notice epsilon_i that the next epoch settlement would apply to `node`.
    function pendingEpsilon(address node) external view returns (uint256 dv, uint256 di, uint256 epsWad) {
        (dv, di) = _pending(node);
        epsWad = dv + di == 0 ? 0 : (di * WAD) / (dv + di);
    }

    function _pending(address node) internal view returns (uint256 dv, uint256 di) {
        uint256 v; uint256 inv;
        for (uint256 j = 0; j < outcomeSources.length; j++) {
            (uint256 a, uint256 b) = outcomeSources[j].outcomes(node);
            v += a; inv += b;
        }
        Settled storage s = settled[node];
        dv = v - s.valid;
        di = inv - s.invalid;
    }

    function _settle(address node) internal {
        (uint256 dv, uint256 di) = _pending(node);
        if (dv + di == 0) return;
        Settled storage s = settled[node];
        s.valid += uint128(dv);
        s.invalid += uint128(di);
        uint256 eps = (di * WAD) / (dv + di);
        if (eps > 0) staking.slash(node, eps);
        // volume V_i: one unit per verified proof
        election.recordOutcomes(node, uint64(dv), uint64(dv + di), uint128(dv));
        emit EpsilonReported(node, dv, di, eps);
    }

    function _R() internal pure returns (uint256) {
        return 21888242871839275222246405745257275088548364400416034343698204186575808495617;
    }
}
