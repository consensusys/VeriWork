"""ERC-8004 'Trustless Agents' identity helpers.

Builds the agent registration file (with MCP / A2A endpoints and x402 support
flag) and, when web3 is available, registers it in the Identity Registry so
FlexFactory operators can screen the agent by on-chain reputation.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

REGISTRATION_TYPE = "https://eips.ethereum.org/EIPS/eip-8004#registration-v1"


def registration_file(name: str, description: str, mcp_endpoint: str, a2a_card_url: str,
                      chain_id: int, identity_registry: str, agent_id: Optional[int] = None) -> Dict[str, Any]:
    reg = {
        "type": REGISTRATION_TYPE,
        "name": name,
        "description": description,
        "image": "",
        "services": [
            {"name": "A2A", "endpoint": a2a_card_url, "version": "0.3.0"},
            {"name": "MCP", "endpoint": mcp_endpoint, "version": "2025-06-18"},
        ],
        "x402Support": True,
        "active": True,
        "registrations": [],
        "supportedTrust": ["reputation", "crypto-economic"],
    }
    if agent_id is not None:
        reg["registrations"].append({"agentId": agent_id, "agentRegistry": f"eip155:{chain_id}:{identity_registry}"})
    return reg


@dataclass
class AgentIdentity:
    wallet: str
    agent_id: Optional[int]
    registry: str
    chain_id: int
    registration: Dict[str, Any]

    @classmethod
    def local(cls, wallet: str) -> "AgentIdentity":
        reg = registration_file("FlexFactory Orchestrator", "Predictive maintenance & procurement agent",
                                "http://localhost:7402/mcp", "http://localhost:7402/.well-known/agent-card.json",
                                31337, "0x0000000000000000000000000000000000008004")
        aid = int(hashlib.sha256(wallet.encode()).hexdigest()[:8], 16)
        reg["registrations"].append({"agentId": aid, "agentRegistry": "eip155:31337:0x...8004"})
        return cls(wallet, aid, "0x...8004", 31337, reg)

    @classmethod
    def register_onchain(cls, w3, account, identity_registry_addr: str, agent_uri: str,
                         registration: Dict[str, Any]) -> "AgentIdentity":
        abi = [{"name": "register", "type": "function", "stateMutability": "nonpayable",
                "inputs": [{"name": "agentURI", "type": "string"}], "outputs": [{"name": "agentId", "type": "uint256"}]}]
        c = w3.eth.contract(address=identity_registry_addr, abi=abi)
        tx = c.functions.register(agent_uri).build_transaction({"from": account.address, "nonce": w3.eth.get_transaction_count(account.address)})
        signed = account.sign_transaction(tx)
        rcpt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed.rawTransaction))
        # agentId = ERC-721 tokenId from the Transfer/Registered log
        agent_id = int(rcpt["logs"][0]["topics"][3].hex(), 16) if rcpt["logs"] else None
        return cls(account.address, agent_id, identity_registry_addr, w3.eth.chain_id, registration)

    def to_json(self) -> str:
        return json.dumps(self.registration, indent=2)
