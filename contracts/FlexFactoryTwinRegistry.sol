// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./interfaces/ITwinVerifier.sol";
import "./interfaces/IERC20Minimal.sol";
import "./interfaces/IOutcomeSource.sol";

// FlexFactory ZK twin registry. The part above
// the "extensions" divider is Code Snippet 1 of
// the paper: keep its lines <= 50 characters.
contract FlexFactoryTwinRegistry is
    IOutcomeSource
{
    ITwinVerifier public immutable verifier;
    IERC20Minimal public immutable vwcToken;
    address public immutable governance;
    uint8 public constant CRITICAL_HEALTH = 40;

    struct MachineState {
        bytes32 stateRoot;  // ZK-attested hash
        uint256 cycleCount;
        uint8 healthScore;  // 0-100
        uint256 lastUpdated;
        bool maintenanceFlag;
    }

    mapping(bytes32 => MachineState) public twins;
    mapping(bytes32 => address) public operators;
    mapping(bytes32 => uint256) public boundsHash;
    mapping(address => uint256) public goodProofs;
    mapping(address => uint256) public badProofs;

    event TwinUpdated(
        bytes32 indexed machineId,
        bytes32 stateRoot,
        uint8 healthScore
    );
    event MaintenanceTriggered(
        bytes32 indexed machineId,
        address operator
    );
    event InvalidProof(
        bytes32 indexed machineId,
        address indexed submitter
    );

    modifier onlyGov() {
        require(msg.sender == governance,
            "Not gov");
        _;
    }

    constructor(
        ITwinVerifier v,
        IERC20Minimal vwc,
        address gov
    ) {
        verifier = v;
        vwcToken = vwc;
        governance = gov;
    }

    // Registers a machine, its operator (the edge
    // gateway) and Poseidon(lo[], hi[]) bounds.
    function registerMachine(
        bytes32 machineId,
        address op,
        uint256 bh
    ) external onlyGov {
        require(
            operators[machineId] == address(0),
            "Registered"
        );
        // R = BN254 scalar field order
        require(
            uint256(machineId) < R && bh < R,
            "Not a field element"
        );
        operators[machineId] = op;
        boundsHash[machineId] = bh;
    }

    function updateTwinState(
        bytes32 machineId,
        bytes32 newStateRoot,
        bytes32 inputHash,  // H_in commitment
        uint8 healthScore,
        uint256 cycleCount,
        uint[2] calldata a,
        uint[2][2] calldata b,
        uint[2] calldata c
    ) external {
        require(
            operators[machineId] == msg.sender,
            "Not operator"
        );
        MachineState storage t = twins[machineId];
        require(cycleCount >= t.cycleCount,
            "Stale update");

        // Rebuild C_twin's public signals: bind
        // state, bounds, machine and sender
        uint[8] memory pub = [
            uint256(newStateRoot),  // H_twin
            uint256(inputHash),     // H_in
            boundsHash[machineId],
            uint256(t.stateRoot),
            uint256(healthScore),
            cycleCount,
            uint256(machineId),
            uint256(uint160(msg.sender))
        ];
        if (!verifier.verifyProof(a, b, c, pub)) {
            // raises eps_i in Eq. (1)
            badProofs[msg.sender] += 1;
            emit InvalidProof(machineId,
                msg.sender);
            return;
        }
        goodProofs[msg.sender] += 1;

        bool crit = healthScore < CRITICAL_HEALTH;
        t.stateRoot = newStateRoot;
        t.cycleCount = cycleCount;
        t.healthScore = healthScore;
        t.lastUpdated = block.timestamp;
        t.maintenanceFlag = crit;

        emit TwinUpdated(machineId, newStateRoot,
            healthScore);
        if (crit) {
            emit MaintenanceTriggered(machineId,
                msg.sender);
        }
    }

    // ===================== extensions (not in the paper listing) =====================

    uint256 internal constant R =
        21888242871839275222246405745257275088548364400416034343698204186575808495617;

    /// @inheritdoc IOutcomeSource
    function outcomes(address node) external view override returns (uint256 valid, uint256 invalid) {
        return (goodProofs[node], badProofs[node]);
    }

    // ---- maintenance escrow with on-chain agent safety rails (paper Sec. VIII-D) ----
    //  * capability whitelist: only whitelisted agents may escrow, and only to
    //    governance-approved service providers
    //  * daily spending cap per agent
    //  * orders above humanReviewThreshold wait for governance (human) review

    enum OrderStatus { None, PendingReview, Scheduled, Completed }
    struct MaintenanceOrder {
        address agent;
        address provider;
        uint256 escrow;
        uint64  scheduledAt;
        OrderStatus status;
    }

    mapping(bytes32 => MaintenanceOrder) public orders;      // machineId => latest order
    mapping(address => bool) public whitelistedAgents;
    mapping(address => uint256) public agentIds;             // ERC-8004 agentId per agent
    mapping(address => bool) public approvedProviders;
    uint256 public humanReviewThreshold = 500e18;
    uint256 public dailyAgentCap = 50_000e18;
    mapping(address => uint256) public spendDay;             // agent => day index of spentToday
    mapping(address => uint256) public spentToday;

    event AgentWhitelisted(address indexed agent, uint256 erc8004AgentId);
    event AgentSuspended(address indexed agent);
    event ProviderApproval(address indexed provider, bool approved);
    event AgentLimits(uint256 humanReviewThreshold, uint256 dailyAgentCap);
    event MaintenanceReviewRequested(bytes32 indexed machineId, address indexed provider, uint256 escrow, address agent);
    event MaintenanceScheduled(bytes32 indexed machineId, address indexed provider, uint256 escrow, address agent);
    event MaintenanceRejected(bytes32 indexed machineId, address indexed provider, uint256 refunded);
    event MaintenanceCompleted(bytes32 indexed machineId, address indexed provider, uint256 paid);

    modifier onlyAgent() { require(whitelistedAgents[msg.sender], "agent not whitelisted"); _; }

    /// @notice Agents are whitelisted with their ERC-8004 identity so operators
    ///         can screen them against portable on-chain reputation.
    function whitelistAgent(address agent, uint256 erc8004AgentId) external onlyGov {
        whitelistedAgents[agent] = true;
        agentIds[agent] = erc8004AgentId;
        emit AgentWhitelisted(agent, erc8004AgentId);
    }

    /// @notice DAO veto: suspend an agent immediately (it can no longer escrow).
    function suspendAgent(address agent) external onlyGov {
        whitelistedAgents[agent] = false;
        emit AgentSuspended(agent);
    }

    function setProvider(address provider, bool approved) external onlyGov {
        approvedProviders[provider] = approved;
        emit ProviderApproval(provider, approved);
    }

    function setAgentLimits(uint256 reviewThreshold, uint256 dailyCap) external onlyGov {
        humanReviewThreshold = reviewThreshold;
        dailyAgentCap = dailyCap;
        emit AgentLimits(reviewThreshold, dailyCap);
    }

    /// @notice Called by a whitelisted AI agent (paper Code Snippet 2) for a
    ///         machine whose ZK-attested health is below CRITICAL_HEALTH.
    function scheduleMaintenance(bytes32 machineId, address provider, uint256 escrow) external onlyAgent {
        require(twins[machineId].maintenanceFlag, "no maintenance flag");
        OrderStatus st = orders[machineId].status;
        require(st == OrderStatus.None || st == OrderStatus.Completed, "order open");
        require(approvedProviders[provider], "provider not approved");
        require(escrow > 0, "zero escrow");

        uint256 day = block.timestamp / 1 days;
        if (spendDay[msg.sender] != day) { spendDay[msg.sender] = day; spentToday[msg.sender] = 0; }
        require(spentToday[msg.sender] + escrow <= dailyAgentCap, "daily cap");
        spentToday[msg.sender] += escrow;

        require(vwcToken.transferFrom(msg.sender, address(this), escrow), "escrow");
        bool review = escrow > humanReviewThreshold;
        orders[machineId] = MaintenanceOrder(msg.sender, provider, escrow, uint64(block.timestamp),
                                             review ? OrderStatus.PendingReview : OrderStatus.Scheduled);
        if (review) emit MaintenanceReviewRequested(machineId, provider, escrow, msg.sender);
        else emit MaintenanceScheduled(machineId, provider, escrow, msg.sender);
    }

    /// @notice Human review for orders above the threshold (governance multisig).
    function reviewOrder(bytes32 machineId, bool approve) external onlyGov {
        MaintenanceOrder storage o = orders[machineId];
        require(o.status == OrderStatus.PendingReview, "not pending");
        if (approve) {
            o.status = OrderStatus.Scheduled;
            o.scheduledAt = uint64(block.timestamp);
            emit MaintenanceScheduled(machineId, o.provider, o.escrow, o.agent);
        } else {
            uint256 refund = o.escrow;
            o.escrow = 0;
            o.status = OrderStatus.None;
            require(vwcToken.transfer(o.agent, refund), "refund");
            emit MaintenanceRejected(machineId, o.provider, refund);
        }
    }

    /// @notice Completion is proven by a fresh ZK-attested twin update, made
    ///         after scheduling, whose health is back at or above the threshold.
    function completeMaintenance(bytes32 machineId) external {
        MaintenanceOrder storage o = orders[machineId];
        require(o.status == OrderStatus.Scheduled, "no scheduled order");
        require(twins[machineId].lastUpdated > o.scheduledAt, "no post-service attestation");
        require(!twins[machineId].maintenanceFlag, "still flagged");
        o.status = OrderStatus.Completed;
        uint256 paid = o.escrow;
        o.escrow = 0;
        require(vwcToken.transfer(o.provider, paid), "pay");
        emit MaintenanceCompleted(machineId, o.provider, paid);
    }
}
