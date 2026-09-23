// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./interfaces/ITwinVerifier.sol";
import "./interfaces/IERC20Minimal.sol";
import "./interfaces/IOutcomeSource.sol";

/// @title L2 task market with per-task ZK verification (Algorithm 1).
/// @notice Tasks reuse the C_twin circuit.  The task id occupies the
///         circuit's machineId slot and the worker's address its submitter
///         slot, so a proof is bound to one task and one worker: it cannot be
///         replayed on another task or front-run by someone who copies it
///         from the mempool.  prevStateRoot is 0 for stand-alone tasks.
contract TaskRegistry is IOutcomeSource {
    uint256 internal constant R =
        21888242871839275222246405745257275088548364400416034343698204186575808495617;

    ITwinVerifier public immutable verifier;
    IERC20Minimal public immutable vwc;

    enum Status { Open, Verified, Rejected, Expired }
    struct Task {
        address submitter;
        bytes32 inputHash;      // H_in (Poseidon commitment to the raw readings)
        uint256 boundsHash;     // Poseidon(lo[], hi[]) -- the declared bounds
        uint64  deadline;
        uint256 reward;         // VWC escrowed
        Status  status;
        address worker;
        bytes32 outputHash;     // H_twin
        string  cid;            // IPFS attestation
    }
    mapping(bytes32 => Task) public tasks;
    uint256 public taskCount;
    mapping(address => uint256) public validCount;
    mapping(address => uint256) public invalidCount;

    event TaskCreated(bytes32 indexed id, address indexed submitter, uint256 reward, uint64 deadline);
    event TaskVerified(bytes32 indexed id, address indexed worker, bytes32 outputHash, string cid);
    event TaskRejected(bytes32 indexed id, address indexed worker);

    constructor(ITwinVerifier _v, IERC20Minimal _vwc) { verifier = _v; vwc = _vwc; }

    function createTask(bytes32 inputHash, uint256 boundsHash, uint64 deadline, uint256 reward)
        external returns (bytes32 id)
    {
        require(deadline > block.timestamp, "params");
        require(uint256(inputHash) < R && boundsHash < R, "not a field element");
        require(vwc.transferFrom(msg.sender, address(this), reward), "escrow");
        id = keccak256(abi.encode(msg.sender, inputHash, taskCount++));
        tasks[id] = Task(msg.sender, inputHash, boundsHash, deadline, reward, Status.Open, address(0), 0, "");
        emit TaskCreated(id, msg.sender, reward, deadline);
    }

    /// @notice Value the prover must use for the circuit's machineId input.
    function circuitTaskId(bytes32 id) public pure returns (uint256) { return uint256(id) % R; }

    /// @notice Worker submits (CID, H_twin, proof).  A failed verification is
    ///         recorded against the worker (epsilon_i) and the task stays open.
    function submitResult(
        bytes32 id,
        bytes32 outputHash,
        uint8 healthScore,
        uint256 cycleCount,
        string calldata cid,
        uint[2] calldata a, uint[2][2] calldata b, uint[2] calldata c
    ) external {
        Task storage t = tasks[id];
        require(t.status == Status.Open, "not open");
        require(block.timestamp <= t.deadline, "late");
        uint[8] memory pub = [
            uint256(outputHash), uint256(t.inputHash), t.boundsHash,
            0, uint256(healthScore), cycleCount,
            circuitTaskId(id), uint256(uint160(msg.sender))
        ];
        if (verifier.verifyProof(a, b, c, pub)) {
            validCount[msg.sender] += 1;
            t.status = Status.Verified; t.worker = msg.sender; t.outputHash = outputHash; t.cid = cid;
            require(vwc.transfer(msg.sender, t.reward), "reward");
            emit TaskVerified(id, msg.sender, outputHash, cid);
        } else {
            invalidCount[msg.sender] += 1;      // read by the rollup for epsilon_i
            emit TaskRejected(id, msg.sender);
        }
    }

    function reclaimExpired(bytes32 id) external {
        Task storage t = tasks[id];
        require(t.status == Status.Open && block.timestamp > t.deadline, "not expired");
        t.status = Status.Expired;
        require(vwc.transfer(t.submitter, t.reward), "refund");
    }

    /// @inheritdoc IOutcomeSource
    function outcomes(address node) external view override returns (uint256 valid, uint256 invalid) {
        return (validCount[node], invalidCount[node]);
    }
}
