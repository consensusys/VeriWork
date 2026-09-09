// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./interfaces/IGroth16Verifier.sol";
import "./interfaces/IERC20Minimal.sol";

/// @title FlexFactory ZK Twin Registry (paper Listing 1, extended with
///        maintenance escrow and ERC-8004-screened service agents).
contract FlexFactoryTwinRegistry {
    IGroth16Verifier public immutable verifier;
    IERC20Minimal public immutable vwcToken;
    address public governance;
    uint8 public constant MAINTENANCE_THRESHOLD = 30;

    struct MachineState {
        bytes32 stateRoot;      // ZK-attested state hash
        uint256 cycleCount;
        uint8   healthScore;    // 0-100
        uint256 lastUpdated;
        bool    maintenanceFlag;
    }
    struct MaintenanceOrder {
        address provider;
        uint256 escrow;
        uint64  scheduledAt;
        bool    completed;
    }

    mapping(bytes32 => MachineState) public twins;
    mapping(bytes32 => address) public operators;            // machineId => operator
    mapping(bytes32 => address) public workers;              // machineId => authorised edge worker
    mapping(address => bool) public whitelistedAgents;       // AI agents allowed to act
    mapping(address => uint256) public agentIds;             // ERC-8004 agentId per agent
    mapping(bytes32 => MaintenanceOrder) public orders;      // machineId => open order
    uint256 public humanReviewThreshold = 500e18;            // orders above need multisig

    event TwinUpdated(bytes32 indexed machineId, bytes32 stateRoot, uint8 healthScore);
    event MaintenanceTriggered(bytes32 indexed machineId, address serviceProvider);
    event MaintenanceScheduled(bytes32 indexed machineId, address indexed provider, uint256 escrow, address agent);
    event MaintenanceCompleted(bytes32 indexed machineId, address indexed provider, uint256 paid);
    event AgentWhitelisted(address indexed agent, uint256 erc8004AgentId);

    modifier onlyGov() { require(msg.sender == governance, "not gov"); _; }
    modifier onlyAgent() { require(whitelistedAgents[msg.sender], "agent not whitelisted"); _; }

    constructor(IGroth16Verifier _verifier, IERC20Minimal _vwc, address _gov) {
        verifier = _verifier; vwcToken = _vwc; governance = _gov;
    }

    function registerMachine(bytes32 machineId, address operator, address worker) external onlyGov {
        operators[machineId] = operator; workers[machineId] = worker;
    }

    /// @notice Agents are whitelisted with their ERC-8004 identity so operators
    ///         can screen them against portable on-chain reputation.
    function whitelistAgent(address agent, uint256 erc8004AgentId) external onlyGov {
        whitelistedAgents[agent] = true; agentIds[agent] = erc8004AgentId;
        emit AgentWhitelisted(agent, erc8004AgentId);
    }

    function updateTwinState(
        bytes32 machineId,
        bytes32 newStateRoot,
        uint8 healthScore,
        uint256 cycleCount,
        uint[2] calldata a,
        uint[2][2] calldata b,
        uint[2] calldata c
    ) external {
        require(msg.sender == workers[machineId], "not authorised worker");
        require(healthScore <= 100, "health range");
        uint[] memory signals = new uint[](3);
        signals[0] = uint256(twins[machineId].stateRoot) % R;
        signals[1] = uint256(newStateRoot) % R;
        signals[2] = uint256(healthScore);
        require(verifier.verifyProof(a, b, c, signals), "Invalid ZK telemetry proof");

        twins[machineId] = MachineState({
            stateRoot: newStateRoot,
            cycleCount: cycleCount,
            healthScore: healthScore,
            lastUpdated: block.timestamp,
            maintenanceFlag: healthScore < MAINTENANCE_THRESHOLD
        });
        emit TwinUpdated(machineId, newStateRoot, healthScore);
        if (healthScore < MAINTENANCE_THRESHOLD) {
            emit MaintenanceTriggered(machineId, operators[machineId]);
        }
    }

    /// @notice Called by a whitelisted AI agent (paper Listing 2). Escrows VWC
    ///         for the service provider; large orders require human review.
    function scheduleMaintenance(bytes32 machineId, address provider, uint256 escrow) external onlyAgent {
        require(twins[machineId].maintenanceFlag, "no maintenance flag");
        require(orders[machineId].escrow == 0, "order open");
        require(escrow <= humanReviewThreshold, "requires human review");
        require(vwcToken.transferFrom(msg.sender, address(this), escrow), "escrow");
        orders[machineId] = MaintenanceOrder(provider, escrow, uint64(block.timestamp), false);
        emit MaintenanceScheduled(machineId, provider, escrow, msg.sender);
    }

    /// @notice Completion is proven by a fresh twin update with health >= threshold.
    function completeMaintenance(bytes32 machineId) external {
        MaintenanceOrder storage o = orders[machineId];
        require(o.escrow > 0 && !o.completed, "no order");
        require(twins[machineId].lastUpdated > o.scheduledAt, "no post-service attestation");
        require(!twins[machineId].maintenanceFlag, "still flagged");
        o.completed = true;
        uint256 paid = o.escrow; o.escrow = 0;
        require(vwcToken.transfer(o.provider, paid), "pay");
        emit MaintenanceCompleted(machineId, o.provider, paid);
    }

    uint256 internal constant R = 21888242871839275222246405745257275088548364400416034343698204186575808495617;
}
