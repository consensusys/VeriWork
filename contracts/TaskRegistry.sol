// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./interfaces/IGroth16Verifier.sol";
import "./interfaces/IERC20Minimal.sol";

/// @title L2 task market with per-task ZK verification (Algorithm 1).
contract TaskRegistry {
    IGroth16Verifier public immutable verifier;
    IERC20Minimal public immutable vwc;

    enum Status { Open, Verified, Rejected, Expired }
    struct Task {
        address submitter;
        bytes32 inputHash;      // H_in
        uint256 lower;          // declared bounds [l, u]
        uint256 upper;
        uint64  deadline;
        uint256 reward;         // VWC escrowed
        Status  status;
        address worker;
        bytes32 outputHash;     // H_twin
        string  cid;            // IPFS attestation
    }
    mapping(bytes32 => Task) public tasks;
    uint256 public taskCount;

    event TaskCreated(bytes32 indexed id, address indexed submitter, uint256 reward, uint64 deadline);
    event TaskVerified(bytes32 indexed id, address indexed worker, bytes32 outputHash, string cid);
    event TaskRejected(bytes32 indexed id, address indexed worker);

    constructor(IGroth16Verifier _v, IERC20Minimal _vwc) { verifier = _v; vwc = _vwc; }

    function createTask(bytes32 inputHash, uint256 lower, uint256 upper, uint64 deadline, uint256 reward)
        external returns (bytes32 id)
    {
        require(deadline > block.timestamp && lower <= upper, "params");
        require(vwc.transferFrom(msg.sender, address(this), reward), "escrow");
        id = keccak256(abi.encode(msg.sender, inputHash, taskCount++));
        tasks[id] = Task(msg.sender, inputHash, lower, upper, deadline, reward, Status.Open, address(0), 0, "");
        emit TaskCreated(id, msg.sender, reward, deadline);
    }

    /// @notice Worker submits (CID, H_twin, proof). Public inputs bind
    ///         [H_in, H_twin, lower, upper] so the proof cannot be replayed.
    function submitResult(bytes32 id, bytes32 outputHash, string calldata cid,
                          uint[2] calldata a, uint[2][2] calldata b, uint[2] calldata c) external {
        Task storage t = tasks[id];
        require(t.status == Status.Open, "not open");
        require(block.timestamp <= t.deadline, "late");
        uint[] memory pub = new uint[](4);
        pub[0] = uint256(t.inputHash) % R; pub[1] = uint256(outputHash) % R;
        pub[2] = t.lower; pub[3] = t.upper;
        if (verifier.verifyProof(a, b, c, pub)) {
            t.status = Status.Verified; t.worker = msg.sender; t.outputHash = outputHash; t.cid = cid;
            require(vwc.transfer(msg.sender, t.reward), "reward");
            emit TaskVerified(id, msg.sender, outputHash, cid);
        } else {
            // task stays Open (re-queued); the rollup reports epsilon for slashing
            emit TaskRejected(id, msg.sender);
        }
    }

    function reclaimExpired(bytes32 id) external {
        Task storage t = tasks[id];
        require(t.status == Status.Open && block.timestamp > t.deadline, "not expired");
        t.status = Status.Expired;
        require(vwc.transfer(t.submitter, t.reward), "refund");
    }

    uint256 internal constant R = 21888242871839275222246405745257275088548364400416034343698204186575808495617;
}
