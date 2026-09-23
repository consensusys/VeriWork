"""Model-agnostic planner interface (paper Code Snippet 2: the planner).

`RuleBasedPlanner` is a deterministic fallback with no API dependency so the
simulator and CI run offline.  `LLMPlanner` calls any OpenAI-compatible or
Anthropic endpoint and asks for the same JSON schema:

    {"maintenance": [...], "procurement": [...], "rebalance": [...]}
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

SYSTEM_PROMPT = (
    "You are a factory floor AI agent. Analyse machine twin states and output JSON: "
    "{maintenance:[{machineId, serviceProvider, escrowVWC}], "
    "procurement:[{supplierId, partSKU, quantity, maxPrice}], "
    "rebalance:[{fromLine, toLine, jobIds}]}. Output JSON only."
)


class Planner:
    def plan(self, twin_states: List[Dict[str, Any]], inventory: Dict[str, int],
             events: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:  # pragma: no cover
        raise NotImplementedError


class RuleBasedPlanner(Planner):
    def __init__(self, threshold: int = 40, provider: str = "svc-provider-0x02",
                 escrow_vwc: float = 120.0, reorder_point: int = 20):
        self.threshold, self.provider, self.escrow, self.reorder_point = threshold, provider, escrow_vwc, reorder_point

    def plan(self, twin_states, inventory, events):
        maint = [{"machineId": t["machine_id"], "serviceProvider": self.provider, "escrowVWC": self.escrow}
                 for t in twin_states if t["health_score"] < self.threshold and not t.get("order_open")]
        proc = [{"supplierId": "SUP-B", "partSKU": sku, "quantity": 100, "maxPrice": 2.5}
                for sku, qty in inventory.items() if qty < self.reorder_point]
        flagged_lines = {t["machine_id"].split("-")[0] for t in twin_states if t["health_score"] < self.threshold}
        rebal = [{"fromLine": ln, "toLine": "assembly", "jobIds": []} for ln in sorted(flagged_lines) if ln != "assembly"]
        return {"maintenance": maint, "procurement": proc, "rebalance": rebal}


class LLMPlanner(Planner):
    """Thin client over an OpenAI-compatible chat endpoint (works with local llama.cpp,
    vLLM, or hosted models). Falls back to RuleBasedPlanner on any error."""

    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None):
        self.model = model or os.getenv("AGENT_MODEL", "local-llama")
        self.base_url = (base_url or os.getenv("AGENT_BASE_URL", "http://localhost:8080/v1")).rstrip("/")
        self.api_key = api_key or os.getenv("AGENT_API_KEY", "none")
        self.fallback = RuleBasedPlanner()

    def plan(self, twin_states, inventory, events):
        try:
            import httpx
            r = httpx.post(f"{self.base_url}/chat/completions",
                           headers={"Authorization": f"Bearer {self.api_key}"},
                           json={"model": self.model, "temperature": 0,
                                 "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                              {"role": "user", "content": json.dumps(
                                                  {"twinStates": twin_states, "inventory": inventory, "events": events})}]},
                           timeout=30)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            plan = json.loads(content.replace("```json", "").replace("```", "").strip())
            for k in ("maintenance", "procurement", "rebalance"):
                plan.setdefault(k, [])
            return plan
        except Exception:
            return self.fallback.plan(twin_states, inventory, events)
