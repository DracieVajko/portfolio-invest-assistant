"""
Trading212 Portfolio Analysis Module

Správne T212 API v0 field names:
  /equity/account/cash  → free, invested, pieCash, result, total
  /equity/portfolio     → ticker, quantity, averagePrice, currentPrice,
                          ppl (P&L), fxPpl, initialFillDate, pieQuantity
"""

import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime
from collections import defaultdict
import logging

from .auth import Trade212Client

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(name)s] %(message)s'
)
logger = logging.getLogger('Trading212Portfolio')


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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

def parse_position(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse one T212 /equity/portfolio item.
    T212 fields: ticker, quantity, averagePrice, currentPrice,
                 ppl (unrealized P&L in account currency), fxPpl,
                 initialFillDate, maxBuy, maxSell, pieQuantity
    """
    quantity    = _safe_float(raw.get("quantity"))
    avg_price   = _safe_float(raw.get("averagePrice"))
    curr_price  = _safe_float(raw.get("currentPrice"))
    ppl         = _safe_float(raw.get("ppl"))          # T212 unrealized P&L field

    cost_basis  = avg_price  * quantity
    value       = curr_price * quantity
    # Prefer T212's own ppl if available; recalc only as fallback
    pnl         = ppl if raw.get("ppl") is not None else (value - cost_basis)
    pnl_pct     = (pnl / cost_basis * 100) if cost_basis else 0.0

    return {
        "symbol":          raw.get("ticker") or raw.get("symbol") or "UNKNOWN",
        "quantity":        quantity,
        "average_price":   avg_price,
        "current_price":   curr_price,
        "value":           value,
        "cost_basis":      cost_basis,
        "pnl":             pnl,
        "pnl_pct":         pnl_pct,
        "pnl_fx":          _safe_float(raw.get("fxPpl")),
        "pie_quantity":    _safe_float(raw.get("pieQuantity")),
        "initial_fill":    raw.get("initialFillDate"),
        "direction":       "LONG" if quantity > 0 else "SHORT",
        "status":          "active",
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

        # --- parse cash (confirmed T212 fields from live API) ---
        # Actual response: { free, total, ppl, result, invested, pieCash, blocked }
        free_cash   = _safe_float(cash_resp.get("free"))
        pie_cash    = _safe_float(cash_resp.get("pieCash"))
        invested    = _safe_float(cash_resp.get("invested"))
        result      = _safe_float(cash_resp.get("result"))    # realized+unrealized P&L
        ppl         = _safe_float(cash_resp.get("ppl"))       # unrealized position P&L
        total       = _safe_float(cash_resp.get("total"))     # total account value
        blocked     = _safe_float(cash_resp.get("blocked"))   # blocked for pending orders

        cash = free_cash + pie_cash   # fully available cash

        # --- parse positions ---
        raw_list = positions_resp if isinstance(positions_resp, list) else []
        positions = [parse_position(p) for p in raw_list if isinstance(p, dict)]

        portfolio_value  = sum(p["value"]      for p in positions)
        cost_basis_total = sum(p["cost_basis"] for p in positions)
        unrealized_pnl   = sum(p["pnl"]        for p in positions)

        result_obj = {
            "account_id":        self.account_id,
            "timestamp":         datetime.now().isoformat(),
            # --- cash ---
            "cash_free":         free_cash,
            "cash_pie":          pie_cash,
            "cash":              cash,
            "cash_blocked":      blocked,
            # --- positions ---
            "portfolio_value":   portfolio_value,
            "invested":          invested,
            "cost_basis":        cost_basis_total,
            # --- P&L ---
            "unrealized_pnl":    ppl,              # T212 ppl = unrealized position P&L
            "unrealized_pnl_calc": unrealized_pnl, # recalculated from positions
            "realized_pnl":      result - ppl,     # approximation
            "total_pnl":         result,
            "total_pnl_pct":     (result / invested * 100) if invested > 0 else 0.0,
            # --- totals ---
            "total_equity":      total,
            "positions_count":   len(positions),
            "positions":         positions,
            "status":            "success",
        }

        self._cache[key] = result_obj
        self._cache_ts[key] = datetime.now().timestamp()
        return result_obj

    def get_positions(self) -> List[Dict[str, Any]]:
        logger.info("Fetching positions…")
        resp = self.client.get_positions()
        if isinstance(resp, list):
            return [parse_position(p) for p in resp if isinstance(p, dict)]
        if isinstance(resp, dict):
            if resp.get("error"):
                logger.error(f"Positions error: {resp['error']}")
                return []
            return [parse_position(p) for p in resp.get("items", []) if isinstance(p, dict)]
        return []

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
