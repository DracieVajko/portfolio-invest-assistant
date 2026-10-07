"""Phase 7 trade-eligibility gates. Pure functions, deterministic.

Every gate returns {rule, pass, reason}. A BUY plan needs ALL gates;
a TRIM plan skips cash/evidence gates but keeps mapping/caps.
"""

from __future__ import annotations

from datetime import date
from typing import Any

try:
    import numpy as _np

    def _busday_count(start: str, end: str) -> int | None:
        try:
            return int(_np.busday_count(start, end))
        except Exception:
            return None
except ImportError:  # pragma: no cover - numpy is core, this is belt-and-braces
    def _busday_count(start: str, end: str) -> int | None:
        return None


def _check(statement: bool, rule: str, reason: str) -> dict[str, Any]:
    return {"rule": rule, "pass": bool(statement), "reason": reason}


def gate_recon(recon_status: str) -> dict[str, Any]:
    ok = str(recon_status or "").upper() == "PASS"
    return _check(ok, "recon-PASS",
                  "reconciliation PASS" if ok else f"reconciliation {recon_status} blocks deployment")


def gate_cash(direction: str, free_cash: float) -> dict[str, Any]:
    if direction in ("BUY", "ADD"):
        ok = free_cash > 0
        return _check(ok, "cash-available",
                      f"free cash €{free_cash:,.2f}" if ok else "zero free cash blocks BUY/ADD")
    return _check(True, "cash-available", "n/a for TRIM/SELL")


def gate_concentration(weight: float, cap_pct: float) -> dict[str, Any]:
    ok = weight <= cap_pct
    return _check(ok, "concentration-cap",
                  f"weight {weight:.1f}% within cap {cap_pct:.1f}%" if ok
                  else f"weight {weight:.1f}% exceeds cap {cap_pct:.1f}%")


def gate_blackout(display: str, earnings_status: dict[str, Any],
                  report_day: str, before_bdays: int = 5, after_bdays: int = 2) -> dict[str, Any]:
    """No new/increase inside [T-before, T+after] business days of earnings."""
    val = (earnings_status or {}).get(str(display).upper())
    ev_day = val[0][:10] if isinstance(val, (list, tuple)) and val and isinstance(val[0], str) else None
    if not ev_day:
        return _check(True, "earnings-blackout", "no known earnings date")
    try:
        before = _busday_count(report_day, ev_day)
        after = _busday_count(ev_day, report_day)
    except Exception:
        before = after = None
    if before is None or after is None:
        return _check(True, "earnings-blackout", "blackout incalculable — treated as clear, flagged")
    if 0 <= (before or 0) <= before_bdays or 0 <= (after or 0) <= after_bdays:
        return _check(False, "earnings-blackout", f"inside blackout of earnings {ev_day}")
    return _check(True, "earnings-blackout", f"clear of earnings {ev_day}")


def gate_mapping(mapping_status: str, needs_levels: bool) -> dict[str, Any]:
    if not needs_levels:
        return _check(True, "mapping-verified", "no stop/limit levels proposed")
    ok = str(mapping_status or "").upper() == "VERIFIED"
    return _check(ok, "mapping-verified",
                  "mapping VERIFIED for levels" if ok
                  else f"mapping {mapping_status or '?'} — levels downgraded to RESEARCH")


def gate_evidence(direction: str, ticker: str, news_events: list[dict[str, Any]],
                  regime: str, risk_posture: str) -> dict[str, Any]:
    """Event-driven BUY needs a T0–T2 supporting event; regime-rebalance path
    cites the risk posture + accumulation-zone regime instead."""
    if direction in ("TRIM", "SELL"):
        return _check(True, "evidence", "risk-managed exit needs no fresh event")
    tagged = [e for e in (news_events or [])
              if isinstance(e, dict) and str(e.get("ticker", "")).upper() == str(ticker).upper()]
    if tagged and all(str(e.get("state", "")) == "SOCIAL" for e in tagged):
        return _check(False, "evidence", "social sentiment alone never creates a plan")
    if tagged:
        return _check(True, "evidence", f"{len(tagged)} supporting event(s)")
    if str(regime).upper() in ("POTENTIAL_ACCUMULATION_ZONE", "MUST_BUY") and risk_posture in ("NORMAL", "CAUTIOUS"):
        return _check(True, "evidence", "regime accumulation-zone + risk posture basis")
    return _check(False, "evidence", "no supporting T0–T2 event")


def gate_sleeve_known(sleeve: str | None) -> dict[str, Any]:
    ok = bool(sleeve and sleeve != "unknown")
    return _check(ok, "sleeve-known",
                  f"sleeve={sleeve}" if ok else "sleeve unknown — research only, no sizing")


def evaluate(direction: str, *, ticker: str, recon_status: str, free_cash: float,
             weight: float, cap_pct: float, earnings_status: dict[str, Any],
             report_day: str, before_bdays: int, after_bdays: int,
             mapping_status: str, needs_levels: bool,
             news_events: list[dict[str, Any]], regime: str, risk_posture: str,
             sleeve: str | None) -> list[dict[str, Any]]:
    """Full gate matrix for one proposed plan (BUY skips nothing, TRIM skips cash/evidence)."""
    gates = [gate_recon(recon_status), gate_concentration(weight, cap_pct),
             gate_blackout(ticker, earnings_status, report_day, before_bdays, after_bdays),
             gate_mapping(mapping_status, needs_levels), gate_sleeve_known(sleeve)]
    if direction in ("BUY", "ADD"):
        gates[1:1] = [gate_cash(direction, free_cash)]
        gates.append(gate_evidence(direction, ticker, news_events, regime, risk_posture))
    else:
        gates.append(gate_evidence(direction, ticker, news_events, regime, risk_posture))
    return gates
