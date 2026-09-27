"""PortfolioRiskManager v1: deterministic risk verdict from broker truth.

No LLM required (provider accepted for interface parity, unused). Evidence:
portfolio rows + reconciliation; regime/cash enrich the verdict.
"""

from __future__ import annotations

from typing import Any

from investment_engine.skills import contracts

SKILL = "portfolio_risk_manager"
VERSION = "v1"

# Current-config default (portfolio_config.json rule_5: max 20% one name).
# Overridable by the Phase 7 user-local policy file when it exists.
CONCENTRATION_CAP_PCT = 20.0


def run(inputs: dict[str, Any], provider=None, settings=None, language: str = "English") -> dict[str, Any]:
    rows = inputs.get("portfolio_rows", []) or []
    recon = inputs.get("reconciliation", {}) or {}
    status = str(recon.get("status", "UNKNOWN")).upper()
    cash = inputs.get("cash", {}) or {}
    try:
        free_cash = float(cash.get("free", 0) or 0)
    except (TypeError, ValueError):
        free_cash = 0.0

    breaches: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            weight = float(row.get("weight", 0) or 0)
        except (TypeError, ValueError):
            continue
        if weight > CONCENTRATION_CAP_PCT:
            breaches.append({
                "ticker": str(row.get("display_symbol") or row.get("ticker") or "?"),
                "weight": round(weight, 2),
                "cap": CONCENTRATION_CAP_PCT,
            })

    if status == "FAIL":
        posture, cash_action = "DEFENSIVE", "withhold deployment until reconciliation passes"
    elif free_cash <= 0:
        posture, cash_action = "CAUTIOUS", "no BUY/ADD proposable: free cash is zero"
    elif breaches:
        posture, cash_action = "CAUTIOUS", "reserve-first: address concentration before adds"
    else:
        posture, cash_action = "NORMAL", "reserve-first deployment per regime"

    basis = [f"recon={status}", f"breaches={len(breaches)}",
             f"free_cash={free_cash:.2f}"]
    return {
        "skill": SKILL,
        "version": VERSION,
        "status": "OK",
        "reason": "",
        "confidence": contracts.confidence(0.9 if status in ("PASS", "FAIL") else 0.5, basis),
        "unverified": [],
        "data": {"posture": posture, "breaches": breaches, "cash_action": cash_action},
    }


def render(output: dict[str, Any]) -> str:
    """Markdown section for the AI context file."""
    data = output.get("data", {}) or {}
    lines = [f"Risk posture: **{data.get('posture', '?')}** — {data.get('cash_action', '')}"]
    for breach in data.get("breaches", []) or []:
        lines.append(f"- Concentration: **{breach.get('ticker')}** {breach.get('weight')}% > cap {breach.get('cap')}%")
    return "\n".join(lines)
