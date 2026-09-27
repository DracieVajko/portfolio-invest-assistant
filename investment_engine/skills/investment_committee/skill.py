"""InvestmentCommittee v1: deterministic reconciliation of skill outputs.

Pure rules engine (provider accepted for interface parity; the verdict logic
is deterministic so committee decisions are reproducible offline).
"""

from __future__ import annotations

from typing import Any

from investment_engine.skills import contracts

SKILL = "investment_committee"
VERSION = "v1"

ACTIONS = frozenset({"BUY", "ACCUMULATE", "HOLD", "TRIM", "SELL", "WATCH"})


def _canon_signal(canonical_map: dict[str, Any] | None, display: str) -> str:
    entry = (canonical_map or {}).get(display, {}) if isinstance(canonical_map, dict) else {}
    sig = entry.get("signal") if isinstance(entry, dict) else entry
    sig = str(sig or "HOLD").upper()
    return sig if sig in ACTIONS else "HOLD"


def run(inputs: dict[str, Any], provider=None, settings=None, language: str = "English") -> dict[str, Any]:
    rows = [r for r in (inputs.get("portfolio_rows", []) or []) if isinstance(r, dict)]
    if not rows:
        return contracts.insufficient_output(SKILL, VERSION, "no portfolio rows")
    canonical_map = inputs.get("canonical_map", {}) or {}
    recon = inputs.get("reconciliation", {}) or {}
    failed = str(recon.get("status", "UNKNOWN")).upper() == "FAIL"
    risk_out = inputs.get("risk_output", {}) or {}
    news_out = inputs.get("news_output", {}) or {}
    equity_out = inputs.get("equity_output", {}) or {}

    owned = {str(r.get("display_symbol") or r.get("ticker") or "").upper() for r in rows}
    news_tickers: set[str] = set()
    for event in (news_out.get("data", {}) or {}).get("events", []) or []:
        if isinstance(event, dict) and event.get("ticker"):
            news_tickers.add(str(event["ticker"]).upper())
    equity_tickers: set[str] = set()
    for note in (equity_out.get("data", {}) or {}).get("notes", []) or []:
        if isinstance(note, dict) and note.get("ticker"):
            equity_tickers.add(str(note["ticker"]).upper())

    decisions, dissent = [], []
    for phantom in sorted((news_tickers | equity_tickers) - owned - {"GENERAL"}):
        dissent.append(f"{phantom}: cited by skills but not an owned holding — dropped")
    for row in rows:
        disp = str(row.get("display_symbol") or row.get("ticker") or "?").upper()
        action = _canon_signal(canonical_map, disp)
        basis = ["canonical-signal"]
        if failed and action in ("BUY", "ACCUMULATE"):
            action, basis = "WATCH", ["canonical-signal", "recon-FAIL-watchlist-only"]
        if disp in news_tickers:
            basis.append("news-event")
        risk_posture = (risk_out.get("data", {}) or {}).get("posture", "")
        if risk_posture:
            basis.append(f"risk-{risk_posture}")
        decisions.append({"ticker": disp, "action": action, "basis": basis})

    return {
        "skill": SKILL,
        "version": VERSION,
        "status": "OK",
        "reason": "",
        "confidence": contracts.confidence(
            0.75, [f"decisions={len(decisions)}", f"dissent={len(dissent)}",
                   f"recon={recon.get('status', 'UNKNOWN')}"]),
        "unverified": list(risk_out.get("unverified", []) or []) + list(news_out.get("unverified", []) or []),
        "data": {"decisions": decisions, "dissent_notes": dissent},
    }


def render(output: dict[str, Any]) -> str:
    """Markdown section for the AI context file."""
    lines = []
    for decision in (output.get("data", {}) or {}).get("decisions", []) or []:
        lines.append(f"- **{decision.get('ticker')}** — {decision.get('action')} "
                     f"({', '.join(decision.get('basis', []))})")
    for note in (output.get("data", {}) or {}).get("dissent_notes", []) or []:
        lines.append(f"- dissent: {note}")
    return "\n".join(lines) if lines else "No committee decisions."
