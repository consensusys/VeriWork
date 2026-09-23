// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./PoAWStaking.sol";

/// @title PoUW scoring and hybrid PoAW committee election (paper Eq. 2, Eq. 3).
/// @notice Off-chain nodes mirror this election (node/veriwork/consensus); the
///         on-chain result is canonical.
///
///   S_i  = alpha * T_valid/T_sub + beta * V_i/V_max + gamma * U_i/U_max      (WAD)
///          (the validity ratio is 0 for a node with no submissions)
///   P(i) = sigma_i * S_i / sum_j sigma_j * S_j
///
///   Validity counts and volume come from on-chain proof verification
///   outcomes (VeriWorkRollup settles them each epoch); uptime U_i is
///   reported by the committee and only affects the gamma term.
contract SequencerElection {
    uint256 public constant WAD = 1e18;

    PoAWStaking public immutable staking;
    address public rollup;
    address public governance;

    uint256 public alpha = 5e17;   // 0.5
    uint256 public beta  = 3e17;   // 0.3
    uint256 public gamma = 2e17;   // 0.2
    uint256 public kEligible = 20;
    uint256 public committeeSize = 7;
    uint256 public reputationCapWad = 125e16;   // 1.25x

    struct Stats { uint64 valid; uint64 submitted; uint128 volume; uint64 uptimeWad; }
    mapping(address => Stats) public stats;
    mapping(address => uint256) public reputationWad;   // >= 1e18, bounded on use

    uint256 public epoch;
    address[] public committee;

    event EpochAdvanced(uint256 indexed epoch, bytes32 seed, address[] committee);

    modifier onlyRollup() { require(msg.sender == rollup, "not rollup"); _; }
    modifier onlyGov()    { require(msg.sender == governance, "not gov"); _; }

    constructor(PoAWStaking _staking, address _gov) { staking = _staking; governance = _gov; }
    function setRollup(address r) external onlyGov { rollup = r; }

    function setWeights(uint256 a, uint256 b, uint256 g) external onlyGov {
        require(a + b + g == WAD, "sum != 1");
        alpha = a; beta = b; gamma = g;
    }
    function setCommittee(uint256 k, uint256 size) external onlyGov {
        require(size <= k && size > 0, "size"); kEligible = k; committeeSize = size;
    }

    /// @notice Rollup records a node's verification outcomes for the epoch,
    ///         read from on-chain outcome sources (not reported by any party).
    function recordOutcomes(address node, uint64 valid, uint64 submitted, uint128 volume) external onlyRollup {
        require(valid <= submitted, "valid > submitted");
        Stats storage s = stats[node];
        s.valid += valid;
        s.submitted += submitted;
        s.volume += volume;
    }

    /// @notice Committee-reported uptime in [0, 1e18]; affects only the gamma term.
    function setUptime(address node, uint64 uptimeWad) external onlyRollup {
        require(uptimeWad <= WAD, "uptime > 1");
        stats[node].uptimeWad = uptimeWad;
    }

    /// @dev PoUW score S_i in WAD given epoch maxima.
    function score(address node, uint256 vMax, uint256 uMax) public view returns (uint256) {
        Stats storage s = stats[node];
        uint256 ratio = s.submitted == 0 ? 0 : (uint256(s.valid) * WAD) / s.submitted;
        uint256 vTerm = vMax == 0 ? 0 : (uint256(s.volume) * WAD) / vMax;
        uint256 uTerm = uMax == 0 ? 0 : (uint256(s.uptimeWad) * WAD) / uMax;
        return (alpha * ratio + beta * vTerm + gamma * uTerm) / WAD;
    }

    /// @notice Weight sigma_i * S_i (* bounded reputation) used by Eq. 2.
    function weight(address node, uint256 vMax, uint256 uMax) public view returns (uint256) {
        uint256 rep = reputationWad[node];
        if (rep < WAD) rep = WAD;
        if (rep > reputationCapWad) rep = reputationCapWad;
        return (staking.stakeOf(node) * score(node, vMax, uMax) / WAD) * rep / WAD;
    }

    /// @notice Hybrid election: top-k by S_i, then sortition without
    ///         replacement weighted by sigma_i * S_i (Eq. 2), seeded by `seed`
    ///         (the rollup derives it from the RANDAO beacon).
    ///         Gas is O(n * k); intended for committees of tens of nodes.
    function advanceEpoch(bytes32 seed) external onlyRollup {
        uint256 n = staking.nodeCount();
        address[] memory cand = new address[](n);
        uint256[] memory sc = new uint256[](n);
        uint256 vMax; uint256 uMax; uint256 m;
        for (uint256 i = 0; i < n; i++) {
            address a = staking.nodeList(i);
            Stats storage s = stats[a];
            if (s.volume > vMax) vMax = s.volume;
            if (s.uptimeWad > uMax) uMax = s.uptimeWad;
        }
        for (uint256 i = 0; i < n; i++) {
            address a = staking.nodeList(i);
            if (!staking.isEligible(a)) continue;
            uint256 si = score(a, vMax, uMax);
            if (si == 0) continue;
            cand[m] = a; sc[m] = si; m++;
        }
        // partial selection sort: keep top-kEligible by score
        uint256 k = m < kEligible ? m : kEligible;
        for (uint256 i = 0; i < k; i++) {
            uint256 best = i;
            for (uint256 j = i + 1; j < m; j++) if (sc[j] > sc[best]) best = j;
            (cand[i], cand[best]) = (cand[best], cand[i]);
            (sc[i], sc[best]) = (sc[best], sc[i]);
        }
        // sigma_i * S_i-weighted sortition without replacement (Eq. 2)
        uint256 size = committeeSize < k ? committeeSize : k;
        address[] memory chosen = new address[](size);
        uint256[] memory w = new uint256[](k);
        for (uint256 i = 0; i < k; i++) w[i] = weight(cand[i], vMax, uMax);
        for (uint256 s_ = 0; s_ < size; s_++) {
            uint256 total;
            for (uint256 i = 0; i < k; i++) total += w[i];
            if (total == 0) { size = s_; break; }
            uint256 r = uint256(keccak256(abi.encode(seed, epoch, s_))) % total;
            uint256 acc;
            for (uint256 i = 0; i < k; i++) {
                acc += w[i];
                if (r < acc) { chosen[s_] = cand[i]; w[i] = 0; break; }
            }
        }
        delete committee;
        for (uint256 i = 0; i < size; i++) committee.push(chosen[i]);
        // reset epoch counters (volume is cumulative; validity/submitted are per-epoch)
        for (uint256 i = 0; i < n; i++) {
            Stats storage s = stats[staking.nodeList(i)];
            s.valid = 0; s.submitted = 0;
        }
        epoch += 1;
        emit EpochAdvanced(epoch, seed, committee);
    }

    function committeeSizeNow() external view returns (uint256) { return committee.length; }
    function isSequencer(address a) external view returns (bool) {
        for (uint256 i = 0; i < committee.length; i++) if (committee[i] == a) return true;
        return false;
    }
}
