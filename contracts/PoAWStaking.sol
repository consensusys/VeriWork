// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./interfaces/IERC20Minimal.sol";

/// @title Multi-layer staking with dynamic slashing (paper Sec. III-B, Eq. 1).
/// @notice Three layers: own bond, delegation, and per-service restaking capped
///         to bound correlated loss.  Slashing is objective: epsilon_i is
///         computed by the rollup from on-chain ZK verification counters.
///         Delegations are share-based, so a slash reduces every delegator's
///         position pro rata; restaked positions are re-capped after a slash.
///
///         sigma_i <- sigma_i * (1 - lambda * epsilon_i)
contract PoAWStaking {
    uint256 public constant WAD = 1e18;

    IERC20Minimal public immutable vwc;
    address public rollup;               // only the rollup may report epsilon
    address public governance;

    uint256 public lambda;               // WAD-scaled penalty coefficient
    uint256 public maxEpochSlash;        // WAD-scaled cap per epoch
    uint256 public restakeCapBps;        // per-service cap in basis points of own bond
    uint256 public minBond;              // minimum own bond for eligibility

    struct Node {
        uint256 own;
        uint256 delegated;
        bool registered;
    }
    mapping(address => Node) public nodes;
    mapping(address => mapping(address => uint256)) public delegatorShares;  // node => delegator => shares
    mapping(address => uint256) public totalDelegatorShares;                // node => shares
    mapping(address => mapping(bytes32 => uint256)) public restaked;       // node => service => amt
    mapping(address => bytes32[]) internal restakedServices;               // node => services used
    mapping(address => mapping(bytes32 => bool)) internal knownService;
    address[] public nodeList;

    event Bonded(address indexed node, uint256 amount);
    event Delegated(address indexed node, address indexed delegator, uint256 amount);
    event Restaked(address indexed node, bytes32 indexed service, uint256 amount);
    event Slashed(address indexed node, uint256 epsilonWad, uint256 amount);
    event Withdrawn(address indexed node, address indexed who, uint256 amount);

    modifier onlyRollup() { require(msg.sender == rollup, "not rollup"); _; }
    modifier onlyGov()    { require(msg.sender == governance, "not gov"); _; }

    constructor(IERC20Minimal _vwc, address _governance) {
        vwc = _vwc;
        governance = _governance;
        lambda = WAD / 2;              // 0.5
        maxEpochSlash = WAD / 2;       // 50 %
        restakeCapBps = 2500;          // 25 %
        minBond = 1000e18;
    }

    function setRollup(address r) external onlyGov { rollup = r; }
    function setParams(uint256 _lambda, uint256 _maxEpochSlash, uint256 _capBps, uint256 _minBond)
        external onlyGov
    {
        require(_lambda <= WAD && _maxEpochSlash <= WAD && _capBps <= 10_000, "range");
        lambda = _lambda; maxEpochSlash = _maxEpochSlash; restakeCapBps = _capBps; minBond = _minBond;
    }

    // ---------------- economic bond layer ----------------
    function bond(uint256 amount) external {
        require(amount > 0, "zero");
        require(vwc.transferFrom(msg.sender, address(this), amount), "xfer");
        Node storage n = nodes[msg.sender];
        if (!n.registered) { n.registered = true; nodeList.push(msg.sender); }
        n.own += amount;
        emit Bonded(msg.sender, amount);
    }

    // ---------------- delegation layer ----------------
    function delegate(address node, uint256 amount) external {
        Node storage n = nodes[node];
        require(n.registered, "unknown node");
        require(amount > 0, "zero");
        uint256 ts = totalDelegatorShares[node];
        require(ts == 0 || n.delegated > 0, "delegation pool wiped");
        require(vwc.transferFrom(msg.sender, address(this), amount), "xfer");
        uint256 shares = ts == 0 ? amount : (amount * ts) / n.delegated;
        delegatorShares[node][msg.sender] += shares;
        totalDelegatorShares[node] = ts + shares;
        n.delegated += amount;
        emit Delegated(node, msg.sender, amount);
    }

    /// @return current value of `delegator`'s position with `node` (after slashing)
    function delegationOf(address node, address delegator) external view returns (uint256) {
        uint256 ts = totalDelegatorShares[node];
        return ts == 0 ? 0 : (delegatorShares[node][delegator] * nodes[node].delegated) / ts;
    }

    // ---------------- restaking layer ----------------
    function restake(bytes32 service, uint256 amount) external {
        Node storage n = nodes[msg.sender];
        uint256 cap = (n.own * restakeCapBps) / 10_000;
        require(restaked[msg.sender][service] + amount <= cap, "restake cap");
        if (!knownService[msg.sender][service]) {
            knownService[msg.sender][service] = true;
            restakedServices[msg.sender].push(service);
        }
        restaked[msg.sender][service] += amount;
        emit Restaked(msg.sender, service, amount);
    }

    // ---------------- views ----------------
    /// @return sigma_i = own + delegated (input to selection, Eq. 2)
    function stakeOf(address node) public view returns (uint256) {
        Node storage n = nodes[node];
        return n.own + n.delegated;
    }

    function isEligible(address node) external view returns (bool) {
        return nodes[node].own >= minBond;
    }

    function nodeCount() external view returns (uint256) { return nodeList.length; }

    // ---------------- dynamic slashing (Eq. 1) ----------------
    /// @param epsilonWad invalid-submission rate in [0, 1e18], computed by the
    ///        rollup from Groth16 verification outcomes over the epoch.
    function slash(address node, uint256 epsilonWad) external onlyRollup returns (uint256) {
        return _slash(node, epsilonWad);
    }

    /// @notice Deviation from the ZK-attested batch order is treated as epsilon = 1.
    function slashOrderingViolation(address node) external onlyRollup returns (uint256) {
        return _slash(node, WAD);
    }

    function _slash(address node, uint256 epsilonWad) internal returns (uint256 slashed) {
        require(epsilonWad <= WAD, "eps");
        uint256 frac = (lambda * epsilonWad) / WAD;
        if (frac > maxEpochSlash) frac = maxEpochSlash;
        Node storage n = nodes[node];
        uint256 total = n.own + n.delegated;
        if (frac == 0 || total == 0) return 0;
        slashed = (total * frac) / WAD;
        // pro-rata across own bond and the delegation pool
        n.own -= (n.own * frac) / WAD;
        n.delegated -= (n.delegated * frac) / WAD;
        // restaked positions may not exceed the per-service cap of the smaller bond
        uint256 cap = (n.own * restakeCapBps) / 10_000;
        bytes32[] storage svcs = restakedServices[node];
        for (uint256 i = 0; i < svcs.length; i++) {
            if (restaked[node][svcs[i]] > cap) restaked[node][svcs[i]] = cap;
        }
        // slashed VWC is burned to the zero-address sink (fixed supply, so we
        // park it in this contract's dead balance rather than minting)
        emit Slashed(node, epsilonWad, slashed);
    }
}
