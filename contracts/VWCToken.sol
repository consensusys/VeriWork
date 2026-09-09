// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title VeriWork Credit (VWC) — the main credit of VeriWork.
/// @notice Minimal ERC-20 with a fixed-supply invariant; cross-chain moves use
///         lock-and-mint on the bridge so global supply never changes.
contract VWCToken {
    string public constant name = "VeriWork Credit";
    string public constant symbol = "VWC";
    uint8 public constant decimals = 18;
    uint256 public immutable totalSupply;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    constructor(uint256 supply, address treasury) {
        totalSupply = supply;
        balanceOf[treasury] = supply;
        emit Transfer(address(0), treasury, supply);
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        _move(msg.sender, to, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 a = allowance[from][msg.sender];
        require(a >= amount, "VWC: allowance");
        if (a != type(uint256).max) allowance[from][msg.sender] = a - amount;
        _move(from, to, amount);
        return true;
    }

    function _move(address from, address to, uint256 amount) internal {
        require(to != address(0), "VWC: zero addr");
        uint256 b = balanceOf[from];
        require(b >= amount, "VWC: balance");
        unchecked { balanceOf[from] = b - amount; }
        balanceOf[to] += amount;
        emit Transfer(from, to, amount);
    }
}
