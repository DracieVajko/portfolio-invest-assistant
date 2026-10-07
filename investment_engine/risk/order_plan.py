"""Phase 7 advisory order plans. INFORMATIONAL ONLY — never executed.

Every plan carries human_confirmation_required=true and the advisory guard.
No venue, order-type, or execution fields exist by design. This module must
never gain network/client imports (guarded by tests).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

ADVISORY_GUARD = "> Advisory only — not executed. Confirm manually outside this program."

_GROUP_SLEEVE = {
    "TECH_PIE": "thematic",
    "SHORT_TERM_TRADING": "tactical",
    "LONG_RUN_DCA": "long_term",
    "CRYPTO": "crypto",
}


def sleeve_for_group(group: str | None) -> str:
    return _GROUP_SLEEVE.get(str(group or "").upper(), "unknown")


def build_plans(*, committee_decisions: list[dict[str, Any]],
                portfolio_rows: list[dict[str, Any]],
                recon_status: str, free_cash: float, total_equity: float,
                earnings_status: dict[str, Any], report_day: str,
                news_events: list[dict[str, Any]], regime: str,
                risk_posture: str, policy: dict[str, Any],
                sleeves: dict[str, str] | None = None,
                sector_headroom_eur: float = float("inf")) -> dict[str, Any]:
    """Build advisory plans for committee BUY/TRIM decisions that pass gates."""
    from investment_engine.risk import eligibility as _elig
    from investment_engine.risk import sizing as _sizing
    from investment_engine.risk.policy import REQUIRES_USER_VALUE, is_deferred

    sleeves = sleeves or {}
    risk_pct = float(policy.get("risk_per_trade_pct", 2.0) or 2.0)
    adverse = float(policy.get("adverse_move_assumption", 0.25) or 0.25)
    eb = policy.get("earnings_blackout", {}) or {}
    cap_default = float((policy.get("concentration", {}) or {}).get("default_max_position_pct", 20.0) or 20.0)
    stops_scope = str((policy.get("stops", {}) or {}).get("scope", "tactical-only"))
    core_trims = (policy.get("core_trims", REQUIRES_USER_VALUE))

    rows = {str(r.get("display_symbol") or r.get("ticker") or "").upper(): r
            for r in (portfolio_rows or []) if isinstance(r, dict)}
    plans, blocked = [], []
    for decision in committee_decisions or []:
        if not isinstance(decision, dict):
            continue
        ticker = str(decision.get("ticker", "")).upper()
        action = str(decision.get("action", "HOLD")).upper()
        if action not in ("BUY", "ACCUMULATE", "TRIM", "SELL"):
            continue
        row = rows.get(ticker)
        if row is None:
            blocked.append({"ticker": ticker, "reason": "not an owned holding"})
            continue
        sleeve = (sleeves or {}).get(ticker, "unknown")
        direction = "BUY" if action in ("BUY", "ACCUMULATE") else "TRIM"
        # Deferred: core trim permission unknown → HOLD+REVIEW text, no plan.
        if direction == "TRIM" and sleeve in ("long_term", "core") and is_deferred(core_trims):
            blocked.append({"ticker": ticker,
                            "reason": "core trim permission pending user confirmation — HOLD+REVIEW"})
            continue
        # Deferred: whole-pie operations never planned until confirmed.
        # Deferred: unknown sleeve → research only (no sizing guesses).
        if sleeve == "unknown":
            blocked.append({"ticker": ticker, "reason": "sleeve unknown — research only, no sizing"})
            continue
        try:
            weight = float(row.get("weight", 0) or 0)
        except (TypeError, ValueError):
            weight = 0.0
        needs_levels = sleeve == "tactical" and "tactical" in stops_scope
        gates = _elig.evaluate(
            direction, ticker=ticker, recon_status=recon_status, free_cash=free_cash,
            weight=weight, cap_pct=cap_default, earnings_status=earnings_status,
            report_day=report_day, before_bdays=int(eb.get("before_bdays", 5) or 5),
            after_bdays=int(eb.get("after_bdays", 2) or 2),
            mapping_status=str(row.get("external_mapping_status", "")),
            needs_levels=needs_levels, news_events=news_events, regime=regime,
            risk_posture=risk_posture, sleeve=sleeve)
        failed = [g for g in gates if not g["pass"]]
        # Mapping gate downgrades levels to RESEARCH instead of blocking outright.
        hard_failed = [g for g in failed if g["rule"] != "mapping-verified"]
        if hard_failed:
            blocked.append({"ticker": ticker,
                            "reason": "; ".join(f"{g['rule']}: {g['reason']}" for g in hard_failed)})
            continue
        sizing = _sizing.indicative_notional_eur(
            weight_pct=weight, cap_pct=cap_default, total_equity=total_equity,
            risk_pct=risk_pct, adverse_move=adverse,
            sector_headroom_eur=sector_headroom_eur, deployable_cash_eur=free_cash)
        tech = {}
        stop_hint = limit_hint = None
        if needs_levels and all(g["pass"] for g in gates if g["rule"] == "mapping-verified"):
            stop_hint, limit_hint = "from canonical Support", "from canonical Resistance"
        rationale = [e for e in (decision.get("basis", []) or [])]
        for event in news_events or []:
            if isinstance(event, dict) and str(event.get("ticker", "")).upper() == ticker:
                rationale.extend(str(i) for i in event.get("item_ids", [])[:2])
        plans.append({
            "ticker": ticker,
            "direction": direction,
            "indicative_notional_eur": sizing["notional_eur"],
            "binding_constraint": sizing["binding_constraint"],
            "sizing_inputs": sizing["inputs"],
            "stop_hint": stop_hint,
            "limit_hint": limit_hint,
            "expires": "next scheduled run",
            "rationale_ids": rationale[:6],
            "policy_checks": gates,
            "confidence": 0.55,
            "sleeve": sleeve,
            "human_confirmation_required": True,
        })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "advisory_guard": ADVISORY_GUARD,
        "plans": plans,
        "blocked": blocked,
        "policy_version": str(policy.get("policy_version", "")),
    }


def render(bundle: dict[str, Any]) -> str:
    """Markdown section for the AI context file (never the human brief)."""
    lines = ["## Advisory Order Plans (not executed)", ADVISORY_GUARD, ""]
    for plan in bundle.get("plans", []) or []:
        lines.append(
            f"- **{plan.get('ticker')}** {plan.get('direction')} "
            f"~€{plan.get('indicative_notional_eur', 0):,.2f} "
            f"(binding: {plan.get('binding_constraint')}, sleeve: {plan.get('sleeve')})")
    for blocked in bundle.get("blocked", []) or []:
        lines.append(f"- {blocked.get('ticker')}: no plan — {blocked.get('reason')}")
    if not bundle.get("plans") and not bundle.get("blocked"):
        lines.append("No advisory plans this run.")
    return "\n".join(lines)
