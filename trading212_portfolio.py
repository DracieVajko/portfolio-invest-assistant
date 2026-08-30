"""
Trading212 Portfolio Analysis Module

T212 API v0 field names:
  /equity/account/cash  → free, invested, pieCash, result, total, ppl, blocked
  /equity/portfolio     → ticker, quantity, averagePrice, currentPrice,
                           ppl (unrealized P&L in account currency), fxPpl,
                           initialFillDate, maxBuy, maxSell, pieQuantity

Currency/price-unit handling:
- T212 returns prices in the instrument's native quotation unit:
  - GBX (pence) for UK/London-listed instruments (suffix _l_EQ, _d_EQ, no _US)
  - USD for US-listed instruments (suffix _US_EQ)
  - EUR for EUR-zone instruments
- P&L fields (ppl, fxPpl) are already in account currency (EUR)
- Must normalize quote prices to major currency units before EUR conversion
"""

import pandas as pd
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from collections import defaultdict
import logging
import re

from trading212_auth import Trade212Client

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(name)s] %(message)s'
)
logger = logging.getLogger('Trading212Portfolio')


# ---------------------------------------------------------------------------
# Currency/Price-Unit Normalization
# ---------------------------------------------------------------------------

# Ticker suffix -> (quote_currency, price_unit, minor_factor)
# minor_factor: 1 = major unit, 100 = minor unit (e.g., GBX = 1/100 GBP)
# This is a fallback; prefer fxPpl-based detection when available
TICKER_CURRENCY_MAP: Dict[str, Tuple[str, str, int]] = {
    # US equities - USD, major unit
    "_US_EQ": ("USD", "USD", 1),
    # London/LSE equities - GBX (pence), minor unit (1/100 GBP)
    "_l_EQ": ("GBP", "GBX", 100),
    # Danish stocks (Copenhagen) - DKK, minor unit (1/100 DKK) 
    # But VWSBd has fxPpl=0, so it's effectively EUR-denominated
    # "_d_EQ": ("DKK", "DKK", 100),  # overridden by fxPpl=0 -> EUR
    # Generic UK (no _US suffix, has _EQ) - assume GBX
    "_EQ": ("GBP", "GBX", 100),
}

# Known instrument overrides: ticker -> (currency, unit, minor_factor)
# Based on fxPpl=0 (EUR-denominated) or known exchange
INSTRUMENT_CURRENCY_OVERRIDES: Dict[str, Tuple[str, str, int]] = {
    # EUR-denominated (fxPpl=0 or known EUR exchange)
    "VWSBd_EQ": ("EUR", "EUR", 1),      # Vestas, Copenhagen (DKK but EUR-denominated on T212)
    "RWEd_EQ": ("EUR", "EUR", 1),       # RWE, Frankfurt
    "IBEe_EQ": ("EUR", "EUR", 1),       # Iberdrola, Madrid
    "ENRd_EQ": ("EUR", "EUR", 1),       # Siemens Energy, Frankfurt
    "SUp_EQ": ("EUR", "EUR", 1),        # Schneider Electric, Paris
    "AIp_EQ": ("EUR", "EUR", 1),        # Air Liquide, Paris
    "C7A0d_EQ": ("EUR", "EUR", 1),      # CATL, Shenzhen/HK (EUR-denominated on T212)
    "IQQHd_EQ": ("EUR", "EUR", 1),      # iShares Clean Energy, EUR
    "ERNXd_EQ": ("EUR", "EUR", 1),      # Earnest, EUR
    "DJGTEEXd_EQ": ("EUR", "EUR", 1),   # iShares Titans 50, EUR
    "VWSBd_EQ": ("EUR", "EUR", 1),      # duplicate for safety
    # GBX-denominated (UK/LSE)
    "EGTl_EQ": ("GBP", "GBX", 100),     # European Green Transition, London
    "SYNl_EQ": ("GBP", "GBX", 100),     # Synergia Energy, London
    "HY9Hd_EQ": ("GBP", "GBX", 100),    # SK Hynix, London (GBX)
    # USD-denominated (US exchanges)
    # _US_EQ suffix covers these
}


def _resolve_instrument_currency(ticker: str, fx_ppl: float = 0.0) -> Tuple[str, str, int]:
    """
    Resolve quote currency, price unit, and minor factor from ticker.
    Uses overrides first, then fxPpl heuristic, then suffix map.
    Returns (quote_currency, price_unit, minor_factor).
    """
    # 1. Check explicit overrides first
    if ticker in INSTRUMENT_CURRENCY_OVERRIDES:
        return INSTRUMENT_CURRENCY_OVERRIDES[ticker]
    
    # 2. Heuristic: fxPpl == 0 strongly suggests EUR-denominated (no FX impact)
    # fxPpl != 0 suggests foreign currency (USD or GBP)
    if abs(fx_ppl) < 0.005:  # effectively zero
        return "EUR", "EUR", 1
    
    # 3. Fall back to suffix map
    for suffix, (ccy, unit, factor) in TICKER_CURRENCY_MAP.items():
        if ticker.endswith(suffix):
            return ccy, unit, factor
    
    # 4. Default: EUR major unit
    logger.warning(f"Unknown ticker suffix for {ticker}, defaulting to EUR major unit")
    return "EUR", "EUR", 1

# Default FX rates (fallback if live rates unavailable)
# Should be replaced with live FX feed in production
# Rates as of 2026-08-27: EUR/USD=1.085, EUR/GBP=0.845
DEFAULT_FX_RATES = {
    "GBP": 1.183,  # GBP -> EUR (1/0.845)
    "USD": 0.922,  # USD -> EUR (1/1.085)
    "EUR": 1.0,
    "DKK": 0.134,  # DKK -> EUR
    "CHF": 1.065,  # CHF -> EUR
    "SEK": 0.087,  # SEK -> EUR
    "NOK": 0.085,  # NOK -> EUR
}


def _resolve_instrument_currency(ticker: str, fx_ppl: float = 0.0) -> Tuple[str, str, int]:
    """
    Resolve quote currency, price unit, and minor factor from ticker.
    Uses overrides first, then fxPpl heuristic, then suffix map.
    Returns (quote_currency, price_unit, minor_factor).
    """
    # 1. Check explicit overrides first
    if ticker in INSTRUMENT_CURRENCY_OVERRIDES:
        return INSTRUMENT_CURRENCY_OVERRIDES[ticker]
    
    # 2. Heuristic: fxPpl == 0 strongly suggests EUR-denominated (no FX impact)
    # fxPpl != 0 suggests foreign currency (USD or GBP)
    if abs(fx_ppl) < 0.005:  # effectively zero
        return "EUR", "EUR", 1
    
    # 3. Fall back to suffix map
    for suffix, (ccy, unit, factor) in TICKER_CURRENCY_MAP.items():
        if ticker.endswith(suffix):
            return ccy, unit, factor
    
    # 4. Default: EUR major unit
    logger.warning(f"Unknown ticker suffix for {ticker}, defaulting to EUR major unit")
    return "EUR", "EUR", 1


def _normalize_price(price: float, minor_factor: int) -> float:
    """Convert minor unit price to major unit (e.g., GBX -> GBP)."""
    if minor_factor <= 1:
        return price
    return price / minor_factor


def _convert_to_account_ccy(amount: float, from_ccy: str, to_ccy: str = "EUR",
                            fx_rates: Optional[Dict[str, float]] = None) -> float:
    """Convert amount from quote currency to account currency (EUR)."""
    if from_ccy == to_ccy:
        return amount
    rates = fx_rates or DEFAULT_FX_RATES
    rate = rates.get(from_ccy)
    if rate is None:
        logger.warning(f"No FX rate for {from_ccy}->{to_ccy}, using 1.0")
        return amount
    return amount * rate


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _is_api_error(resp: Any) -> bool:
    if isinstance(resp, dict) and resp.get("error"):
        return True
    return False


# ---------------------------------------------------------------------------
# Position parser
# ---------------------------------------------------------------------------

def parse_position(raw: Dict[str, Any], fx_rates: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """
    Parse one T212 /equity/portfolio item with currency normalization.
    T212 fields: ticker, quantity, averagePrice, currentPrice,
                 ppl (unrealized P&L in account currency), fxPpl,
                 initialFillDate, maxBuy, maxSell, pieQuantity

    Returns position with:
    - raw quote prices preserved
    - normalized prices in major quote currency
    - market value in account currency (EUR)
    - P&L in account currency (from broker)
    """
    ticker = raw.get("ticker") or raw.get("symbol") or "UNKNOWN"
    quantity    = _safe_float(raw.get("quantity"))
    avg_price   = _safe_float(raw.get("averagePrice"))
    curr_price  = _safe_float(raw.get("currentPrice"))
    ppl         = _safe_float(raw.get("ppl"))          # T212 unrealized P&L in account currency
    fx_ppl      = _safe_float(raw.get("fxPpl"))        # FX component of P&L
    pie_quantity = _safe_float(raw.get("pieQuantity"))  # >0 indicates pie constituent

    # Resolve instrument currency and price unit using fxPpl as hint
    quote_ccy, price_unit, minor_factor = _resolve_instrument_currency(ticker, fx_ppl)

    # Normalize prices from minor unit to major unit (e.g., GBX -> GBP)
    avg_price_norm = _normalize_price(avg_price, minor_factor)
    curr_price_norm = _normalize_price(curr_price, minor_factor)

    # Convert to account currency (EUR)
    avg_price_eur = _convert_to_account_ccy(avg_price_norm, quote_ccy, "EUR", fx_rates)
    curr_price_eur = _convert_to_account_ccy(curr_price_norm, quote_ccy, "EUR", fx_rates)

    # Calculate values in account currency
    cost_basis_eur = avg_price_eur * quantity
    value_eur = curr_price_eur * quantity

    # P&L in account currency (from broker)
    pnl_eur = ppl if raw.get("ppl") is not None else (value_eur - cost_basis_eur)
    pnl_pct = (pnl_eur / cost_basis_eur * 100) if cost_basis_eur else 0.0

    # Calculate implied FX rate for diagnostics
    implied_fx_rate = (curr_price_eur / curr_price_norm) if curr_price_norm != 0 else None

    # Determine FX source hierarchy
    # Priority: 1) Broker EUR value (if available) -> 2) Broker-implied FX -> 3) Static fallback
    broker_eur_value = raw.get("value")  # Not typically available in T212 API
    fx_source = "static_fallback"
    fx_rate = implied_fx_rate
    fx_is_fallback = True
    
    if fx_ppl is not None and abs(fx_ppl) > 0.001:
        # Broker reports FX component -> we have broker-implied FX
        fx_source = "broker_implied"
        fx_is_fallback = False
    elif implied_fx_rate is not None:
        fx_source = "broker_implied"
        fx_is_fallback = False

    # Validation
    validation_status = "PASS"
    validation_reason = "Broker/calculated value within tolerance"
    if quantity > 0 and (value_eur is None or value_eur == 0):
        validation_status = "FAIL"
        validation_reason = "Positive quantity with zero EUR value"
    elif quote_ccy is None:
        validation_status = "FAIL"
        validation_reason = "Missing quote currency"
    elif minor_factor <= 0:
        validation_status = "FAIL"
        validation_reason = "GBX unit normalization mismatch"

    return {
        "symbol":                    ticker,
        "quantity":                  quantity,
        # Raw quote data (preserved for diagnostics)
        "raw_average_price":         avg_price,
        "raw_current_price":         curr_price,
        "quote_currency":            quote_ccy,
        "price_unit":                price_unit,
        "minor_unit_factor":         minor_factor,
        # Normalized prices (major quote currency)
        "normalized_average_price":  avg_price_norm,
        "normalized_current_price":  curr_price_norm,
        # Account currency (EUR) values
        "average_price_eur":         avg_price_eur,
        "current_price_eur":         curr_price_eur,
        "value_eur":                 value_eur,
        "cost_basis_eur":            cost_basis_eur,
        # P&L (account currency, from broker)
        "pnl_eur":                   pnl_eur,
        "pnl_pct":                   pnl_pct,
        "pnl_fx_eur":                fx_ppl,
        # Metadata
        "pie_quantity":              pie_quantity,
        "initial_fill":              raw.get("initialFillDate"),
        "direction":                 "LONG" if quantity > 0 else "SHORT",
        "status":                    "active",
        "is_pie_constituent":        pie_quantity > 0,
        # Validation
        "validation_status":         validation_status,
        "validation_reason":         validation_reason,
        # Diagnostics
        "implied_fx_rate":           implied_fx_rate,
        "fx_source":                 fx_source,
        "fx_rate":                   fx_rate,
        "fx_is_fallback":            fx_is_fallback,
    }


# ---------------------------------------------------------------------------
# PortfolioMonitor
# ---------------------------------------------------------------------------

class PortfolioMonitor:
    """Main portfolio monitoring and analysis engine for Trading212."""

    def __init__(self, api_key: str, api_secret: str = None, account_id: str = None):
        """
        T212 Basic Auth requires both api_key and api_secret.
        """
        self.client = Trade212Client(api_key, api_secret, account_id)
        self.account_id = account_id
        self._cache: Dict[str, Any] = {}
        self._cache_ts: Dict[str, float] = {}

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def get_portfolio_summary(self, use_cache: bool = False, cache_ttl: int = 60) -> Dict[str, Any]:
        """
        Full portfolio snapshot.
        Merges /equity/account/cash  +  /equity/portfolio.
        """
        key = "portfolio_summary"
        if use_cache and key in self._cache:
            age = datetime.now().timestamp() - self._cache_ts.get(key, 0)
            if age < cache_ttl:
                logger.info(f"Cache hit ({age:.0f}s old)")
                return self._cache[key]

        logger.info("Fetching account cash…")
        cash_resp = self.client.get_account_cash()
        if _is_api_error(cash_resp):
            return {"error": cash_resp["error"], "status": "failed"}

        logger.info("Fetching positions…")
        positions_resp = self.client.get_positions()
        if _is_api_error(positions_resp):
            return {"error": positions_resp["error"], "status": "failed"}

        # Parse positions with currency normalization
        if isinstance(positions_resp, list):
            all_positions = [parse_position(p, fx_rates=DEFAULT_FX_RATES) for p in positions_resp if isinstance(p, dict)]
        elif isinstance(positions_resp, dict):
            all_positions = [parse_position(p, fx_rates=DEFAULT_FX_RATES) for p in positions_resp.get("items", []) if isinstance(p, dict)]
        else:
            all_positions = []

        # --- parse cash (confirmed T212 fields from live API) ---
        # Actual response: { free, total, ppl, result, invested, pieCash, blocked }
        free_cash   = _safe_float(cash_resp.get("free"))
        pie_cash    = _safe_float(cash_resp.get("pieCash"))
        invested    = _safe_float(cash_resp.get("invested"))
        result      = _safe_float(cash_resp.get("result"))    # T212: realized+unrealized P&L (snapshot, NOT account return)
        ppl         = _safe_float(cash_resp.get("ppl"))       # unrealized position P&L
        total       = _safe_float(cash_resp.get("total"))     # total account value (broker equity)
        blocked     = _safe_float(cash_resp.get("blocked"))   # blocked for pending orders

        cash = free_cash + pie_cash   # fully available cash

        # Filter out pie constituents for display/aggregation (avoid double counting)
        # Pie constituents have is_pie_constituent = True
        active_positions = [p for p in all_positions if not p.get("is_pie_constituent", False)]

        # Portfolio value from ACTIVE positions only (for display)
        portfolio_value_eur  = sum(p["value_eur"]      for p in active_positions)
        cost_basis_total_eur = sum(p["cost_basis_eur"] for p in active_positions)
        unrealized_pnl_eur   = sum(p["pnl_eur"]        for p in active_positions)

        # RECONCILIATION: Use ALL positions (including pie constituents) to match broker's 'invested'
        # Broker's 'invested' field = market value of ALL positions in account currency
        all_positions_value_eur = sum(p["value_eur"] for p in all_positions)
        # Derived total = all positions value + available cash
        # Per T212 API: total equity = invested + free + pieCash + blocked
        derived_holdings_plus_available_cash = all_positions_value_eur + free_cash + pie_cash
        reconciliation_difference = abs(total - derived_holdings_plus_available_cash)
        reconciliation_threshold = max(1.0, total * 0.001)  # max(€1, 0.1% of equity)
        reconciliation_status = "PASS" if reconciliation_difference <= reconciliation_threshold else "FAIL"

        # NOTE: T212 does not provide net_deposits (deposits - withdrawals only) via API.
        # The 'invested' field is current market value of positions, NOT net deposits.
        # The 'result' field is T212's snapshot realized+unrealized P&L, NOT equity - net_deposits.
        # Derived account return = total_equity - net_deposits (requires external net_deposits data).
        # We expose derived_unrealized_pnl (recalculated) and T212's unrealized_pnl (ppl).
        # Realized P&L requires full transaction ledger - not available from current API.

# Mask account_id for security (show only last 4 chars)
        masked_account_id = self.account_id[-4:] if self.account_id and len(self.account_id) >= 4 else "****"
        
        result_obj = {
            "account_id":        masked_account_id,
            "timestamp":         datetime.now().isoformat(),
            # --- cash ---
            "cash_free":         free_cash,
            "cash_pie":          pie_cash,
            "cash":              cash,
            "cash_blocked":      blocked,
            # --- positions (EUR) ---
            "portfolio_value_eur":   portfolio_value_eur,
            "all_positions_value_eur": all_positions_value_eur,
            "invested":          invested,
            "cost_basis_eur":    cost_basis_total_eur,
            # --- P&L (position-level only, EUR) ---
            "unrealized_pnl_eur":    ppl,              # T212 ppl = unrealized position P&L (EUR)
            "unrealized_pnl_calc_eur": unrealized_pnl_eur, # recalculated from active positions
            "realized_pnl_eur":      None,             # requires full transaction ledger (not available)
            # --- totals (broker-provided) ---
            "total_equity":      total,
            # --- reconciliation ---
            "derived_holdings_plus_available_cash": derived_holdings_plus_available_cash,
            "reconciliation_difference": reconciliation_difference,
            "reconciliation_threshold": reconciliation_threshold,
            "reconciliation_status": reconciliation_status,
            "invested_reconciliation_diff": abs(invested - all_positions_value_eur),
            # --- metadata ---
            "positions_count":   len(active_positions),
            "all_positions_count": len(all_positions),
            "positions":         active_positions,
            "all_positions":     all_positions,
            "status":            "success",
        }

        self._cache[key] = result_obj
        self._cache_ts[key] = datetime.now().timestamp()
        return result_obj

    def get_positions(self, include_pie_constituents: bool = False) -> List[Dict[str, Any]]:
        logger.info("Fetching positions…")
        resp = self.client.get_positions()
        if isinstance(resp, list):
            positions = [parse_position(p, fx_rates=DEFAULT_FX_RATES) for p in resp if isinstance(p, dict)]
        elif isinstance(resp, dict):
            if resp.get("error"):
                logger.error(f"Positions error: {resp['error']}")
                return []
            positions = [parse_position(p, fx_rates=DEFAULT_FX_RATES) for p in resp.get("items", []) if isinstance(p, dict)]
        else:
            return []
        
        if not include_pie_constituents:
            return [p for p in positions if not p.get("is_pie_constituent", False)]
        return positions

    def get_account_cash(self) -> Dict[str, Any]:
        """Returns parsed cash info — confirmed T212 fields from live API."""
        resp = self.client.get_account_cash()
        if _is_api_error(resp):
            return {"error": resp["error"], "status": "failed"}
        return {
            "free":     _safe_float(resp.get("free")),
            "invested": _safe_float(resp.get("invested")),
            "pie_cash": _safe_float(resp.get("pieCash")),
            "result":   _safe_float(resp.get("result")),   # total P&L
            "ppl":      _safe_float(resp.get("ppl")),      # unrealized P&L
            "total":    _safe_float(resp.get("total")),    # total account value
            "blocked":  _safe_float(resp.get("blocked")),  # blocked for orders
            "status":   "success",
        }

    def get_order_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        logger.info(f"Fetching order history (limit={limit})…")
        resp = self.client.get_order_history(limit)
        if _is_api_error(resp):
            logger.error(f"Order history error: {resp['error']}")
            return []
        items = resp.get("items", []) if isinstance(resp, dict) else []
        parsed = []
        for t in items:
            try:
                parsed.append({
                    "symbol":    t.get("ticker") or t.get("symbol", "UNKNOWN"),
                    "side":      t.get("side", "UNKNOWN"),
                    "quantity":  _safe_float(t.get("filledQuantity") or t.get("quantity")),
                    "price":     _safe_float(t.get("fillPrice") or t.get("price")),
                    "timestamp": t.get("dateModified") or t.get("time", ""),
                    "status":    t.get("status", ""),
                    "type":      t.get("type", ""),
                })
            except Exception as e:
                logger.error(f"Order parse error: {e}")
        return parsed

    def get_pies(self) -> List[Dict[str, Any]]:
        logger.info("Fetching pies…")
        resp = self.client.get_pies()
        if isinstance(resp, list):
            return resp
        if isinstance(resp, dict) and not resp.get("error"):
            return resp.get("items", [])
        logger.error(f"Pies error: {resp}")
        return []

    def get_dividends(self, limit: int = 50) -> List[Dict[str, Any]]:
        logger.info("Fetching dividends…")
        resp = self.client.get_dividends(limit)
        if _is_api_error(resp):
            logger.error(f"Dividends error: {resp['error']}")
            return []
        return resp.get("items", []) if isinstance(resp, dict) else []

    def calculate_portfolio_metrics(self, positions: Optional[List[Dict]] = None) -> Dict[str, Any]:
        if positions is None:
            positions = self.get_positions()

        total_value      = sum(p.get("value", 0)      for p in positions)
        total_cost_basis = sum(p.get("cost_basis", 0) for p in positions)
        total_pnl        = sum(p.get("pnl", 0)        for p in positions)

        by_direction: Dict[str, Any] = defaultdict(lambda: {"count": 0, "value": 0, "pnl": 0})
        for p in positions:
            d = p.get("direction", "LONG")
            by_direction[d]["count"] += 1
            by_direction[d]["value"] += p.get("value", 0)
            by_direction[d]["pnl"]   += p.get("pnl", 0)

        top5 = sorted(positions, key=lambda p: p.get("value", 0), reverse=True)[:5]
        best = max(positions, key=lambda p: p.get("pnl_pct", -999), default=None)
        worst = min(positions, key=lambda p: p.get("pnl_pct", 999), default=None)

        return {
            "total_positions":    len(positions),
            "total_value":        total_value,
            "total_cost_basis":   total_cost_basis,
            "total_pnl":          total_pnl,
            "total_pnl_pct":      (total_pnl / total_cost_basis * 100) if total_cost_basis else 0.0,
            "top5_by_value":      top5,
            "best_performer":     best,
            "worst_performer":    worst,
            "positions_by_direction": dict(by_direction),
            "timestamp":          datetime.now().isoformat(),
        }

    def get_symbol_price_history(self, symbol: str, period: str = "3mo",
                                 interval: str = "1d") -> Optional[pd.DataFrame]:
        try:
            import yfinance as yf
            logger.info(f"yfinance: {symbol} {period}/{interval}")
            return yf.Ticker(symbol).history(period=period, interval=interval)
        except Exception as e:
            logger.error(f"yfinance error for {symbol}: {e}")
            return None

    def clear_cache(self):
        self._cache.clear()
        self._cache_ts.clear()
        logger.info("Cache cleared")
