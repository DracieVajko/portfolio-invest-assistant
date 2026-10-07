"""Phase 7 investment-policy loading and validation.

Template: investment_policy.example.json (committed). Real file:
investment_policy.json (gitignored, user-local). Missing file →
POLICY NOT SET (order-plan section renders the notice and nothing else).
Deferred REQUIRES_USER_VALUE entries block the dependent plan type.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REQUIRES_USER_VALUE = "REQUIRES_USER_VALUE"
TEMPLATE_NAME = "investment_policy.example.json"
LOCAL_NAME = "investment_policy.json"


def load_policy(path: str | Path | None = None) -> dict[str, Any]:
    """Load and validate. Returns {status, policy, version, problems}."""
    target = Path(path) if path else Path(LOCAL_NAME)
    if not target.is_file():
        return {"status": "NOT SET", "policy": {}, "version": "",
                "problems": [f"{target} not found — order plans disabled"]}
    try:
        policy = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"status": "INVALID", "policy": {}, "version": "",
                "problems": [f"unreadable policy file: {type(exc).__name__}"]}
    if not isinstance(policy, dict):
        return {"status": "INVALID", "policy": {}, "version": "",
                "problems": ["policy root must be an object"]}
    problems = []
    for key in ("risk_per_trade_pct", "cash", "earnings_blackout", "discovery"):
        if key not in policy:
            problems.append(f"missing required section: {key}")
    return {"status": "OK" if not problems else "INVALID",
            "policy": policy,
            "version": str(policy.get("policy_version", "")),
            "problems": problems}


def is_deferred(value: Any) -> bool:
    return value == REQUIRES_USER_VALUE or (
        isinstance(value, dict) and value.get("REQUIRES_USER_VALUE") is not None)
