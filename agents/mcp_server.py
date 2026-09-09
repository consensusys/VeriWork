"""MCP server exposing FlexFactory twin tools to any MCP-capable model.

Run:  python -m agents.mcp_server           (stdio transport)
Tools: get_twin_states, get_twin, get_recent_events, schedule_maintenance,
       quote_credit_swap, get_inventory
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "node"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from veriwork.sdk import LocalL2

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # keep importable without the optional dependency
    FastMCP = None

_l2 = LocalL2()
_l2.register_credit("FXC-A", 10_000.0, 20_000.0, 0.5)
_l2.register_credit("SUP-B", 30_000.0, 10_000.0, 0.25)
_agent = None


def _get_agent():
    global _agent
    if _agent is None:
        from agents.factory_agent import FactoryAgent
        _agent = FactoryAgent(_l2, model=None, agent_wallet="agent-mcp")
    return _agent


def _tools():
    return {
        "get_twin_states": lambda: [t.__dict__ | {"state_root": t.state_root.hex()} for t in _l2.all_twin_states()],
        "get_twin": lambda machine_id: (lambda t: t.__dict__ | {"state_root": t.state_root.hex()} if t else None)(_l2.twin_state(machine_id)),
        "get_recent_events": lambda since_block=0: _l2.recent_events(int(since_block)),
        "schedule_maintenance": lambda machine_id, provider, escrow_vwc: _get_agent()._schedule_maintenance(machine_id, provider, float(escrow_vwc)),
        "quote_credit_swap": lambda from_credit, to_credit, amount: _l2.quote(from_credit, to_credit, float(amount)),
        "get_inventory": lambda: _get_agent().inventory,
    }


def build_server():
    if FastMCP is None:
        raise SystemExit("pip install mcp  (or: pip install 'veriwork[agents]')")
    mcp = FastMCP("flexfactory-twin")
    for name, fn in _tools().items():
        mcp.tool(name=name)(fn)
    return mcp


if __name__ == "__main__":
    build_server().run()
