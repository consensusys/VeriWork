// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./interfaces/IERC20Minimal.sol";

/// @title Bancor-style bonding-curve credit market with adaptive pricing
///        (paper Sec. V-C, Eq. 4–5).
/// @notice Each application credit (e.g. a factory's FXC) has a VWC reserve
///         B_a, outstanding supply O_m and reserve weight W_a.
///           spot P = B_a / (O_m * W_a)
///         Swaps route credit_a -> VWC -> credit_b atomically.  A time-weighted
///         demand/supply signal scales quotes: P_c = P_0 (1 + alpha_p D_r/S_r)
///         with D_r the *excess* demand, so a balanced market trades at P_0 and
///         the surcharge under congestion accrues to the reserve pool.
///         Fractional powers are evaluated on-chain with a fixed-point log/exp;
///         for gas reasons the reference contract supports W_a in {1/2, 1/4, 1}
///         exactly and delegates other weights to the L2 execution client.
contract CreditMarket {
    uint256 public constant WAD = 1e18;

    IERC20Minimal public immutable vwc;
    address public governance;
    uint256 public alphaP = 35e16;           // 0.35
    uint256 public surgeCap = 3e18;          // 3x
    uint256 public deflationFloor = 8e17;    // 0.8x

    struct Credit {
        uint256 reserve;      // B_a in VWC
        uint256 supply;       // O_m
        uint256 weightBps;    // W_a * 10_000  (2500, 5000 or 10000)
        bool exists;
    }
    mapping(bytes32 => Credit) public credits;
    mapping(bytes32 => mapping(address => uint256)) public balanceOf;   // credit => holder

    // adaptive pricing signal (TWAP of demand/supply), WAD-scaled
    uint256 public demandSupplyTwap = WAD;

    event CreditRegistered(bytes32 indexed id, uint256 reserve, uint256 supply, uint256 weightBps);
    event Swapped(bytes32 indexed from, bytes32 indexed to, address indexed who, uint256 amountIn, uint256 amountOut);
    event Bought(bytes32 indexed id, address indexed who, uint256 vwcIn, uint256 minted);
    event Sold(bytes32 indexed id, address indexed who, uint256 burned, uint256 vwcOut);

    modifier onlyGov() { require(msg.sender == governance, "not gov"); _; }

    constructor(IERC20Minimal _vwc, address _gov) { vwc = _vwc; governance = _gov; }

    function registerCredit(bytes32 id, uint256 reserve, uint256 supply, uint256 weightBps) external {
        require(!credits[id].exists, "exists");
        require(weightBps == 2500 || weightBps == 5000 || weightBps == 10_000, "unsupported weight");
        require(vwc.transferFrom(msg.sender, address(this), reserve), "xfer");
        credits[id] = Credit(reserve, supply, weightBps, true);
        balanceOf[id][msg.sender] = supply;
        emit CreditRegistered(id, reserve, supply, weightBps);
    }

    /// @notice Oracle-free demand signal update, called by the rollup each pricing window.
    function updateDemandSignal(uint256 demand, uint256 supply) external onlyGov {
        uint256 ratio = supply == 0 ? 0 : (demand * WAD) / supply;
        demandSupplyTwap = (demandSupplyTwap * 3 + ratio) / 4;   // EMA ~ window TWAP
    }

    /// @return multiplier P_c / P_0 (WAD)
    /// @dev r = demand/supply TWAP.  r >= 1: 1 + alphaP*(r-1) capped at surgeCap;
    ///      r <  1: 1 - alphaP*(1-r) floored at deflationFloor.  r = 1 => exactly 1.
    function adaptiveMultiplier() public view returns (uint256) {
        uint256 r = demandSupplyTwap;
        uint256 m;
        if (r >= WAD) {
            m = WAD + (alphaP * (r - WAD)) / WAD;
            if (m > surgeCap) m = surgeCap;
        } else {
            uint256 dec = (alphaP * (WAD - r)) / WAD;
            m = dec >= WAD ? deflationFloor : WAD - dec;
            if (m < deflationFloor) m = deflationFloor;
        }
        return m;
    }

    /// @notice Spot price P_0 in VWC per credit (WAD) — before adaptive multiplier.
    function spotPrice(bytes32 id) public view returns (uint256) {
        Credit storage c = credits[id];
        return (c.reserve * WAD * 10_000) / (c.supply * c.weightBps);
    }

    /// @notice Effective price P_c = P_0 * multiplier.
    function effectivePrice(bytes32 id) external view returns (uint256) {
        return (spotPrice(id) * adaptiveMultiplier()) / WAD;
    }

    // ---------------- curve maths ----------------
    /// @dev minted = O_m * ((1 + T/B)^W - 1)
    function _buyOut(Credit storage c, uint256 vwcIn) internal view returns (uint256) {
        uint256 base = WAD + (vwcIn * WAD) / c.reserve;          // 1 + T/B
        uint256 pw = _powW(base, c.weightBps);
        return (c.supply * (pw - WAD)) / WAD;
    }

    /// @dev paid = B * (1 - (1 - S/O)^(1/W))
    function _sellOut(Credit storage c, uint256 creditIn) internal view returns (uint256) {
        uint256 base = WAD - (creditIn * WAD) / c.supply;         // 1 - S/O
        uint256 pw = _powInvW(base, c.weightBps);
        return (c.reserve * (WAD - pw)) / WAD;
    }

    function _powW(uint256 x, uint256 wBps) internal pure returns (uint256) {
        if (wBps == 10_000) return x;
        if (wBps == 5000)  return _sqrt(x * WAD);
        return _sqrt(_sqrt(x * WAD) * WAD);           // 2500 -> x^(1/4)
    }

    function _powInvW(uint256 x, uint256 wBps) internal pure returns (uint256) {
        if (wBps == 10_000) return x;
        if (wBps == 5000)  return (x * x) / WAD;      // x^2
        uint256 x2 = (x * x) / WAD;                   // x^4
        return (x2 * x2) / WAD;
    }

    function _sqrt(uint256 y) internal pure returns (uint256 z) {
        if (y > 3) { z = y; uint256 k = y / 2 + 1; while (k < z) { z = k; k = (y / k + k) / 2; } }
        else if (y != 0) z = 1;
    }

    // ---------------- user actions ----------------
    function buy(bytes32 id, uint256 vwcIn, uint256 minOut) external returns (uint256 minted) {
        Credit storage c = credits[id]; require(c.exists, "no credit");
        minted = (_buyOut(c, vwcIn) * WAD) / adaptiveMultiplier();
        require(minted >= minOut, "slippage");
        require(vwc.transferFrom(msg.sender, address(this), vwcIn), "xfer");
        c.reserve += vwcIn; c.supply += minted; balanceOf[id][msg.sender] += minted;
        emit Bought(id, msg.sender, vwcIn, minted);
    }

    function sell(bytes32 id, uint256 creditIn, uint256 minOut) external returns (uint256 paid) {
        Credit storage c = credits[id]; require(c.exists, "no credit");
        require(balanceOf[id][msg.sender] >= creditIn, "balance");
        paid = _sellOut(c, creditIn);
        require(paid >= minOut, "slippage");
        balanceOf[id][msg.sender] -= creditIn; c.supply -= creditIn; c.reserve -= paid;
        require(vwc.transfer(msg.sender, paid), "xfer");
        emit Sold(id, msg.sender, creditIn, paid);
    }

    /// @notice Atomic credit_a -> credit_b swap via VWC (no external transfer).
    function swap(bytes32 from, bytes32 to, uint256 amountIn, uint256 minOut) external returns (uint256 out) {
        Credit storage a = credits[from]; Credit storage b = credits[to];
        require(a.exists && b.exists, "no credit");
        require(balanceOf[from][msg.sender] >= amountIn, "balance");
        uint256 vwcMid = _sellOut(a, amountIn);
        out = (_buyOut(b, vwcMid) * WAD) / adaptiveMultiplier();
        require(out >= minOut, "slippage");
        balanceOf[from][msg.sender] -= amountIn; a.supply -= amountIn; a.reserve -= vwcMid;
        b.reserve += vwcMid; b.supply += out; balanceOf[to][msg.sender] += out;
        emit Swapped(from, to, msg.sender, amountIn, out);
    }
}
