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

    pack = {
        "symbol": symbol, "currency": currency, "standard": standard,
        "fiscal_year_end": fiscal_year_end,
        "statements_available": {"annual": n_annual, "quarterly": n_q},
        "ttm": {"revenue": ttm_rev,
                "fcf": ttm_fcf,
                "fcf_margin": (ttm_fcf / ttm_rev) if ttm_fcf is not None and ttm_rev else None,
                "net_income": ttm_ni,
                "fcf_conversion": (ttm_fcf / ttm_ni) if ttm_fcf is not None and ttm_ni else None},
        "margins_latest": {"gross": margin(gp, rev), "operating": margin(op, rev),
                           "fcf": margin(fcf, rev)},
        "cash_generation": {"ocf_latest": ocf[-1] if ocf else None,
                            "capex_latest": capex[-1] if capex else None,
                            "fcf_latest": fcf[-1] if fcf else None,
                            "sbc_latest": sbc[-1] if sbc else None,
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
        "history": history,
        "notes": notes,
    }
    return pack
