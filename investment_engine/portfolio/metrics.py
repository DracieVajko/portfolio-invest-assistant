"""Portfolio metrics: weights, P/L with FX, concentration, risk share.

Pure functions (numpy only). Every output states its denominator, coverage
and basis; missing optional data yields nulls + notes, never silent filler.
Structural input errors (unknown/missing price unit) raise ValueError —
a data-quality error, never a guess ( Eval T02).
"""

from __future__ import annotations

from typing import Any

import numpy as np

# Sub-unit price units -> multiplier to the major unit.
UNIT_MULTIPLIER = {"GBX": 0.01, "GBP": 1.0, "EUR": 1.0, "USD": 1.0,
                   "USX": 0.01, "ZAC": 0.01}


def classify_coverage(accounts: list[dict] | None) -> dict:
    """Split accounts by coverage; denominator label for weights."""
    complete, partial = [], []
    for acc in accounts or []:
        if not isinstance(acc, dict):
            continue
        (complete if str(acc.get("coverage", "")).lower() == "complete"
         else partial).append(str(acc.get("id", "?")))
    return {"complete": complete, "partial": partial,
            "denominator": ("covered accounts only"
                            if partial else "complete coverage")}


def classify_holding_status(*, held: bool, log_status: str | None = None,
                            coverage_complete: bool = True) -> str:
    """Portfolio status class (AGENTS.md 4.2).

    Absent + partial coverage -> "unknown" (never "not_found").
    Pure; never raises.
    """
    try:
        if held:
            return "confirmed_current_holding"
        if log_status:
            return str(log_status)
        return "not_found" if coverage_complete else "unknown"
    except Exception:
        return "unknown"


def position_values(holdings: list[dict], *, base_ccy: str = "EUR",
                    fx: dict | None = None, include_cash: float = 0.0) -> dict:
    """Native + base values and weights. Denominator = positions + cash.

    Holding: {symbol, quantity, price, currency, price_unit?, sector?,
    account?}. fx maps currency -> rate to base_ccy. Unknown/missing
    price_unit raises ValueError; missing fx yields native-only + note.
    """
    fx = fx or {}
    out: list[dict] = []
    notes: list[str] = []
    total = float(include_cash or 0.0)
    rows = []
    for h in holdings or []:
        if not isinstance(h, dict):
            continue
        symbol = str(h.get("symbol", "?"))
        try:
            qty = float(h.get("quantity", 0) or 0)
            price = float(h.get("price", 0) or 0)
        except (TypeError, ValueError):
            notes.append(f"{symbol}: non-numeric quantity/price, skipped")
            continue
        unit = h.get("price_unit")
        mult = UNIT_MULTIPLIER.get(str(unit or "").upper())
        if mult is None:
            raise ValueError(f"{symbol}: missing/unknown price_unit "
                             f"{unit!r} (provide GBX/GBP/EUR/…; refusing to guess)")
        ccy = str(h.get("currency", "") or "").upper()
        native = qty * price * mult
        rate = fx.get(ccy, fx.get(ccy.upper(), None)) if ccy else None
        try:
            rate = float(rate) if rate is not None else None
        except (TypeError, ValueError):
            rate = None
        base = native * rate if rate else None
        if base is None:
            notes.append(f"{symbol}: no FX {ccy}->{base_ccy}, base value null")
        else:
            total += base
        rows.append({"symbol": symbol, "quantity": qty,
                     "price_native": price * mult, "currency": ccy,
                     "value_native": native, "value_base": base,
                     "sector": str(h.get("sector", "") or ""),
                     "account": str(h.get("account", "") or "")})
    for r in rows:
        r["weight_pct"] = (100.0 * r["value_base"] / total
                           if r["value_base"] is not None and total > 0 else None)
    return {"positions": rows, "total_base": total, "cash_base": float(include_cash or 0.0),
            "denominator": "covered positions + cash", "notes": notes}


def concentration(weights: dict[str, float]) -> dict:
    """Effective N, top-5 weight. Weights in percent."""
    vals = np.array([float(v) for v in weights.values()], dtype=float)
    vals = vals[~np.isnan(vals)] / 100.0
    if vals.size == 0 or vals.sum() <= 0:
        return {"effective_n": None, "top5_weight_pct": None}
    return {"effective_n": float(1.0 / np.sum(vals ** 2)),
            "top5_weight_pct": float(100.0 * np.sum(np.sort(vals)[-5:]))}


def position_pl(*, quantity: float, avg_cost: float, cost_ccy_fx: float,
                price: float, price_ccy_fx: float) -> dict:
    """P/L split into local move vs FX contribution (all in base currency).

    pnl_base = price*qty*fx_now - avg*qty*fx_buy
    fx_contribution = pnl_base - pnl_local * fx_now
    """
    try:
        qty = float(quantity)
        local = (float(price) - float(avg_cost)) * qty
        base = float(price) * qty * float(price_ccy_fx) \
            - float(avg_cost) * qty * float(cost_ccy_fx)
    except (TypeError, ValueError):
        return {"pnl_local": None, "pnl_base": None, "fx_contribution": None}
    return {"pnl_local": local, "pnl_base": base,
            "fx_contribution": base - local * float(price_ccy_fx)}


def correlation_clusters(returns: dict[str, list[float]],
                         threshold: float = 0.7) -> list[list[str]]:
    """Connected components over |corr| >= threshold (union-find)."""
    syms = sorted(returns)
    parent = {s: s for s in syms}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    series = {}
    for s in syms:
        try:
            series[s] = np.asarray(returns[s], dtype=float)
        except (TypeError, ValueError):
            continue
    keys = sorted(series)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = series[keys[i]], series[keys[j]]
            m = min(len(a), len(b))
            if m < 3:
                continue
            c = np.corrcoef(a[:m], b[:m])[0, 1]
            if c == c and abs(float(c)) >= threshold:
                parent[find(keys[i])] = find(keys[j])
    groups: dict[str, list[str]] = {}
    for s in keys:
        groups.setdefault(find(s), []).append(s)
    return sorted(groups.values())


def risk_contribution(weights_pct: dict[str, float], vols: dict[str, float],
                      corr: dict[tuple[str, str], float] | None = None) -> dict:
    """Euler volatility share per holding (weights %, vols as decimals).

    contrib_i = w_i * (Σw)_i / σp, shares sum to 1. corr keys are
    (a, b) tuples, default 0. Returns {} when degenerate.
    """
    corr = corr or {}
    syms = [s for s in weights_pct if s in vols]
    if not syms:
        return {}
    try:
        w = np.array([float(weights_pct[s]) / 100.0 for s in syms])
        v = np.array([float(vols[s]) for s in syms])
        n = len(syms)
        cov = np.zeros((n, n))
        for i in range(n):
            for j in range(n):
                if i == j:
                    cov[i, j] = v[i] ** 2
                else:
                    rho = corr.get((syms[i], syms[j]),
                                   corr.get((syms[j], syms[i]), 0.0))
                    cov[i, j] = float(rho) * v[i] * v[j]
        var_p = float(w @ cov @ w)
        if var_p <= 0:
            return {}
        sig = float(np.sqrt(var_p))
        marginal = (cov @ w) / sig
        contrib = w * marginal / sig
    except (TypeError, ValueError):
        return {}
    return {s: {"vol_share": float(contrib[i])} for i, s in enumerate(syms)}


def drawdown_share(weights_pct: dict[str, float],
                   max_dd: dict[str, float]) -> dict:
    """Approximate drawdown contribution: w_i*dd_i shares (documented proxy)."""
    num = {}
    for s, w in weights_pct.items():
        if s in max_dd:
            try:
                num[s] = abs(float(w) / 100.0 * float(max_dd[s]))
            except (TypeError, ValueError):
                continue
    total = sum(num.values())
    if total <= 0:
        return {}
    return {s: {"dd_share": float(v / total), "dd_abs": float(v)}
            for s, v in num.items()}


def lookthrough_overlap(existing: dict[str, float] | None,
                        candidate: dict[str, float] | None) -> dict:
    """ETF look-through overlap from issuer holdings files ONLY.

    Inputs map issuer -> weight %. Either side missing -> {"overlap": "unknown"}.
    Metric: sum of min() weights (shared economic exposure).
    """
    if not isinstance(existing, dict) or not isinstance(candidate, dict):
        return {"overlap": "unknown",
                "note": "issuer holdings file missing on at least one side"}
    try:
        shared = sorted(set(existing) & set(candidate))
        overlap = sum(min(float(existing[s]), float(candidate[s])) for s in shared)
    except (TypeError, ValueError):
        return {"overlap": "unknown", "note": "unparsable holdings"}
    return {"overlap_pct": float(overlap), "shared_issuers": shared}


def size_position(*, portfolio_value: float, adverse_move_pct: float,
                  loss_budget_pct: float | None = None,
                  caps: dict | None = None,
                  max_drawdown_tolerance_pct: float | None = None) -> dict:
    """AGENTS.md 9.3 sizing. max_drawdown_tolerance_pct is accepted and
    IGNORED (guard: it must never become a per-position budget).

    Returns final_pct + binding_constraint, or a provisional range when all
    caps are null (upper bound <= 5%).
    """
    _ = max_drawdown_tolerance_pct  # documented ignore (T05 case 4)
    try:
        adverse = float(adverse_move_pct)
        value = float(portfolio_value)
        if adverse <= 0 or value <= 0:
            return {"error": "adverse_move_pct and portfolio_value must be > 0"}
        defaulted = loss_budget_pct is None
        budget = float(loss_budget_pct) if not defaulted else 1.0
        raw_pct = budget / adverse * 100.0
    except (TypeError, ValueError):
        return {"error": "non-numeric inputs"}
    caps = caps or {}
    live = {}
    for key in ("max_single_stock_pct", "max_single_etf_pct", "max_sector_pct",
                "max_country_pct", "max_speculative_pct"):
        try:
            if caps.get(key) is not None:
                live[key] = float(caps[key])
        except (TypeError, ValueError):
            continue
    if not live:
        return {"notional_pct": None,
                "range_pct": [0.0, round(min(raw_pct, 5.0), 4)],
                "provisional": True, "budget_defaulted": defaulted,
                "note": "no caps: provisional range, upper bound 5%"}
    binding = "formula"
    final = raw_pct
    for key, cap in live.items():
        if cap < final:
            final, binding = cap, key
    return {"notional_pct": round(final, 4),
            "notional_value": round(final / 100.0 * value, 2),
            "binding_constraint": binding, "budget_defaulted": defaulted,
            "formula_pct": round(raw_pct, 4)}


def resulting_exposure(holdings: list[dict], candidate: dict,
                       caps: dict | None = None) -> dict:
    """Post-trade weights (cash-funded) + cap-breach verdict suggestion.

    holdings: [{symbol, value_base, sector, currency}].
    candidate: {symbol, amount_base, sector, currency}.
    """
    try:
        total = sum(float(h.get("value_base", 0) or 0) for h in holdings or [])
        amount = float(candidate.get("amount_base", 0) or 0)
        new_total = total + amount
        if new_total <= 0:
            return {"error": "empty portfolio"}
    except (TypeError, ValueError):
        return {"error": "non-numeric inputs"}
    sym = str(candidate.get("symbol", "?"))
    post_w = 100.0 * amount / new_total
    sector = str(candidate.get("sector", "") or "")
    ccy = str(candidate.get("currency", "") or "")
    sector_w = 100.0 * (sum(float(h.get("value_base", 0) or 0)
                             for h in holdings or []
                             if str(h.get("sector", "")) == sector) + amount) / new_total
    ccy_w = 100.0 * (sum(float(h.get("value_base", 0) or 0)
                          for h in holdings or []
                          if str(h.get("currency", "")) == ccy) + amount) / new_total
    breaches = []
    for key, limit, got in (
            ("max_single_stock_pct", (caps or {}).get("max_single_stock_pct"), post_w),
            ("max_sector_pct", (caps or {}).get("max_sector_pct"), sector_w),
            ("max_country_pct", (caps or {}).get("max_country_pct"), ccy_w)):
        try:
            if limit is not None and got > float(limit):
                breaches.append({"cap": key, "limit": float(limit),
                                 "result": round(got, 4)})
        except (TypeError, ValueError):
            continue
    return {"post_weight_pct": round(post_w, 4),
            "post_sector_weight_pct": round(sector_w, 4),
            "post_currency_weight_pct": round(ccy_w, 4),
            "breach": bool(breaches), "breaches": breaches,
            "suggested_verdict": ("WATCH (downgraded: cap breach)"
                                  if breaches else "no breach")}
