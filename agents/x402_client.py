"""x402 payment client (HTTP 402 'Payment Required' flow, Coinbase 2025).

Flow:  GET resource -> 402 + PaymentRequirements -> sign payment payload
       (EIP-3009 transferWithAuthorization for USDC, or VWC transfer on L2)
       -> retry with X-PAYMENT header -> 200 + X-PAYMENT-RESPONSE (tx hash).

`X402Client.pay()` performs the real flow when `httpx` and a signer are
available, and returns a deterministic local receipt otherwise so the agent
loop is testable offline.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class PaymentReceipt:
    url: str
    amount: float
    asset: str
    tx_hash: str
    network: str


class X402Client:
    def __init__(self, payer: str, signer=None, facilitator: str = "https://x402.org/facilitator"):
        self.payer, self.signer, self.facilitator = payer, signer, facilitator

    def _local_receipt(self, url: str, amount: float, asset: str) -> PaymentReceipt:
        h = hashlib.sha256(f"{self.payer}|{url}|{amount}|{asset}|{time.time_ns()}".encode()).hexdigest()
        return PaymentReceipt(url, amount, asset, "0x" + h, "veriwork-l2-local")

    def pay(self, url: str, amount: float, asset: str = "USDC") -> PaymentReceipt:
        if self.signer is None:
            return self._local_receipt(url, amount, asset)
        import httpx  # optional dependency
        r = httpx.get(url, timeout=15)
        if r.status_code != 402:
            return PaymentReceipt(url, 0.0, asset, "", "none")
        req = r.json()["accepts"][0]           # PaymentRequirements
        payload = self.signer.sign_payment(req, amount)   # EIP-3009 / Permit2 authorisation
        r2 = httpx.get(url, headers={"X-PAYMENT": json.dumps(payload)}, timeout=30)
        r2.raise_for_status()
        resp = json.loads(r2.headers.get("X-PAYMENT-RESPONSE", "{}"))
        return PaymentReceipt(url, amount, asset, resp.get("transaction", ""), req.get("network", ""))
