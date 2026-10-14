"""Integration-matrix decision function (AGENTS.md 10.4).

decide() maps (thesis, valuation, regime, modifiers, held, horizon,
fit_breach, instrument_class, overweight, decline_thesis_relevant) to
{allowed, default, notes}. Total and deterministic; guards enforced last:
held assets never get AVOID, not-held never get HOLD/ADD/TRIM/SELL,
"strengthened" maps to intact rows.
"""

from __future__ import annotations

from typing import Any

_HELD_ONLY = {"HOLD", "TRIM", "SELL"}
_NOT_HELD_ONLY = {"BUY", "ADD", "WATCH", "AVOID"}


def decide(*, thesis: str = "uncertain", valuation: str = "fair",
           regime: str = "TRANSITION_OR_RANGE",
           modifiers: list[str] | None = None, held: bool = False,
           horizon: str = "strategic", fit_breach: bool = False,
           instrument_class: str = "single_stock",
           overweight: bool = False,
           decline_thesis_relevant: bool = True) -> dict:
    """Return {allowed (sorted list), default, notes (list)}."""
    mods = {str(m or "").upper() for m in (modifiers or [])}
    tstate = str(thesis or "uncertain").lower()
    if tstate == "strengthened":
        tstate = "intact"
    val = str(valuation or "fair").lower()
    reg = str(regime or "TRANSITION_OR_RANGE").upper()
    hor = str(horizon or "strategic").lower()
    notes: list[str] = []
    allowed: set[str] = set()
    default = "WATCH" if not held else "HOLD"

    downtrend = reg in ("DOWNTREND", "BEAR_RALLY")
    uptrend = reg in ("STRONG_UPTREND", "UPTREND_PULLBACK")

    if tstate == "intact" and val == "attractive":
        if uptrend and "EXTENDED" not in mods:
            allowed, default = {"BUY", "ADD", "WATCH"}, "BUY"
        elif uptrend:
            allowed, default = {"BUY", "WATCH"}, "BUY"
            notes.append("EXTENDED column: tranche only")
        elif downtrend:
            if decline_thesis_relevant:
                allowed, default = {"WATCH"}, "WATCH"
            else:
                allowed, default = {"BUY", "WATCH"}, "BUY"
                notes.append("small first tranche; decline not thesis-relevant")
        else:
            allowed, default = {"BUY", "WATCH"}, "BUY"
            notes.append("range: tranches")
    elif tstate == "intact" and val == "fair":
        if str(instrument_class or "") == "broad_fund" and not held:
            allowed, default = {"BUY", "WATCH"}, "BUY"
            notes.append("broad fund: chart affects staging only")
        else:
            allowed, default = ({"HOLD"} if held else {"WATCH"}), \
                ("HOLD" if held else "WATCH")
    elif tstate == "intact" and val == "stretched":
        if held and overweight:
            allowed, default = {"TRIM", "HOLD"}, "TRIM"
        elif held:
            allowed, default = {"HOLD"}, "HOLD"
            notes.append("TRIM needs overweight or weak thesis")
        else:
            allowed, default = {"AVOID", "WATCH"}, "AVOID"
    elif tstate in ("weakened", "uncertain"):
        if held:
            allowed, default = {"HOLD", "TRIM", "SELL"}, "HOLD"
            notes.append("dated reassessment, no add")
        else:
            allowed, default = {"AVOID", "WATCH"}, "AVOID"
    elif tstate == "broken":
        if held:
            allowed, default = {"SELL", "TRIM"}, "SELL"
            notes.append("chart ignored (row E)")
        else:
            allowed, default = {"AVOID"}, "AVOID"
    else:
        notes.append("unlisted combination: conservative fallback")
        allowed, default = ({"HOLD"} if held else {"WATCH"}), \
            ("HOLD" if held else "WATCH")

    if hor == "tactical" and reg == "DOWNTREND" and not held:
        allowed.add("WATCH")
        notes.append("tactical horizon: chart may dominate")
    if fit_breach:
        allowed -= {"BUY", "ADD"}
        notes.append("fit gate: caps/restrictions can only downgrade")
        if default in ("BUY", "ADD"):
            default = "WATCH" if not held else "HOLD"

    # Guards last (10.4, held-assets rule).
    if held:
        allowed -= {"AVOID"}
        if default == "AVOID":
            default = "HOLD"
    else:
        allowed -= {"HOLD", "ADD", "TRIM", "SELL"}
        if default not in allowed:
            default = "WATCH" if "WATCH" in allowed else sorted(allowed)[0]
    if not allowed:
        allowed = {"HOLD"} if held else {"WATCH"}
        default = next(iter(allowed))
    return {"allowed": sorted(allowed), "default": default, "notes": notes}


def allowed_for(verdict: str, **kwargs: Any) -> bool:
    """True when a verdict is in the allowed set for the given situation."""
    try:
        return str(verdict or "").upper() in decide(**kwargs)["allowed"]
    except Exception:
        return False
