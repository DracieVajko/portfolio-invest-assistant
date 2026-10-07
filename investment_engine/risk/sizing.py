"""Phase 7 indicative sizing: fixed-fractional with documented assumptions.

notional = min(position headroom, sector headroom, risk cap, deployable cash)

Risk cap = risk_pct * total_equity / adverse_move_assumption.
Default assumption: a 25% adverse move should cost at most risk_pct of equity
(2% default → max notional 8% of equity). The assumption is explicit in the
policy file and overridable; plans always carry the assumption used.
All notionals are INDICATIVE (whole-share rounding and broker fractional
support are outside this program).
"""

from __future__ import annotations


def position_headroom_eur(weight_pct: float, cap_pct: float, total_equity: float) -> float:
    return max(0.0, (cap_pct - weight_pct) / 100.0 * max(0.0, total_equity))


def risk_cap_eur(total_equity: float, risk_pct: float, adverse_move: float = 0.25) -> float:
    if adverse_move <= 0:
        return 0.0
    return max(0.0, risk_pct / 100.0 * max(0.0, total_equity) / adverse_move)


def indicative_notional_eur(*, weight_pct: float, cap_pct: float, total_equity: float,
                            risk_pct: float, adverse_move: float,
                            sector_headroom_eur: float, deployable_cash_eur: float) -> dict:
    """Return {notional_eur, binding_constraint, inputs} — never negative."""
    cands = {
        "position-cap": position_headroom_eur(weight_pct, cap_pct, total_equity),
        "sector-cap": max(0.0, sector_headroom_eur),
        "risk-cap": risk_cap_eur(total_equity, risk_pct, adverse_move),
        "cash": max(0.0, deployable_cash_eur),
    }
    binding = min(cands, key=lambda k: cands[k])
    return {"notional_eur": round(cands[binding], 2), "binding_constraint": binding,
            "inputs": {k: round(v, 2) for k, v in cands.items()}}
