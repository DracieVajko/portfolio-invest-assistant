"""EquityResearchAnalyst v1: deterministic thesis templates (+ optional polish).

Mapping gate enforced in code: technical observations render only for
VERIFIED rows. No price targets, no averaging-down endorsement, no sizes.
"""

from __future__ import annotations

from typing import Any

from investment_engine.skills import contracts

SKILL = "equity_research_analyst"
VERSION = "v1"


def _tech_line(display: str, technicals: dict[str, Any] | None) -> str:
    tech = (technicals or {}).get(display, {}) if isinstance(technicals, dict) else {}
    if not isinstance(tech, dict):
        return ""
    rsi = tech.get("RSI_14")
    parts = []
    try:
        if rsi is not None and float(rsi) >= 70:
            parts.append(f"RSI {float(rsi):.0f} overbought")
        elif rsi is not None and float(rsi) <= 30:
            parts.append(f"RSI {float(rsi):.0f} oversold")
    except (TypeError, ValueError):
        pass
    for key in ("Support", "Resistance"):
        if tech.get(key) is not None:
            parts.append(f"{key} {tech[key]}")
    return "; ".join(parts)


def run(inputs: dict[str, Any], provider=None, settings=None, language: str = "English") -> dict[str, Any]:
    rows = [r for r in (inputs.get("portfolio_rows", []) or []) if isinstance(r, dict)]
    if not rows:
        return contracts.insufficient_output(SKILL, VERSION, "no portfolio rows")
    technicals = inputs.get("technicals", {}) or {}
    earnings = inputs.get("earnings_status", {}) or {}
    items = [i for i in (inputs.get("corpus_items", []) or []) if isinstance(i, dict)]
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        for tag in item.get("ticker_tags", []) or []:
            by_ticker.setdefault(str(tag).upper(), []).append(item)

    notes = []
    unverified: list[str] = []
    for row in rows:
        disp = str(row.get("display_symbol") or row.get("ticker") or "?").upper()
        signal = str(row.get("signal", "HOLD")).upper()
        mapping = str(row.get("external_mapping_status", ""))
        verified = mapping == "VERIFIED"
        thesis = {"BUY": "ACCUMULATE", "SELL": "TRIM"}.get(signal, "HOLD")
        catalysts, risks, evidence_ids = [], [], []
        for item in by_ticker.get(disp, [])[:3]:
            evidence_ids.append(str(item.get("id")))
            catalysts.append(f"{str(item.get('title', ''))[:100]} ({item.get('publisher', '')})")
        tech_line = _tech_line(disp, technicals) if verified else ""
        if not verified:
            unverified.append(f"{disp}: technicals withheld ({mapping or 'unknown mapping'})")
        earn = earnings.get(disp)
        if isinstance(earn, (list, tuple)) and earn:
            risks.append(f"earnings {earn[0]} ({earn[1] if len(earn) > 1 else 'dated'})")
        try:
            pnl = float(row.get("pnl_pct", 0) or 0)
            if pnl < 0:
                risks.append(f"position P&L {pnl:+.1f}% — review, no averaging-down endorsement")
        except (TypeError, ValueError):
            pass
        note = {"ticker": disp, "thesis": thesis, "catalysts": catalysts[:3],
                "risks": risks[:3], "evidence_ids": evidence_ids}
        if tech_line:
            note["technicals"] = tech_line
        notes.append(note)

    known = {str(i.get("id")) for i in items}
    good, why = contracts.validate_citations({"data": {"notes": notes}}, known)
    return {
        "skill": SKILL,
        "version": VERSION,
        "status": "OK" if good else "DEGRADED",
        "reason": "" if good else why,
        "confidence": contracts.confidence(0.6, [f"notes={len(notes)}", "template"]),
        "unverified": unverified,
        "data": {"notes": notes},
    }


def render(output: dict[str, Any]) -> str:
    """Markdown section for the AI context file."""
    lines = []
    for note in (output.get("data", {}) or {}).get("notes", []) or []:
        lines.append(f"- **{note.get('ticker')}** — {note.get('thesis')}")
    return "\n".join(lines) if lines else "No equity notes."
