"""Fundamentals pack builder (operating instructions §5.2, Appendix D).

Pure functions over yfinance-shaped statements (no network here).
Definitions follow Appendix D verbatim; every figure states its basis and
every gap is null with a note. Never fills history from memory: growth
rates and percentiles are computed only over the years actually present.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# yfinance row-label aliases -> canonical metric.
ALIASES: dict[str, tuple[str, ...]] = {
    "revenue": ("Total Revenue", "Revenue", "Sales"),
    "gross_profit": ("Gross Profit",),
    "operating_income": ("Operating Income", "EBIT"),
    "ebit": ("EBIT", "Operating Income"),
    "ebitda": ("EBITDA",),
    "net_income": ("Net Income Common Stockholders", "Net Income",
                   "Net Income Including Noncontrolling Interests"),
    "eps_diluted": ("Diluted EPS", "DilutedEPS"),
    "ocf": ("Operating Cash Flow", "Total Operating Cash Flow"),
    "capex": ("Capital Expenditure",),
    "sbc": ("Stock Based Compensation",),
    "interest_expense": ("Interest Expense", "Interest Expense Non Operating"),
    "tax_provision": ("Tax Provision",),
    "pretax_income": ("Pretax Income", "Earnings Before Tax"),
    "total_debt": ("Total Debt",),
    "long_term_debt": ("Long Term Debt",),
    "short_debt": ("Current Debt", "Short Term Debt", "Short Long Term Debt"),
    "cash": ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"),
    "equity": ("Total Equity Gross Minority Interest", "Stockholders Equity",
               "Total Stockholders Equity"),
    "shares": ("Ordinary Shares Number", "Share Issued", "Diluted Average Shares",
               "Basic Average Shares"),
    "dividends_paid": ("Cash Dividends Paid",),
    "retained_earnings": ("Retained Earnings",),
}


def pick_row(frame: pd.DataFrame, metric: str) -> pd.Series | None:
    """First matching yfinance row for a canonical metric (case-insensitive)."""
    if frame is None or frame.empty:
        return None
    index = {str(i).strip().lower(): i for i in frame.index}
    for alias in ALIASES.get(metric, ()):
        if alias.strip().lower() in index:
            return frame.loc[index[alias.strip().lower()]]
    return None


def _num(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _col(row: pd.Series | None, pos: int = 0) -> float | None:
    if row is None:
        return None
    try:
        vals = pd.to_numeric(row, errors="coerce").to_numpy(dtype=float)
        return _num(vals[pos]) if len(vals) > pos else None
    except Exception:
        return None


def _ttm(qframe: pd.DataFrame, metric: str) -> float | None:
    row = pick_row(qframe, metric)
    if row is None:
        return None
    try:
        vals = pd.to_numeric(row, errors="coerce").to_numpy(dtype=float)[:4]
        vals = vals[~np.isnan(vals)]
        return float(np.sum(vals)) if vals.size else None
    except Exception:
        return None


def _cagr(values: list[float | None]) -> float | None:
    clean = [v for v in values if v is not None and v == v]
    if len(clean) < 2 or not clean[0] or clean[0] <= 0:
        return None
    try:
        return float((clean[-1] / clean[0]) ** (1.0 / (len(clean) - 1)) - 1.0)
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def annual_series(annual: pd.DataFrame, metric: str) -> list[float | None]:
    """Oldest-first annual values for a metric ([] when absent)."""
    row = pick_row(annual, metric)
    if row is None:
        return []
    try:
        vals = pd.to_numeric(row, errors="coerce").to_numpy(dtype=float)
        return [_num(v) for v in vals[::-1]]
    except Exception:
        return []


def build_pack(
    annual_fin: pd.DataFrame | None = None,
    annual_bs: pd.DataFrame | None = None,
    annual_cf: pd.DataFrame | None = None,
    quarterly_fin: pd.DataFrame | None = None,
    *,
    symbol: str = "",
    currency: str = "",
    standard: str = "",
    fiscal_year_end: str = "",
) -> dict:
    """Assemble the §5.2 pack. Never raises; gaps are null + notes."""
    notes: list[str] = []
    annual_fin = annual_fin if annual_fin is not None else pd.DataFrame()
    annual_bs = annual_bs if annual_bs is not None else pd.DataFrame()
    annual_cf = annual_cf if annual_cf is not None else pd.DataFrame()
    quarterly_fin = quarterly_fin if quarterly_fin is not None else pd.DataFrame()
    n_annual = len(annual_fin.columns) if not annual_fin.empty else 0
    n_q = len(quarterly_fin.columns) if not quarterly_fin.empty else 0
    if n_annual < 3:
        notes.append(f"only {n_annual} annual statements: trend conclusions weak")
    if n_annual == 0:
        notes.append("no annual statements")

    rev = annual_series(annual_fin, "revenue")
    gp = annual_series(annual_fin, "gross_profit")
    op = annual_series(annual_fin, "operating_income")
    ni = annual_series(annual_fin, "net_income")
    eps = annual_series(annual_fin, "eps_diluted")
    ocf = annual_series(annual_cf if not annual_cf.empty else annual_fin, "ocf")
    capex = annual_series(annual_cf if not annual_cf.empty else annual_fin, "capex")
    sbc = annual_series(annual_cf if not annual_cf.empty else annual_fin, "sbc")
    shares = annual_series(annual_fin, "shares") or annual_series(annual_bs, "shares")
    divs = annual_series(annual_cf if not annual_cf.empty else annual_fin, "dividends_paid")
    ebit = annual_series(annual_fin, "ebit")
    interest = annual_series(annual_fin, "interest_expense")
    tax_prov = annual_series(annual_fin, "tax_provision")
    pretax = annual_series(annual_fin, "pretax_income")

    fcf = [None if a is None or b is None else a + b  # capex is negative in statements
           for a, b in zip(ocf, capex)] if ocf and capex else []

    def margin(num: list, den: list, i: int = -1) -> float | None:
        try:
            if num and den and num[i] is not None and den[i]:
                return float(num[i] / den[i])
        except (IndexError, TypeError, ZeroDivisionError):
            pass
        return None

    # TTM from quarterlies (fallback: latest annual).
    ttm_rev = _ttm(quarterly_fin, "revenue") if n_q else None
    if ttm_rev is None and rev:
        ttm_rev = rev[-1]
        if n_q == 0:
            notes.append("no quarterlies: TTM falls back to latest annual")
    ttm_ocf = _ttm(quarterly_fin, "ocf") if n_q else None
    if ttm_ocf is None and ocf:
        ttm_ocf = ocf[-1]
    ttm_capex = _ttm(quarterly_fin, "capex") if n_q else None
    if ttm_capex is None and capex:
        ttm_capex = capex[-1]
    ttm_fcf = (ttm_ocf + ttm_capex) if ttm_ocf is not None and ttm_capex is not None else None
    ttm_ni = _ttm(quarterly_fin, "net_income") if n_q else None
    if ttm_ni is None and ni:
        ttm_ni = ni[-1]

    # Balance sheet (latest column).
    debt = _col(pick_row(annual_bs, "total_debt"))
    if debt is None:
        ltd = _col(pick_row(annual_bs, "long_term_debt")) or 0.0
        std = _col(pick_row(annual_bs, "short_debt")) or 0.0
        debt = ltd + std if (ltd or std) else None
    cash = _col(pick_row(annual_bs, "cash"))
    equity = _col(pick_row(annual_bs, "equity"))
    net_debt = (debt - cash) if debt is not None and cash is not None else None
    if debt is not None:
        notes.append("net debt = interest-bearing debt incl. reported leases where "
                     "present, minus cash (lease split not separated)")

    # ROIC needs an effective tax rate from the statements (never invented).
    eff_rate = None
    if tax_prov and pretax and tax_prov[-1] is not None and pretax[-1]:
        try:
            eff_rate = float(tax_prov[-1] / pretax[-1])
        except (TypeError, ZeroDivisionError):
            eff_rate = None
    roic = None
    if ebit and ebit[-1] is not None and eff_rate is not None and equity is not None \
            and net_debt is not None and (equity + net_debt) > 0:
        try:
            roic = float(ebit[-1] * (1 - eff_rate) / (equity + net_debt))
        except (TypeError, ZeroDivisionError):
            roic = None
    if roic is None:
        notes.append("ROIC not computable (needs EBIT + effective rate + capital)")

    ebitda_hist = annual_series(annual_fin, "ebitda")
    nd_ebitda = (net_debt / ebitda_hist[-1]) if net_debt is not None and ebitda_hist \
        and ebitda_hist[-1] else None
    ic = (ebit[-1] / interest[-1]) if ebit and interest and ebit[-1] is not None \
        and interest[-1] else None

    dilution = None
    if len(shares) >= 2 and shares[0]:
        try:
            dilution = float((shares[-1] / shares[0]) ** (1.0 / (len(shares) - 1)) - 1.0)
        except (ValueError, ZeroDivisionError, OverflowError):
            dilution = None

    dps = None
    if divs and divs[-1] is not None and shares and shares[-1]:
        try:
            dps = abs(float(divs[-1])) / float(shares[-1])
        except (TypeError, ValueError, ZeroDivisionError):
            dps = None

    years = sorted({str(c)[:10] for c in list(annual_fin.columns)}) if n_annual else []
    history = []
    for i in range(len(rev)):
        history.append({
            "year": years[i] if i < len(years) else "",
            "revenue": rev[i] if i < len(rev) else None,
            "gross_margin": margin(gp, rev, i),
            "operating_margin": margin(op, rev, i),
            "fcf_margin": margin(fcf, rev, i),
            "eps_diluted": eps[i] if i < len(eps) else None,
            "ocf": ocf[i] if i < len(ocf) else None,
            "capex": capex[i] if i < len(capex) else None,
            "fcf": fcf[i] if i < len(fcf) else None,
            "shares": shares[i] if i < len(shares) else None,
            "dps": None,
        })
    if history and dps is not None:
        history[-1]["dps"] = dps

    # GAAP vs adjusted labeling: record exactly which yfinance row fed each
    # series, so a mixed basis can never pass silently (T15).
    def _source_of(frame: pd.DataFrame, metric: str) -> str:
        row = pick_row(frame, metric)
        if row is None:
            return ""
        try:
            return str(row.name)
        except AttributeError:
            return ""

    row_sources = {
        "revenue": _source_of(annual_fin, "revenue"),
        "eps_diluted": _source_of(annual_fin, "eps_diluted"),
        "net_income": _source_of(annual_fin, "net_income"),
    }

    fcf_latest = fcf[-1] if fcf else None
    sbc_latest = sbc[-1] if sbc else None
    fcf_after_sbc = (fcf_latest - sbc_latest
                     if fcf_latest is not None and sbc_latest is not None else None)
    ttm_sbc = None
    if n_q:
        _sbc_row = pick_row(quarterly_fin, "sbc")
        if _sbc_row is not None:
            try:
                _vals = pd.to_numeric(_sbc_row, errors="coerce").to_numpy(dtype=float)[:4]
                _vals = _vals[~np.isnan(_vals)]
                ttm_sbc = float(np.sum(_vals)) if _vals.size else None
            except Exception:
                ttm_sbc = None
    if ttm_sbc is None and sbc:
        ttm_sbc = sbc[-1]
    ttm_fcf_after_sbc = (ttm_fcf - ttm_sbc
                         if ttm_fcf is not None and ttm_sbc is not None else None)

    pack = {
        "symbol": symbol, "currency": currency, "standard": standard,
        "fiscal_year_end": fiscal_year_end,
        "statements_available": {"annual": n_annual, "quarterly": n_q},
        "ttm": {"revenue": ttm_rev,
                "fcf": ttm_fcf,
                "fcf_after_sbc": ttm_fcf_after_sbc,
                "fcf_margin": (ttm_fcf / ttm_rev) if ttm_fcf is not None and ttm_rev else None,
                "net_income": ttm_ni,
                "fcf_conversion": (ttm_fcf / ttm_ni) if ttm_fcf is not None and ttm_ni else None},
        "margins_latest": {"gross": margin(gp, rev), "operating": margin(op, rev),
                           "fcf": margin(fcf, rev)},
        "cash_generation": {"ocf_latest": ocf[-1] if ocf else None,
                            "capex_latest": capex[-1] if capex else None,
                            "fcf_latest": fcf_latest,
                            "fcf_after_sbc": fcf_after_sbc,
                            "sbc_latest": sbc_latest,
                            "sbc_to_revenue": margin(sbc, rev),
                            "sbc_to_fcf": (abs(sbc[-1] / fcf[-1])) if sbc and fcf
                            and sbc[-1] is not None and fcf[-1] else None},
        "balance": {"net_debt": net_debt, "cash": cash, "equity": equity,
                    "net_debt_to_ebitda": nd_ebitda,
                    "interest_coverage": ic,
                    "effective_tax_rate": eff_rate},
        "returns": {"roic": roic},
        "shares": {"latest": shares[-1] if shares else None,
                   "dilution_cagr": dilution},
        "dividends": {"dps_latest": dps,
                      "payout_on_net_income": (abs(divs[-1] / ni[-1])) if divs and ni
                      and divs[-1] is not None and ni[-1] else None,
                      "payout_on_fcf": (abs(divs[-1] / fcf[-1])) if divs and fcf
                      and divs[-1] is not None and fcf[-1] else None},
        "growth": {"revenue_cagr_3y": _cagr(rev[-4:]) if len(rev) >= 4 else None,
                   "revenue_cagr_5y": _cagr(rev[-6:]) if len(rev) >= 6 else None,
                   "revenue_cagr_10y": _cagr(rev[-11:]) if len(rev) >= 11 else None},
        "valuation_percentile": "not computable",
        "valuation_note": ("no point-in-time historical multiples from free "
                           "vendor data; percentiles need 5-10y of dated multiples"),
        "row_sources": row_sources,
        "history": history,
        "notes": notes,
    }
    return pack


def dcf_value(*, fcf_base: float, growth: float, discount: float,
              years: int = 10, terminal_growth: float = 0.02) -> float | None:
    """Enterprise value for a constant-FCF-growth path (Appendix D notes).

    Pure helper behind reverse_dcf; terminal growth is capped at 3%.
    Never raises (None on bad inputs).
    """
    try:
        fcf = float(fcf_base)
        g = float(growth)
        r = float(discount)
        n = int(years)
        tg = min(float(terminal_growth), 0.03)
        if n <= 0 or r <= tg:
            return None
    except (TypeError, ValueError):
        return None
    try:
        pv = sum(fcf * (1 + g) ** t / (1 + r) ** t for t in range(1, n + 1))
        tv = fcf * (1 + g) ** n * (1 + tg) / ((r - tg) * (1 + r) ** n)
        return float(pv + tv)
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def reverse_dcf(*, ev: float, fcf_ttm: float,
                discount_range: tuple[float, float] = (0.08, 0.10),
                years: int = 10, terminal_growth: float = 0.02,
                growth_grid: tuple[float, ...] = (-0.05, 0.0, 0.05, 0.10, 0.15)
                ) -> dict:
    """Implied FCF CAGR priced in at today's EV, per discount rate.

    Binary-searches g in [-50%, +100%] so that dcf_value == ev, plus an
    EV sensitivity grid over (discount x assumed growth). Pure; never raises.
    Terminal growth capped at 3% (long-run nominal ceiling).
    """
    out: dict = {"implied_growth": {}, "sensitivity": [], "inputs": {},
                 "note": ""}
    try:
        target = float(ev)
        fcf = float(fcf_ttm)
        lo_r, hi_r = float(discount_range[0]), float(discount_range[1])
        if target <= 0 or fcf <= 0 or lo_r <= 0 or hi_r < lo_r:
            out["note"] = "needs positive EV/FCF and a valid discount range"
            return out
    except (TypeError, ValueError, IndexError):
        out["note"] = "non-numeric inputs"
        return out
    out["inputs"] = {"ev": target, "fcf_ttm": fcf,
                     "discount_range": [lo_r, hi_r], "years": years,
                     "terminal_growth": min(float(terminal_growth), 0.03)}
    steps = max(1, int(round((hi_r - lo_r) / 0.01)))
    rates = sorted({round(lo_r + i * (hi_r - lo_r) / steps, 4)
                    for i in range(steps + 1)})

    def implied(r: float) -> float | None:
        lo_g, hi_g = -0.5, 1.0
        v_lo = dcf_value(fcf_base=fcf, growth=lo_g, discount=r, years=years,
                         terminal_growth=terminal_growth)
        v_hi = dcf_value(fcf_base=fcf, growth=hi_g, discount=r, years=years,
                         terminal_growth=terminal_growth)
        if v_lo is None or v_hi is None:
            return None
        if not (v_lo <= target <= v_hi):
            return None
        for _ in range(60):
            mid = (lo_g + hi_g) / 2.0
            v = dcf_value(fcf_base=fcf, growth=mid, discount=r, years=years,
                          terminal_growth=terminal_growth)
            if v is None:
                return None
            if v < target:
                lo_g = mid
            else:
                hi_g = mid
        return round((lo_g + hi_g) / 2.0, 4)

    for r in rates:
        g = implied(r)
        out["implied_growth"][str(r)] = g
        row: dict = {"discount": r, "implied_growth": g, "ev_at_assumed": {}}
        for ga in growth_grid:
            try:
                row["ev_at_assumed"][str(ga)] = dcf_value(
                    fcf_base=fcf, growth=float(ga), discount=r, years=years,
                    terminal_growth=terminal_growth)
            except (TypeError, ValueError):
                row["ev_at_assumed"][str(ga)] = None
        out["sensitivity"].append(row)
    out["note"] = ("what you need to believe: FCF CAGR in implied_growth; "
                   "sensitivity shows model EV at assumed growth rates")
    return out


def scenarios(*, revenue_base: float, cases: dict[str, dict],
              shares_outstanding: float, net_debt: float = 0.0,
              dividends_per_share: float = 0.0) -> dict:
    """Bear/base/bull equity value per share from explicit drivers.

    cases: {name: {revenue_growth, net_margin, exit_pe, years}}.
    value = (EPS_yearN x exit_PE + DPS x years - net_debt/shares);
    undiscounted, labeled judgment. Pure; never raises.
    """
    out: dict = {"cases": {}, "inputs_echo": {},
                 "note": "undiscounted; judgment, not a forecast"}
    try:
        rev = float(revenue_base)
        shares = float(shares_outstanding)
        debt = float(net_debt)
        dps = float(dividends_per_share)
        if rev <= 0 or shares <= 0 or not isinstance(cases, dict):
            out["note"] += "; needs positive revenue/shares and a cases map"
            return out
    except (TypeError, ValueError):
        out["note"] += "; non-numeric inputs"
        return out
    out["inputs_echo"] = {"revenue_base": rev, "shares_outstanding": shares,
                          "net_debt": debt, "dividends_per_share": dps,
                          "cases": cases}
    for name, c in cases.items():
        try:
            g = float(c.get("revenue_growth", 0) or 0)
            m = float(c.get("net_margin", 0) or 0)
            pe = float(c.get("exit_pe", 0) or 0)
            yrs = int(c.get("years", 5) or 5)
            rev_n = rev * (1 + g) ** yrs
            eps_n = rev_n * m / shares
            value = eps_n * pe + dps * yrs - debt / shares
            out["cases"][str(name)] = {"value_per_share": round(value, 2),
                                       "eps_year_n": round(eps_n, 2),
                                       "revenue_year_n": round(rev_n, 2)}
        except (TypeError, ValueError, KeyError, AttributeError):
            out["cases"][str(name)] = {"value_per_share": None}
    return out
