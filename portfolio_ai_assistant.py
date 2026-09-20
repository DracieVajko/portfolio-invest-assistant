#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Portfolio AI Assistant - reliable investment monitoring (V4 hardening).

Deterministic, read-only portfolio analysis pipeline:
- Trading 212 / Revolut positions (read-only broker sync)
- Yahoo Finance market data (price, moving averages, RSI, trend)
- DuckDuckGo news with keyword sentiment (per ticker, global, discovery)
- Data-quality scoring and broker-vs-market mapping gates (v4_hardening)
- Canonical actions with hard gates
  (ADD / HOLD / WAIT / REVIEW / REDUCE / NO_DATA / REVIEW_MAPPING)
- Optional AI commentary through a configurable backend chain
  (LM Studio / Ollama / OpenRouter / Gemini / Mistral).
  AI only summarizes validated facts; it never mutates scores or actions.

Outputs are published atomically (see publish_run):
- reports/latest/portfolio_report.md / .json
- reports/latest/data_quality.md
- reports/latest/run_manifest.json
- reports/archive/YYYY-MM-DD/<run_id>/

Exit codes: 0 success, 1 fatal, 2 config error, 3 already running,
4 partial, 5 broker sync failed (see v4_hardening.RunExit).
"""

# --------------------------------------------------------------------
# Imports
# --------------------------------------------------------------------

import argparse
import base64
import json
import logging
import math
import os
import queue
import random
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import numpy as np
import requests
import yfinance as yf

import v4_hardening as V4

# --------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

latest_log = LOG_DIR / "latest_run.log"

logger = logging.getLogger("PortfolioAI")
logger.setLevel(logging.INFO)
logger.handlers.clear()

formatter = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

latest_handler = logging.FileHandler(latest_log, encoding="utf-8")
latest_handler.setFormatter(formatter)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)

logger.addHandler(latest_handler)
logger.addHandler(console_handler)

logger.info("=" * 70)
logger.info("Portfolio AI Assistant started")
logger.info("Working directory: %s", Path.cwd())
logger.info("Python version: %s", sys.version.split()[0])
logger.info("=" * 70)

yf.set_tz_cache_location("data/cache")  # stable tz cache location for yfinance

# DuckDuckGo search - single import attempt
try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None


# ============================================================================
# CONFIGURATION & VALIDATION
# ============================================================================

def migrate_legacy_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert legacy config (holdings array) to new schema (assets objects).
    Returns migrated config without modifying original.
    """
    if "assets" in config:
        # Already using new schema
        return config
    
    # Convert old "holdings" array to new "assets" structure
    migrated = config.copy()
    migrated["assets"] = []
    
    holdings = config.get("holdings", [])
    if isinstance(holdings, list):
        # Simple array of symbols
        for symbol in holdings:
            migrated["assets"].append({
                "broker_symbol": symbol,
                "yahoo_symbol": symbol,
                "name": symbol,
                "group": "PORTFOLIO",
                "search_query": symbol
            })
    elif isinstance(holdings, dict):
        # Dictionary of holdings
        for symbol, data in holdings.items():
            migrated["assets"].append({
                "broker_symbol": symbol,
                "yahoo_symbol": symbol,
                "name": symbol,
                "group": "PORTFOLIO",
                "quantity": data.get("quantity"),
                "search_query": symbol
            })
    
    return migrated


# --------------------------------------------------------------------
# Data Collection
# --------------------------------------------------------------------

def collect_asset_data(asset: Dict[str, Any],
                       settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Retrieve price, technical indicators, fundamentals and news for one asset.

    Returns a dict with keys: price (dict: current price, changes, MAs, RSI),
    market_cap, volume, news, info, technicals. Missing values are None.
    """
    settings = settings or {}
    symbol = asset.get("yahoo_symbol") or asset.get("broker_symbol")
    if not symbol:
        return {}

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        hist_period = settings.get("days_price_history", "3mo")
        hist = ticker.history(period=hist_period, interval="1d")

        price = info.get('regularMarketPrice')
        market_cap = info.get('marketCap')
        volume = info.get('volume')

        technicals: Dict[str, Any] = {}
        if not hist.empty:
            close = hist['Close']
            technicals['change_1d_pct'] = ((close.iloc[-1] / close.iloc[-2]) - 1) * 100 if len(close) > 1 else None
            technicals['change_5d_pct'] = ((close.iloc[-1] / close.iloc[-6]) - 1) * 100 if len(close) > 5 else None
            technicals['ma20'] = close.rolling(20).mean().iloc[-1] if len(close) >= 20 else None
            technicals['ma50'] = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
            technicals['ma200'] = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None
            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            technicals['rsi'] = (100 - (100 / (1 + rs))).iloc[-1] if len(close) >= 14 else None
            if technicals.get('ma20') and technicals.get('ma50'):
                technicals['trend_hint'] = "UP" if technicals['ma20'] > technicals['ma50'] else "DOWN"
            else:
                technicals['trend_hint'] = "NEUTRAL"
            technicals['currency'] = info.get('currency', 'USD')
            try:
                technicals['history_bars'] = int(len(close))
                technicals['last_bar'] = close.index[-1].isoformat()
            except Exception:
                technicals['history_bars'] = None
                technicals['last_bar'] = None

        price_dict = {
            'price': price,
            'change_1d_pct': technicals.get('change_1d_pct'),
            'change_5d_pct': technicals.get('change_5d_pct'),
            'currency': technicals.get('currency', 'USD'),
            'trend_hint': technicals.get('trend_hint'),
            'ma20': technicals.get('ma20'),
            'ma50': technicals.get('ma50'),
            'ma200': technicals.get('ma200'),
            'rsi': technicals.get('rsi'),
            'history_bars': technicals.get('history_bars'),
            'last_bar': technicals.get('last_bar'),
        }

        aliases = (asset.get("aliases") or []) + [asset.get("name", ""), symbol]
        ticker_news = collect_ticker_news(
            symbol,
            aliases=aliases,
            max_items=int(settings.get("max_news_per_asset", 5)),
            max_age_hours=int(settings.get("max_news_age_hours", 48)),
        )

        return {
            'price': price_dict,
            'market_cap': market_cap,
            'volume': volume,
            'info': info,
            'technicals': technicals,
            'news': ticker_news,
        }
    except Exception as e:
        logger.warning(f"collect_asset_data failed for {symbol}: {e}")
        return {}




# ============================================================================
# ENVIRONMENT & BROKER INTEGRATIONS
# ============================================================================

def env_bool(value: Any, default: bool = False) -> bool:
    """Parse boolean-ish values from env/config."""
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def mask_secret(value: Optional[str]) -> str:
    """Mask API key for diagnostics without leaking it."""
    if not value:
        return "missing"
    value = str(value)
    if len(value) <= 8:
        return "***"
    return value[:4] + "..." + value[-4:]


def load_environment_variables():
    """Load environment variables from api.env without requiring python-dotenv."""
    env_path = Path("api.env")

    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            # api.env should override stale shell variables for this project.
            if key:
                os.environ[key] = value
    # NOTE: python-dotenv is intentionally not used; api.env is authoritative.
    # (A legacy `load_dotenv` fallback was removed: the name was never imported.)

    return {
        "trading212_enabled": env_bool(os.getenv("TRADING212_ENABLED"), False),
        "trading212_api_base": os.getenv("TRADING212_API_BASE", "https://live.trading212.com").rstrip("/"),
        "trading212_api_key":    os.getenv("TRADING212_API_KEY"),
        "trading212_api_secret": os.getenv("TRADING212_API_SECRET"),
        "revolut_file": os.getenv("REVOLUT_MANUAL_RESEARCH_FILE", "data/revolut_research_notes.md"),
    }


def normalize_t212_ticker(raw_ticker: Optional[str]) -> str:
    """
    T212 internal tickers have an exchange/type suffix, e.g.:
      WDC_US_EQ → WDC
      AAPL_US_EQ → AAPL
      BTCUSD → BTCUSD   (crypto tickers usually have no suffix)
      000660_KS_EQ → 000660.KS  (Korean exchange — T212 uses underscore, Yahoo uses dot)
    Strips the trailing _XX_EQ / _XXX_EQ pattern and known suffixes.
    """
    if not raw_ticker:
        return ""
    t = raw_ticker.strip().upper()

    # Common T212 suffix pattern: SYMBOL_EXCHANGE_EQ
    if t.endswith("_EQ"):
        parts = t.split("_")
        if len(parts) >= 3:
            symbol = parts[0]
            exchange = parts[1]
            # Korean / some Asian exchanges: T212 uses SYMBOL_KS_EQ, Yahoo uses SYMBOL.KS
            if exchange in ("KS", "KQ", "L", "T", "HK", "SS", "SZ"):
                return f"{symbol}.{exchange}"
            return symbol
        elif len(parts) == 2:
            return parts[0]

    # Crypto pairs and anything without a recognizable suffix: return as-is
    return t


def fetch_trading212_positions(api_key: str, api_secret: str = None, api_base: str = "https://live.trading212.com") -> Dict[str, Any]:
    """
    Fetch positions from Trading 212 API in read-only mode.

    Auth: T212 API v0 (current) uses HTTP Basic Auth.
    Header: Authorization: Basic base64(api_key:api_secret)
    Both api_key AND api_secret are required — they form a matched pair.
    Endpoint: GET /api/v0/equity/portfolio  → returns list of positions.

    This function is intentionally read-only. It never places orders.
    """
    status = {
        "enabled": bool(api_key),
        "ok": False,
        "base_url": api_base,
        "endpoint_used": None,
        "positions_count": 0,
        "error": None,
        "api_key": mask_secret(api_key),
        "transient": False,   # True when a retry may help (timeout/connection/429/5xx)
        "retry_after": None,  # seconds from Retry-After header on 429, if present
    }

    if not api_key:
        status["error"] = "Missing TRADING212_API_KEY"
        return {"positions": [], "status": status}

    base = (api_base or "https://live.trading212.com").rstrip("/")
    # Ensure we strip any /api/v0 suffix the user may have added
    if base.endswith("/api/v0"):
        base = base[: -len("/api/v0")]

    # T212 API v0 (current): HTTP Basic Auth = base64(api_key:api_secret)
    _secret = api_secret or ""
    _creds = base64.b64encode(f"{api_key}:{_secret}".encode()).decode()
    headers = {"Authorization": f"Basic {_creds}"}

    # Official endpoint order – /equity/portfolio is the correct one for positions
    endpoint_candidates = [
        "/api/v0/equity/portfolio",
        "/api/v0/equity/account/portfolio",
        "/api/v0/equity/positions",
    ]

    last_error = None
    for ep in endpoint_candidates:
        url = base + ep
        try:
            response = requests.get(url, headers=headers, timeout=20)

            if response.status_code == 401:
                status["error"] = "401 Unauthorized – skontroluj KEY+SECRET pár (musia byť z rovnakého generate v T212 app)"
                return {"positions": [], "status": status}

            if response.status_code == 403:
                status["error"] = "403 Forbidden – check API key permissions in Trading212 settings"
                return {"positions": [], "status": status}

            if response.status_code == 404:
                last_error = f"404 not found: {ep}"
                continue

            if response.status_code == 429:
                status["error"] = "429 Rate limited – try again later"
                status["transient"] = True
                try:
                    status["retry_after"] = float(response.headers.get("Retry-After", 0)) or None
                except (TypeError, ValueError):
                    status["retry_after"] = None
                return {"positions": [], "status": status}

            if response.status_code in (500, 502, 503, 504):
                last_error = f"{response.status_code} server error: {ep}"
                status["transient"] = True
                continue

            response.raise_for_status()

            data = response.json()

            # /equity/portfolio returns a list directly; others may wrap in dict
            if isinstance(data, list):
                raw_positions = data
            elif isinstance(data, dict):
                raw_positions = (
                    data.get("positions")
                    or data.get("items")
                    or data.get("portfolio")
                    or data.get("data")
                    or []
                )
            else:
                raw_positions = []

            positions = []
            for item in raw_positions:
                if not isinstance(item, dict):
                    continue
                raw_ticker = (
                    item.get("ticker")
                    or item.get("symbol")
                    or item.get("instrumentCode")
                    or item.get("shortName")
                )
                qty    = item.get("quantity")
                avg_p  = item.get("averagePrice") or item.get("average_price")
                curr_p = item.get("currentPrice") or item.get("current_price")
                pnl    = item.get("ppl") or item.get("pnl") or item.get("profitLoss")

                pnl_pct = None
                try:
                    if avg_p and qty and pnl is not None:
                        cost_basis = float(avg_p) * float(qty)
                        if cost_basis:
                            pnl_pct = float(pnl) / cost_basis * 100
                except (TypeError, ValueError):
                    pnl_pct = None

                positions.append({
                    "broker": "Trading212",
                    "broker_symbol": raw_ticker,
                    "broker_symbol_clean": normalize_t212_ticker(raw_ticker),
                    "quantity": qty,
                    "average_price": avg_p,
                    "current_price": curr_p,
                    "market_value": item.get("value") or item.get("marketValue") or item.get("market_value"),
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "currency": item.get("currencyCode") or item.get("currency"),
                })

            status.update({
                "ok": True,
                "endpoint_used": ep,
                "positions_count": len(positions),
                "error": None,
            })
            return {"positions": positions, "status": status}

        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, (ConnectionError, TimeoutError)):
                status["transient"] = True

    status["error"] = last_error or "Trading 212 API request failed"
    if status["transient"] is not True:
        # requests exceptions are wrapped; classify by name as well.
        status["transient"] = any(
            token in (last_error or "")
            for token in ("ConnectTimeout", "ReadTimeout", "ConnectionError",
                          "Timeout", "Temporary failure", "503", "502", "504", "429")
        )
    return {"positions": [], "status": status}


def fetch_trading212_with_retry(api_key: str, api_secret: str = None,
                                api_base: str = "https://live.trading212.com",
                                max_attempts: int = 3) -> Dict[str, Any]:
    """Fetch T212 positions, retrying transient failures only.

    Never retries 401/403/404/config errors. Honors Retry-After on 429.
    Read-only: only GET requests.
    """
    last = {"positions": [], "status": {"ok": False, "error": "no attempt", "transient": False}}
    for attempt in range(max_attempts):
        last = fetch_trading212_positions(api_key, api_secret=api_secret, api_base=api_base)
        status = last.get("status", {})
        if status.get("ok") or not status.get("transient"):
            return last
        if attempt < max_attempts - 1:
            wait = status.get("retry_after") or (2.0 * (2 ** attempt))
            wait = min(float(wait), 60.0) + random.uniform(0, 0.5)
            logger.warning("T212 transient failure (attempt %d/%d), retrying in %.1fs: %s",
                           attempt + 1, max_attempts, wait,
                           V4.redact_secrets(status.get("error")))
            time.sleep(wait)
    return last


def fetch_trading212_cash(api_key: str, api_secret: str = None,
                          api_base: str = "https://live.trading212.com",
                          max_attempts: int = 3) -> Dict[str, Any]:
    """Read-only EUR account totals (total/result/free). Transient-only retries."""
    base = (api_base or "https://live.trading212.com").rstrip("/")
    creds = base64.b64encode(f"{api_key}:{api_secret or ''}".encode()).decode()
    headers = {"Authorization": f"Basic {creds}"}
    last_error = None
    for attempt in range(max_attempts):
        try:
            response = requests.get(f"{base}/api/v0/equity/account/cash",
                                    headers=headers, timeout=10)
            if response.status_code == 200:
                cash = response.json()
                return {"ok": True, "total": float(cash.get("total", 0)),
                        "result": float(cash.get("result", 0)),
                        "free": float(cash.get("free", 0)), "error": None}
            if response.status_code in (401, 403, 404):
                return {"ok": False, "error": f"cash API HTTP {response.status_code}"}
            last_error = f"cash API HTTP {response.status_code}"
            wait = V4.retry_after_seconds(response.headers, 2.0 * (2 ** attempt))
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            transient = isinstance(exc, (ConnectionError, TimeoutError)) or \
                "Timeout" in type(exc).__name__ or "Connection" in type(exc).__name__
            if not transient:
                return {"ok": False, "error": V4.redact_secrets(last_error)}
            wait = 2.0 * (2 ** attempt)
        if attempt < max_attempts - 1:
            time.sleep(min(wait, 60.0) + _random.uniform(0, 0.5))
    return {"ok": False, "error": V4.redact_secrets(last_error or "cash API failed")}


def load_revolut_data(file_path: str):
    """Parse Revolut positions from manual research file."""
    if not file_path or not Path(file_path).exists():
        return []
    
    positions = []
    file_path = Path(file_path)
    
    try:
        if file_path.suffix.lower() == ".json":
            data = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for item in data:
                    positions.append({
                        "broker": "Revolut",
                        "broker_symbol": item.get("ticker") or item.get("symbol"),
                        "quantity": item.get("quantity"),
                        "buy_price": item.get("buy_price"),
                        "notes": item.get("notes"),
                    })
        elif file_path.suffix.lower() in [".csv", ".txt"]:
            lines = file_path.read_text(encoding="utf-8").strip().split("\n")
            for line in lines[1:]:
                if not line.strip():
                    continue
                fields = [f.strip() for f in line.split(",")]
                if len(fields) >= 2:
                    try:
                        positions.append({
                            "broker": "Revolut",
                            "broker_symbol": fields[0],
                            "quantity": float(fields[1]) if len(fields) > 1 else None,
                            "buy_price": float(fields[2]) if len(fields) > 2 else None,
                            "notes": fields[3] if len(fields) > 3 else "",
                        })
                    except ValueError:
                        continue
    except Exception:
        pass
    return positions


def auto_discover_unmatched_positions(assets, broker_positions, settings, symbol_aliases=None):
    """
    Pre T212 pozície ktoré sa NEPODARILO spárovať so žiadnym assetom v configu
    (napr. nový nákup, ktorý si ešte nepridal do portfolio_config.json),
    vygeneruj dočasný minimálny asset záznam aby sa pre ne stiahli ceny aj novinky.

    Tieto auto-discovered assety sa nezapisujú späť do portfolio_config.json —
    sú iba pre tento jeden beh reportu.
    """
    if not settings.get("auto_discover_t212_positions", True):
        return assets

    symbol_aliases = symbol_aliases or {}
    
    matched_clean = set()
    for asset in assets:
        if asset.get("trading212"):
            t = asset["trading212"]
            clean = t.get("broker_symbol_clean") or t.get("broker_symbol", "")
            matched_clean.add(clean.upper())

    discovered = []
    seen_raw = set()
    for pos in broker_positions.get("trading212", []):
        raw = (pos.get("broker_symbol") or "").upper()
        clean = (pos.get("broker_symbol_clean") or raw).upper()
        if not raw or raw in seen_raw or clean in matched_clean:
            continue
        seen_raw.add(raw)

        qty = pos.get("quantity") or 0
        # Voliteľný filter: ignoruj prachové/zanedbateľné pozície (default off)
        min_value = float(settings.get("auto_discover_min_value", 0))
        val = pos.get("market_value") or 0
        if min_value and val and val < min_value:
            continue

        discovered.append({
            "broker_symbol":      clean,
            "yahoo_symbol":       symbol_aliases.get(clean, clean),
            "tradingview_symbol": None,
            "name":               clean,
            "group":              "T212_AUTO_DISCOVERED",
            "quantity":           qty,
            "average_price":      pos.get("average_price"),
            "target_weight_pct":  None,
            "search_query":       f"{clean} stock news",
            "aliases":            [clean],
            "enabled":            True,
            "trading212":         pos,
            "auto_discovered":    True,
            "mapping":            V4.MappingAudit(
                broker_symbol=pos.get("broker_symbol") or clean,
                normalized_broker_symbol=clean.upper(),
                configured_asset_id="",
                configured_ticker="",
                external_market_ticker=symbol_aliases.get(clean, clean),
                mapping_method=V4.METHOD_AUTO,
                mapping_confidence=V4.CONF_LOW,
                mapping_status=V4.STATUS_UNRESOLVED,
                mapping_reason=("Broker position has no configured asset; temporary "
                                "auto-discovered entry, never actionable without review."),
                broker_currency=pos.get("currencyCode") or pos.get("currency"),
                market_currency=None,
                currency_comparison_status=V4.CCY_UNKNOWN,
                price_comparison_status=V4.PRICE_UNAVAILABLE,
                price_difference_pct=None,
                is_actionable=False,
            ).to_dict(),
        })

    if discovered:
        print(f"[BROKER] Auto-discovered {len(discovered)} T212 position(s) not in config: "
              f"{', '.join(d['broker_symbol'] for d in discovered)}")

    return assets + discovered


_MAPPING_FIELDS = (
    "broker_symbol", "normalized_broker_symbol", "configured_asset_id",
    "configured_ticker", "external_market_ticker", "mapping_method",
    "mapping_confidence", "mapping_status", "mapping_reason",
    "broker_currency", "market_currency", "currency_comparison_status",
    "price_comparison_status", "price_difference_pct", "is_actionable",
)


def _mapping_of(asset: Dict[str, Any]) -> "V4.MappingAudit":
    """Rebuild the MappingAudit attached during merge (defensive copy)."""
    raw = asset.get("mapping")
    if isinstance(raw, dict):
        try:
            return V4.MappingAudit(**{k: raw.get(k) for k in _MAPPING_FIELDS})
        except Exception:
            pass
    return V4.new_audit(broker_symbol=asset.get("broker_symbol") or "")


def merge_broker_data(assets, broker_positions, symbol_aliases=None):
    """Merge broker position data with portfolio assets.

    Every asset gets a machine-readable ``mapping`` audit record (see
    v4_hardening.MappingAudit). Assets without a broker position are watchlist
    entries (NO_BROKER_POSITION, market data still decides actionability).
    Prefix matches are WEAK by design and never actionable on their own.
    """
    symbol_aliases = symbol_aliases or {}
    alias_keys = {str(k).upper() for k in symbol_aliases.keys()}
    enhanced_assets = []

    # Build lookup maps for T212 positions (track collisions for ambiguity gate)
    t212_by_clean = {}
    t212_by_raw = {}
    clean_to_raws: Dict[str, set] = {}
    for pos in broker_positions.get("trading212", []):
        clean = (pos.get("broker_symbol_clean") or "")
        raw = (pos.get("broker_symbol") or "")
        if clean:
            t212_by_clean[clean.upper()] = pos
            clean_to_raws.setdefault(clean.upper(), set()).add(raw.upper())
        if raw:
            t212_by_raw[raw.upper()] = pos

    for asset in assets:
        broker_symbol = (asset.get("broker_symbol", "") or "").upper()
        yahoo_symbol = asset.get("yahoo_symbol") or broker_symbol
        enhanced = asset.copy()
        audit = V4.new_audit(
            broker_symbol=asset.get("broker_symbol", "") or "",
            normalized=broker_symbol,
            asset_id=asset.get("broker_symbol", "") or "",
            asset_ticker=asset.get("broker_symbol", "") or "",
            market_ticker=yahoo_symbol,
        )

        matched_pos = None
        method = V4.METHOD_UNMATCHED
        # Try exact match on normalized ticker first
        if broker_symbol in t212_by_clean:
            matched_pos = t212_by_clean[broker_symbol]
            method = V4.METHOD_EXACT_SYMBOL
        elif broker_symbol in t212_by_raw:
            matched_pos = t212_by_raw[broker_symbol]
            method = V4.METHOD_NORMALIZED
        else:
            # Try prefix match for edge cases (WEAK: never validated silently)
            for raw_sym, pos in t212_by_raw.items():
                if raw_sym.startswith(broker_symbol + "_"):
                    matched_pos = pos
                    method = V4.METHOD_PREFIX
                    break

        if matched_pos is not None:
            enhanced["trading212"] = matched_pos
            pos = matched_pos
            audit.broker_symbol = pos.get("broker_symbol") or audit.broker_symbol
            audit.normalized_broker_symbol = (
                pos.get("broker_symbol_clean") or pos.get("broker_symbol") or broker_symbol).upper()
            raws = clean_to_raws.get(broker_symbol, set())
            if method == V4.METHOD_PREFIX:
                audit.mapping_method = V4.METHOD_PREFIX
                audit.mapping_confidence = V4.CONF_LOW
                audit.mapping_status = V4.STATUS_SUSPECT
                audit.mapping_reason = (
                    f"Weak prefix match: config '{broker_symbol}' matched broker "
                    f"'{pos.get('broker_symbol')}'; explicit alias required for validation.")
                audit.is_actionable = False
            elif len(raws) > 1:
                # Ambiguous: several distinct broker instruments share one normalized symbol.
                audit.mapping_method = method
                audit.mapping_confidence = V4.CONF_NONE
                audit.mapping_status = V4.STATUS_SUSPECT
                audit.mapping_reason = (
                    f"Ambiguous mapping: {len(raws)} distinct broker instruments "
                    f"({', '.join(sorted(raws))}) share normalized symbol '{broker_symbol}'; "
                    f"blocked until disambiguated by explicit alias.")
                audit.is_actionable = False
            else:
                if broker_symbol in alias_keys:
                    audit.mapping_method = V4.METHOD_ALIAS
                    audit.mapping_confidence = V4.CONF_MEDIUM
                    audit.mapping_reason = (
                        f"Explicit alias maps '{broker_symbol}' to Yahoo '{yahoo_symbol}'.")
                else:
                    audit.mapping_method = method
                    audit.mapping_confidence = V4.CONF_HIGH
                    audit.mapping_reason = f"Exact symbol match on '{broker_symbol}'."
                audit.mapping_status = V4.STATUS_VALID
                audit.is_actionable = True
        else:
            audit.mapping_method = V4.METHOD_NO_POSITION
            audit.mapping_confidence = V4.CONF_NONE
            audit.mapping_status = V4.STATUS_NO_POSITION
            audit.mapping_reason = "No broker position; watchlist entry, market data decides."
            audit.is_actionable = True

        enhanced["mapping"] = audit.to_dict()

        # Revolut
        for pos in broker_positions.get("revolut", []):
            if (pos.get("broker_symbol", "") or "").upper() == broker_symbol:
                enhanced["revolut"] = pos
                break

        enhanced_assets.append(enhanced)

    return enhanced_assets


def safe_float(x):
    """Convert to float safely, return None if invalid."""
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None


def fmt(v, digits=2, suffix=""):
    """Format float to string."""
    if v is None:
        return "N/A"
    return f"{v:.{digits}f}{suffix}"


def _openai_compat_call(base_url: str, model: str, messages: list,
                        temperature: float, max_tokens: int, timeout: int,
                        api_key: str, extra_headers: Optional[Dict[str, str]] = None) -> str:
    """POST /chat/completions for OpenAI-compatible APIs (OpenRouter, Gemini, Mistral)."""
    if not api_key:
        raise ValueError("Missing api_key for OpenAI-compatible provider")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    payload = {"model": model, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    r = requests.post(base_url.rstrip("/") + "/chat/completions",
                      json=payload, headers=headers, timeout=timeout)
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"] or {}
    # Reasoning models may put output in 'reasoning' when content is empty
    text = msg.get("content") or msg.get("reasoning") or ""
    text = text.strip() if isinstance(text, str) else ""
    if not text:
        raise ValueError("Empty response content")
    return text


def _call_provider(base_url: str, provider: str, model: str,
                   messages: list, temperature: float,
                   num_ctx: int, max_tokens: int, timeout: int,
                   api_key: str = None, extra_headers: Dict[str, str] = None) -> str:
    if provider == "lmstudio":
        payload = {"model": model, "messages": messages, "temperature": temperature,
                   "max_tokens": max_tokens or num_ctx, "stream": False}
        r = requests.post(base_url.rstrip("/") + "/chat/completions", json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    elif provider in ("openrouter", "gemini", "mistral", "openai_compat"):
        return _openai_compat_call(base_url, model, messages, temperature,
                                   max_tokens or 2048, timeout, api_key, extra_headers)
    else:
        payload = {"model": model, "stream": False, "messages": messages,
                   "options": {"temperature": temperature, "num_ctx": num_ctx}}
        r = requests.post(base_url.rstrip("/") + "/api/chat", json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "").strip()


def _probe_backend(base_url: str, provider: str, timeout: int = 5, api_key: str = None) -> bool:
    try:
        if provider == "lmstudio":
            ep = "/models"
            return requests.get(base_url.rstrip("/") + ep, timeout=timeout).status_code == 200
        elif provider in ("openrouter", "gemini", "mistral", "openai_compat"):
            if not api_key:
                return False
            headers = {"Authorization": f"Bearer {api_key}"}
            return requests.get(base_url.rstrip("/") + "/models", headers=headers, timeout=timeout).status_code == 200
        else:
            ep = "/api/tags"
            return requests.get(base_url.rstrip("/") + ep, timeout=timeout).status_code == 200
    except Exception:
        return False


def _get_backend_models(base_url: str, provider: str) -> List[str]:
    try:
        ep = "/models" if provider == "lmstudio" else "/api/tags"
        r = requests.get(base_url.rstrip("/") + ep, timeout=8); r.raise_for_status()
        if provider == "lmstudio": return [m.get("id","") for m in r.json().get("data",[])]
        return [m.get("name","") for m in r.json().get("models",[])]
    except Exception:
        return []


def _pick_ollama_model(base_url: str, wanted: str) -> str:
    """Return wanted model if present on the host, else first available model.
    Allows empty 'model' in config = auto-select whatever the host serves."""
    try:
        models = _get_backend_models(base_url, "ollama")
        if not models:
            return wanted
        if wanted and any(wanted in m for m in models):
            return wanted
        picked = models[0]
        print(f"  [AI] model '{wanted}' not on host, auto-selected '{picked}'")
        return picked
    except Exception:
        return wanted


def _resolve_env(value: Optional[str]) -> Optional[str]:
    """Substitute ${VAR_NAME} with environment variable value (keys live in api.env)."""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return os.getenv(value[2:-1])
    return value


def call_stage(stage: str, system_prompt: str, user_prompt: str,
               settings: Dict[str, Any], warmup: Dict[str, Any] = None,
               trace: List[Dict[str, Any]] = None) -> str:
    """Route prompt through the backend chain for the given stage.
    Chain order from settings['ai_chain_<stage>'], default [stage, 'fallback'].
    Expected chain: LM Studio -> remote Ollama -> OpenRouter -> Gemini -> Mistral.
    If a background warmup (start_ai_warmup) preloaded a backend, it goes first;
    backends proven dead by warmup are skipped without re-probing.
    Each entry: {provider, base_url, model, api_key, timeout, num_ctx, temperature, enabled}
    api_key supports ${ENV_VAR} substitution (keys live in api.env, never logged).
    When `trace` is a list, per-backend attempts are appended
    {stage, backend, provider, model, ok, latency_s, error} for the run manifest.
    AI never mutates scores/actions; it only summarizes validated facts.
    """
    backends = settings.get("ai_backends", {})
    chain = settings.get(f"ai_chain_{stage}") or [stage, "fallback"]

    # Brief wait for background probe results so offline hosts are skipped fast
    if warmup is not None and not warmup["done"].is_set():
        warmup["done"].wait(timeout=int(settings.get("ai_warmup_wait", 30)))
    skip = set(warmup["skip"]) if warmup else set()
    probe_to = int(settings.get("ai_probe_timeout", 5))
    warmed_key = warmup["warmed"][0] if (warmup and warmup.get("warmed")) else None
    reachable_key = warmup["reachable"][0] if (warmup and warmup.get("reachable")) else None

    order = []
    if warmed_key:
        order.append(warmed_key)
    if reachable_key and reachable_key not in order:
        order.append(reachable_key)
    for key in chain:
        if key not in order:
            order.append(key)

    candidates = []
    for key in order:
        if key in skip and key != warmed_key:
            continue
        cfg = backends.get(key, {})
        if cfg and cfg.get("enabled", True):
            candidates.append((key, cfg))

    if not candidates:
        return f"ERROR: No enabled backends configured for stage '{stage}'."

    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

    for key, cfg in candidates:
        base_url = cfg.get("base_url", ""); provider = cfg.get("provider", "ollama")
        model = cfg.get("model", ""); timeout = int(cfg.get("timeout", 300))
        num_ctx = int(cfg.get("num_ctx", 8192)); temp = float(cfg.get("temperature", 0.08))
        max_tok = int(cfg.get("max_tokens", 2048))
        api_key = _resolve_env(cfg.get("api_key"))
        extra_headers = cfg.get("headers") or None
        if not base_url:
            continue
        if provider == "ollama":
            if warmed_key == key and warmup.get("warmed"):
                model = warmup["warmed"][1]
            else:
                model = _pick_ollama_model(base_url, model)
        if not model:
            print(f"  [AI] {key}: no model available - next")
            continue
        if not _probe_backend(base_url, provider, timeout=probe_to, api_key=api_key):
            print(f"  [AI] {key} ({provider.upper()}) unreachable - next")
            continue
        print(f"  [AI] {stage.upper()} -> {key} | {model[:50]} | ctx={num_ctx}")
        started = time.perf_counter()
        try:
            out = _call_provider(base_url, provider, model, messages, temp,
                                 num_ctx, max_tok, timeout, api_key, extra_headers)
            if trace is not None:
                trace.append({"stage": stage, "backend": key, "provider": provider,
                              "model": model, "ok": True,
                              "latency_s": round(time.perf_counter() - started, 2),
                              "error": None})
            return out
        except Exception as e:
            if trace is not None:
                trace.append({"stage": stage, "backend": key, "provider": provider,
                              "model": model, "ok": False,
                              "latency_s": round(time.perf_counter() - started, 2),
                              "error": V4.redact_secrets(str(e))[:160]})
            print(f"  [AI] {key} error ({V4.redact_secrets(str(e))[:160]}) - next backend")
    return f"ERROR: All backends failed for stage '{stage}'."


def _warm_model(base_url: str, provider: str, model: str, timeout: int) -> bool:
    """Preload a local model with a tiny request (loads weights into memory).
    Ollama: keep_alive keeps it resident for the real call later."""
    tiny = [{"role": "user", "content": "Reply with: OK"}]
    if provider == "ollama":
        payload = {"model": model, "stream": False, "messages": tiny,
                   "options": {"temperature": 0, "num_ctx": 2048}, "keep_alive": "60m"}
        r = requests.post(base_url.rstrip("/") + "/api/chat", json=payload, timeout=timeout)
    else:  # lmstudio
        payload = {"model": model, "messages": tiny, "temperature": 0,
                   "max_tokens": 5, "stream": False}
        r = requests.post(base_url.rstrip("/") + "/chat/completions", json=payload, timeout=timeout)
    r.raise_for_status()
    return True


def start_ai_warmup(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Probe the alpha chain in a background thread and preload the first
    reachable local model. Main flow keeps collecting data meanwhile.
    Returns state: {done, warmed, reachable, skip, thread}.
    - warmed: (key, model) preloaded and ready, try first
    - reachable: (key, model) probed OK but not (yet) preloaded
    - skip: keys proven dead this run (offline endpoint / failed load)
    """
    state: Dict[str, Any] = {"done": threading.Event(), "warmed": None,
                             "reachable": None, "skip": set(), "thread": None}

    def _work():
        try:
            chain = settings.get("ai_chain_alpha") or ["alpha", "fallback"]
            backends = settings.get("ai_backends", {})
            probe_to = int(settings.get("ai_probe_timeout", 5))
            for key in chain:
                cfg = backends.get(key, {})
                if not cfg or not cfg.get("enabled", True):
                    continue
                prov = cfg.get("provider", "ollama")
                url = cfg.get("base_url", "")
                akey = _resolve_env(cfg.get("api_key"))
                if not url or not _probe_backend(url, prov, timeout=probe_to, api_key=akey):
                    state["skip"].add(key)
                    continue
                model = cfg.get("model", "")
                if prov == "ollama":
                    try:
                        model = _pick_ollama_model(url, model)
                    except Exception:
                        pass
                if not model:
                    state["skip"].add(key)
                    continue
                if state["reachable"] is None:
                    state["reachable"] = (key, model)
                if prov in ("ollama", "lmstudio"):
                    cap = int(settings.get("lmstudio_load_timeout", 900)
                              if prov == "lmstudio"
                              else settings.get("ollama_load_timeout", 300))
                    try:
                        _warm_model(url, prov, model, cap)
                        state["warmed"] = (key, model)
                        print(f"  [AI] warmup OK: {key} | {model[:50]} preloaded")
                    except Exception as e:
                        print(f"  [AI] warmup {key} failed ({str(e)[:120]}), skipping")
                        state["skip"].add(key)
                        continue
                else:
                    # Cloud backends need no preload; first reachable wins
                    state["warmed"] = (key, model)
                break
        finally:
            state["done"].set()

    t = threading.Thread(target=_work, daemon=True, name="ai-warmup")
    state["thread"] = t
    t.start()
    return state


_SYSTEM_ALPHA = (
    "You are the Chief Investment Officer. Think in English, respond in Slovak.\n"
    "Make clear portfolio decisions based ONLY on the provided data.\n"
    "For EVERY holding: one verdict — DOKUPOVAT / DRZAT / ZVAZUJ PREDAJ / PREDAJ.\n"
    "For Pie holdings: recommend allocation % changes or stock substitutions.\n"
    "NEWS: only concrete CHANGES with numbers. Skip generic sector commentary.\n"
    "Never fabricate prices, earnings, ratings, or events not in the data.\n"
    "Missing data → write 'neoverene'. Be direct and actionable."
)

_SYSTEM_SUMMARY = (
    "You are a portfolio briefing writer. Think in English, respond in Slovak.\n"
    "Compress the analysis into max 400 words.\n"
    "Structure: 1) Top 3 actions  2) Top risk  3) Top opportunity  4) Portfolio health.\n"
    "No fluff. Institutional tone."
)


def run_ai_pipeline(prompt: str, settings: Dict[str, Any],
                    use_summary: bool = True, warmup: Dict[str, Any] = None,
                    trace: List[Dict[str, Any]] = None) -> str:
    """Alpha -> optional Summary stage through the backend chain."""
    alpha_out = call_stage("alpha", _SYSTEM_ALPHA, prompt, settings, warmup=warmup, trace=trace)
    if alpha_out.startswith("ERROR:") or not use_summary:
        return alpha_out
    summary_out = call_stage("summary", _SYSTEM_SUMMARY,
                             f"Compress this analysis:\n\n{alpha_out}", settings,
                             warmup=warmup, trace=trace)
    if summary_out.startswith("ERROR:"):
        return alpha_out
    return f"{alpha_out}\n\n---\n## Executive Brief\n{summary_out}"



def _asset_line(data: Dict[str, Any]) -> str:
    """Compact 1-line for T212 positions with no signal/news."""
    t212  = data.get("trading212") or {}
    price = data.get("price") or {}
    rec   = data.get("recommendation") or {}
    sym   = data.get("broker_symbol", "?")
    name  = data.get("name", "")
    pnl   = t212.get("pnl")
    pct   = t212.get("pnl_pct")
    p     = price.get("price") or t212.get("current_price")
    sign  = "+" if (pnl or 0) >= 0 else ""
    label = (f"P&L={sign}{fmt(pnl)} ({sign}{pct:.1f}%)" if pnl is not None and pct is not None
             else f"cena={fmt(p)}" if p else "")
    verdict = rec.get("recommendation", "WATCH")
    n     = f" — {name}" if name and name != sym else ""
    return f"- {sym}{n}: {label} | {verdict}"


def _append_asset_block(lines: list, data: Dict[str, Any],
                        include_all_news: bool = True) -> None:
    """Full block for assets with signal/news data — for AI prompt."""
    ACTIONABLE = [
        "earnings","revenue","profit","loss","guidance","forecast",
        "acquisition","merger","deal","lawsuit","fine","penalty",
        "upgrade","downgrade","price target","beat","miss",
        "CEO","CFO","layoff","restructur","dividend","buyback",
        "trump","tariff","sanction","ban","regulation","recall",
    ]
    sym   = data.get("broker_symbol", "?")
    name  = data.get("name") or sym
    price = data.get("price") or {}
    sigs  = data.get("signals") or {}
    rec   = data.get("recommendation") or {}
    t212  = data.get("trading212")
    news  = data.get("news") or []

    hdr = f"## {sym}" + (f" — {name}" if name and name != sym else "")
    lines.append(hdr)

    p = price.get("price")
    if p:
        lines.append(
            f"Cena: {fmt(p)} | 1D: {fmt(price.get('change_1d_pct'),2,'%')} | "
            f"5D: {fmt(price.get('change_5d_pct'),2,'%')} | Trend: {price.get('trend_hint','?')}"
        )

    bp = sigs.get("buy_probability", 0)
    sp = sigs.get("sell_probability", 0)
    if bp or sp:
        lines.append(f"Signál: BUY {bp}% | SELL {sp}% | Tech: {sigs.get('technical_score',0)} | "
                     f"Sent: {sigs.get('sentiment_score',0)} | DQ: {data.get('data_quality',0)}")

    if rec:
        lines.append(f"Score: V{rec.get('score_value',0)} M{rec.get('score_momentum',0)} "
                     f"S{rec.get('score_sentiment',0)} = {rec.get('total_score',0):+d} "
                     f"| Python rec: {rec.get('recommendation','WATCH')} | DQ: {rec.get('data_quality','?')}")

    if t212:
        qty   = t212.get("quantity")
        avg_p = t212.get("average_price")
        pnl   = t212.get("pnl")
        pct   = t212.get("pnl_pct")
        live  = price.get("price") or t212.get("current_price") or 0
        val   = (live * qty) if (live and qty) else t212.get("market_value")
        sign  = "+" if (pnl or 0) >= 0 else ""
        pct_s = f" ({sign}{pct:.1f}%)" if pct is not None else ""
        lines.append(f"Pozícia: qty={fmt(qty,4)} | avg={fmt(avg_p)} | "
                     f"val≈{fmt(val)} EUR | P&L={sign}{fmt(pnl)}{pct_s}")

    filtered = [n for n in news if any(
        kw in (n.get("title","") + n.get("snippet","")).lower() for kw in ACTIONABLE)]
    to_show = news[:3] if include_all_news else filtered[:2]
    if to_show:
        lines.append("Správy:")
        for n in to_show:
            lines.append(f"  • [{n.get('sentiment','')}] {n.get('title','')}")
    lines.append("")


def build_prompt(collected_data: List[Dict[str, Any]], portfolio_rules: Dict[str, str],
                 settings: Dict[str, Any] = None,
                 trump_news_ctx: Optional[Dict[str, Any]] = None) -> str:
    """
    Zostav prompt pre Fin-R1 — deep reasoning per ticker.
    Každý ticker dostane štruktúrovaný blok s dátami, AI musí rozmyslieť a dať verdikt.
    """
    settings = settings or {}
    today = datetime.now().strftime("%Y-%m-%d")
    news_age = settings.get("max_news_age_hours", 48)
    lines = []

    lines.append(f"# PORTFOLIO ANALYSIS — {today}")
    lines.append(f"News timeframe: last {news_age} hours only")
    lines.append("")

    if portfolio_rules:
        lines.append("# PORTFOLIO RULES")
        for v in portfolio_rules.values():
            lines.append(f"- {v}")
        lines.append("")

    # Build detailed asset blocks for ALL T212 positions (not just rich ones)
    t212_all = [d for d in collected_data
                if d.get("trading212")
                and (d.get("summary") or {}).get("label") != "T212_INTERNAL_TICKER"]
    
    # Sort by position value descending
    t212_all.sort(key=lambda d: abs((d.get("trading212") or {}).get("market_value") or 0), reverse=True)

    if t212_all:
        lines.append("# T212 POZÍCIE — DEEP ANALYSIS REQUIRED")
        lines.append("Pre KAŽDÚ pozíciu: daj VERDIKT (DOKUPOVAT/DRZAT/ZVAZUJ PREDAJ/PREDAJ) + dôvod.")
        lines.append("")
        for d in t212_all:
            _append_asset_block(lines, d, include_all_news=True)

    # Watchlist with signals
    non_hold = [d for d in collected_data
                if not d.get("trading212")
                and (d.get("summary") or {}).get("label") != "T212_INTERNAL_TICKER"
                and d.get("data_quality", 0) >= 60
                and (d.get("recommendation") or {}).get("recommendation") in ("BUY", "SELL")]
    if non_hold:
        lines.append("\n# WATCHLIST — SIGNAL CANDIDATES")
        lines.append("Pre KAŽDÚ: potvrď BUY/SELL alebo WATCH s dôvodom.")
        lines.append("")
        for d in sorted(non_hold,
                        key=lambda d: abs((d.get("recommendation") or {}).get("total_score", 0)),
                        reverse=True):
            _append_asset_block(lines, d, include_all_news=False)

    # Global context
    t_market    = (trump_news_ctx or {}).get("market_impact", [])
    t_companies = (trump_news_ctx or {}).get("company_hits", {})
    if t_market or t_companies:
        lines.append("\n# MAKRO KONTEXT")
        for item in t_market[:4]:
            lines.append(f"- {item.get('title','')} [{item.get('sentiment','')}]")
        for sym, hits in list(t_companies.items())[:6]:
            for h in hits[:1]:
                lines.append(f"- [{sym}] {h.get('title','')} [{h.get('sentiment','')}]")
        lines.append("")

    # News summary
    global_news = []
    for d in collected_data:
        for n in d.get("news", [])[:2]:
            if n.get("title"):
                global_news.append(n)
    if global_news:
        lines.append("\n# NEWS SUMMARY")
        for n in global_news[:15]:
            lines.append(f"- [{n.get('sentiment','NEUTRAL')}] {n.get('title','')}")
        lines.append("")

    lines.append("\n# ÚLOHA")
    lines.append("Si Chief Investment Officer. Pre KAŽDÚ T212 pozíciu daj:")
    lines.append("  1. VERDIKT: DOKUPOVAT / DRZAT / ZVAZUJ PREDAJ / PREDAJ")
    lines.append("  2. DÔVOD: 2-3 konkrétne vety (fundamentál, technika, news, riziko)")
    lines.append("  3. PRICE TARGET: cieľová cena na 3-6 mesiacov")
    lines.append("  4. STOP LOSS: kde by si vyhodil")
    lines.append("")
    lines.append("Pre watchlist: BUY / SELL / WATCH + dôvod.")
    lines.append("")
    lines.append("NEVYMÝŠĽAJ ceny ani pravdepodobnosti. Použi IBA poskytnuté dáta.")
    lines.append("Ak chýbajú dáta: napíš 'NEDOSTATOČNÉ DÁTA'.")

    return "\n".join(lines)





# ============================================================================
# UTILITY FUNCTIONS (Sentiment, Domain Classification)
# ============================================================================

def classify_sentiment(text: str) -> Tuple[str, float]:
    """Classify sentiment of text. Returns (sentiment, confidence).
    
    Simple keyword-based classification for financial news.
    """
    text_lower = text.lower()
    
    # Positive keywords
    positive_kw = [
        "beat", "exceed", "surge", "soar", "jump", "rise", "gain", "growth",
        "profit", "record", "strong", "bullish", "upgrade", "buy", "outperform",
        "dividend", "buyback", "acquisition", "merger", "partnership", "deal",
        "approval", "launch", "expand", "increase", "raise", "boost", "rally"
    ]
    
    # Negative keywords
    negative_kw = [
        "miss", "fall", "drop", "decline", "loss", "weak", "bearish", "downgrade",
        "sell", "underperform", "cut", "reduce", "layoff", "restructuring",
        "lawsuit", "fine", "penalty", "investigation", "recall", "bankruptcy",
        "default", "delay", "cancel", "warn", "alert", "risk", "concern"
    ]
    
    pos_count = sum(1 for kw in positive_kw if kw in text_lower)
    neg_count = sum(1 for kw in negative_kw if kw in text_lower)
    
    if pos_count > neg_count:
        confidence = min(0.9, 0.5 + (pos_count - neg_count) * 0.1)
        return "POSITIVE", confidence
    elif neg_count > pos_count:
        confidence = min(0.9, 0.5 + (neg_count - pos_count) * 0.1)
        return "NEGATIVE", confidence
    else:
        return "NEUTRAL", 0.5


def extract_domain(url: str) -> str:
    """Extract domain from URL."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        # Remove www prefix
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def classify_source(url: str) -> str:
    """Classify source type based on domain."""
    domain = extract_domain(url)
    
    # Financial news
    finance_domains = {
        "reuters.com", "bloomberg.com", "wsj.com", "ft.com", "cnbc.com",
        "marketwatch.com", "investing.com", "seekingalpha.com", "fool.com",
        "morningstar.com", "yahoo.com", "finance.yahoo.com", "barrons.com",
        "thestreet.com", "benzinga.com", "zacks.com", "tipranks.com"
    }
    
    # Official sources
    official_domains = {
        "sec.gov", "ecb.europa.eu", "federalreserve.gov", "treasury.gov",
        "who.int", "eia.gov", "bls.gov", "census.gov"
    }
    
    # General news
    news_domains = {
        "apnews.com", "bbc.com", "cnn.com", "nytimes.com", "washingtonpost.com",
        "theguardian.com", "reuters.com", "ap.org", "afp.com", "dpa-international.com"
    }
    
    if domain in finance_domains:
        return "finance_data"
    elif domain in official_domains:
        return "official"
    elif domain in news_domains:
        return "news"
    elif domain:
        return "other"
    return "unknown"


# Simple in-memory cache for DDGS queries within a single run
_ddgs_cache = {}

def _ddgs_cached_news(query: str, max_results: int = 5, timeout: float = 8.0) -> List[Dict]:
    """Cached DDGS news search with timeout - returns empty list if too slow."""
    cache_key = f"{query}:{max_results}"
    if cache_key in _ddgs_cache:
        return _ddgs_cache[cache_key]
    
    if not DDGS:
        return []
    
    result_queue = queue.Queue()
    
    def worker():
        try:
            results = list(DDGS().news(query, max_results=max_results, region='us', safesearch='moderate'))
            _ddgs_cache[cache_key] = results
            result_queue.put(results)
        except Exception as e:
            logger.debug(f"DDGS cached news failed for {query}: {e}")
            result_queue.put([])
    
    thread = threading.Thread(target=worker)
    thread.daemon = True
    thread.start()
    thread.join(timeout=timeout)
    
    if thread.is_alive():
        logger.debug(f"DDGS timeout for query: {query}")
        return []
    
    return result_queue.get() if not result_queue.empty() else []


def collect_ticker_news(symbol: str, aliases: List[str], max_items: int = 5, max_age_hours: int = 48, timeout: float = 5.0) -> List[Dict[str, Any]]:
    """Collect news for a specific ticker using its symbol and aliases.
    
    Args:
        symbol: Primary ticker symbol
        aliases: Alternative symbols/names
        max_items: Maximum news items to return
        max_age_hours: Only include news from last N hours (default 48h)
        timeout: Timeout in seconds for each DDGS query (default 5s)
        
    Returns:
        List of news items with sentiment, filtered by time
    """
    if not DDGS:
        return []
    
    # Build search queries from symbol and aliases
    search_terms = [symbol] + aliases[:3]  # Limit to avoid too many queries
    out = []
    seen = set()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    
    for term in search_terms:
        query = f"{term} stock news"
        try:
            for item in _ddgs_cached_news(query, max_results=max_items, timeout=timeout):
                url = item.get("url", "")
                title = item.get("title", "")
                key = url or title
                if not key or key in seen:
                    continue
                seen.add(key)
                
                # Check date if available
                date_str = item.get("date") or item.get("published")
                if date_str:
                    try:
                        # Parse various date formats
                        for fmt in ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%a, %d %b %Y %H:%M:%S %z"]:
                            try:
                                item_date = datetime.strptime(date_str.replace("Z", "+0000"), fmt)
                                if item_date.tzinfo is None:
                                    item_date = item_date.replace(tzinfo=timezone.utc)
                                if item_date < cutoff:
                                    continue
                                break
                            except ValueError:
                                continue
                    except Exception:
                        pass  # If date parsing fails, include anyway
                
                text = f"{title} {item.get('snippet', '')}"
                sentiment, confidence = classify_sentiment(text)
                out.append({
                    **item,
                    "ticker": symbol,
                    "query": query,
                    "source_domain": extract_domain(url),
                    "source_type": classify_source(url),
                    "sentiment": sentiment,
                    "sentiment_confidence": round(confidence, 2),
                    "trust_level": "MEDIUM" if classify_source(url) in {"news", "finance_data", "official"} else "LOW",
                })
        except Exception as e:
            logger.debug(f"collect_ticker_news failed for {term}: {e}")
            continue
    
    return out[:max_items]


def collect_global_news(settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect broad market/theme news for context beyond individual tickers.
    
    Time-filtered to last 48 hours for relevance.
    """
    queries = settings.get("global_news_queries", [])
    max_items = int(settings.get("max_global_news", 20))  # Increased from 12
    max_age_hours = int(settings.get("max_news_age_hours", 48))
    out = []
    seen = set()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    
    for query in queries:
        try:
            for item in _ddgs_cached_news(query, max_results=5, timeout=8.0):
                url = item.get("url", "")
                title = item.get("title", "")
                key = url or title
                if not key or key in seen:
                    continue
                
                # Check date
                date_str = item.get("date") or item.get("published")
                if date_str:
                    try:
                        for fmt in ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%a, %d %b %Y %H:%M:%S %z"]:
                            try:
                                item_date = datetime.strptime(date_str.replace("Z", "+0000"), fmt)
                                if item_date.tzinfo is None:
                                    item_date = item_date.replace(tzinfo=timezone.utc)
                                if item_date < cutoff:
                                    continue
                                break
                            except ValueError:
                                continue
                    except Exception:
                        pass
                
                seen.add(key)
                text = f"{title} {item.get('snippet', '')}"
                sentiment, confidence = classify_sentiment(text)
                out.append({
                    **item,
                    "query": query,
                    "source_domain": extract_domain(url),
                    "source_type": classify_source(url),
                    "sentiment": sentiment,
                    "sentiment_confidence": round(confidence, 2),
                    "trust_level": "MEDIUM" if classify_source(url) in {"news", "finance_data", "official"} else "LOW",
                })
        except Exception as e:
            logger.debug(f"collect_global_news failed for {query}: {e}")
            continue
    
    return out[:max_items]


def collect_discovery_news(settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Collect new-stock / catalyst ideas. These are NOT automatic recommendations.
    They are watchlist candidates for manual review.
    Time-filtered to last 48 hours.
    """
    queries = settings.get("discovery_queries", [])
    max_items = int(settings.get("max_discovery_news", 20))  # Increased from 12
    max_age_hours = int(settings.get("max_news_age_hours", 48))
    out = []
    seen = set()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    
    for query in queries:
        try:
            for item in _ddgs_cached_news(query, max_results=5, timeout=8.0):
                url = item.get("url", "")
                title = item.get("title", "")
                key = url or title
                if not key or key in seen:
                    continue
                
                # Check date
                date_str = item.get("date") or item.get("published")
                if date_str:
                    try:
                        for fmt in ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%a, %d %b %Y %H:%M:%S %z"]:
                            try:
                                item_date = datetime.strptime(date_str.replace("Z", "+0000"), fmt)
                                if item_date.tzinfo is None:
                                    item_date = item_date.replace(tzinfo=timezone.utc)
                                if item_date < cutoff:
                                    continue
                                break
                            except ValueError:
                                continue
                    except Exception:
                        pass
                
                seen.add(key)
                text = f"{title} {item.get('snippet', '')}"
                sentiment, confidence = classify_sentiment(text)
                out.append({
                    **item,
                    "query": query,
                    "source_domain": extract_domain(url),
                    "source_type": classify_source(url),
                    "sentiment": sentiment,
                    "sentiment_confidence": round(confidence, 2),
                    "trust_level": "LOW_TO_MEDIUM",
                })
        except Exception as e:
            logger.debug(f"collect_discovery_news failed for {query}: {e}")
            continue
    
    return out[:max_items]


# ============================================================================
# ASSET SUMMARY BUILDER
# ============================================================================

def build_asset_summary(asset_data: Dict[str, Any], signals: Dict[str, Any],
    data_quality: int,
    settings: Dict[str, Any]) -> Dict[str, Any]:
    """Build comprehensive summary for asset including signals and key metrics."""
    price_data = asset_data.get("price", {}) or {}
    technicals = asset_data.get("technicals", {}) or {}
    info = asset_data.get("info", {}) or {}
    news = asset_data.get("news", [])
    rec = asset_data.get("recommendation", {})
    t212 = asset_data.get("trading212", {})
    
    # Calculate average news sentiment
    avg_sentiment = 0.0
    if news:
        sents = []
        for item in news[:10]:
            s = item.get("sentiment", "NEUTRAL")
            if s == "POSITIVE":
                sents.append(1)
            elif s == "NEGATIVE":
                sents.append(-1)
            else:
                sents.append(0)
        if sents:
            avg_sentiment = sum(sents) / len(sents)
    
    # Buy/Sell probabilities from signals
    buy_p = signals.get("buy_probability", 0)
    sell_p = signals.get("sell_probability", 0)
    
    # Determine status based on thresholds
    buy_thresh = settings.get("buy_probability_threshold", 60)
    sell_thresh = settings.get("sell_probability_threshold", 70)
    
    if buy_p >= buy_thresh:
        status = "BUY_CANDIDATE"
    elif sell_p >= sell_thresh:
        status = "SELL_CANDIDATE"
    elif data_quality < 40:
        status = "LOW_DATA_QUALITY"
    else:
        status = "HOLD_OR_WAIT"
    
    # Risk levels
    risk_levels = {}
    current_price = price_data.get("price")
    rsi = signals.get("rsi")
    if rsi and rsi < 30:
        risk_levels["oversold"] = f"RSI oversold at {rsi:.1f}"
    elif rsi and rsi > 70:
        risk_levels["overbought"] = f"RSI overbought at {rsi:.1f}"
    
    return {
        # Identity
        "broker_symbol": asset_data.get("broker_symbol"),
        "name": asset_data.get("name"),
        "group": asset_data.get("group"),
        
        # Price
        "current_price": current_price,
        "change_1d_pct": technicals.get("change_1d_pct"),
        "change_5d_pct": technicals.get("change_5d_pct"),
        "currency": technicals.get("currency"),
        "trend_hint": technicals.get("trend_hint"),
        
        # Technicals
        "ma20": technicals.get("ma20"),
        "ma50": technicals.get("ma50"),
        "ma200": technicals.get("ma200"),
        "rsi": rsi,
        
        # Signals
        "technical_score": signals.get("technical_score", 0),
        "sentiment_score": signals.get("sentiment_score", 0),
        "buy_probability": buy_p,
        "sell_probability": sell_p,
        "avg_news_sentiment": round(avg_sentiment, 2),
        
        # Quality
        "data_quality_score": data_quality,
        "dq_status": asset_data.get("dq_status", "PARTIAL"),
        
        # Risk
        "risk_levels": risk_levels,
        "risk_level_value": None,
        
        # Status
        "status": status,
        "label": status,
        
        # Additional
        "news_count": len(news),
        "market_cap": info.get("marketCap"),
        "volume": asset_data.get("volume"),
        "pe_ratio": info.get("trailingPE"),
        "dividend_yield": info.get("dividendYield"),
        "beta": info.get("beta"),
    }

# ============================================================================
# SIGNAL CALCULATION (Technical Analysis)
# ============================================================================

def calculate_signals(price_data: Dict[str, Any], news: list, settings: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate technical signals from price data and news sentiment.
    
    Args:
        price_data: Dict with current price, technicals (MA, RSI, trend, changes)
        news: List of news items with sentiment
        settings: Configuration settings
        
    Returns:
        Dict with buy_probability, sell_probability, technical_score, sentiment_score
    """
    # The price_data dict contains technical indicators directly (not nested under "technicals")
    # Extract all needed values from price_data dict
    if not isinstance(price_data, dict):
        price_data = {}
    
    # Default values - start slightly bullish for growth stocks
    buy_prob = 50.0
    sell_prob = 50.0
    tech_score = 50
    sent_score = 50
    
    # Technical analysis - extract from price_data directly
    rsi = price_data.get('rsi')
    ma20 = price_data.get('ma20')
    ma50 = price_data.get('ma50')
    ma200 = price_data.get('ma200')
    current_price = price_data.get('price')
    change_1d = price_data.get('change_1d_pct', 0)
    change_5d = price_data.get('change_5d_pct', 0)
    trend = price_data.get('trend_hint', 'NEUTRAL')
    
    # RSI signals (0-100) - more sensitive
    if rsi is not None:
        if rsi < 30:
            buy_prob += 20
            tech_score += 15
        elif rsi > 70:
            sell_prob += 20
            tech_score -= 15
        elif rsi < 40:
            buy_prob += 10
            tech_score += 5
        elif rsi > 60:
            sell_prob += 10
            tech_score -= 5
        elif rsi < 50:
            buy_prob += 3
            tech_score += 2
        elif rsi > 50:
            sell_prob += 3
            tech_score -= 2
    
    # Moving average signals - more sensitive
    if current_price is not None and ma20 is not None and ma50 is not None:
        if current_price > ma20 > ma50:
            buy_prob += 12
            tech_score += 8
        elif current_price < ma20 < ma50:
            sell_prob += 12
            tech_score -= 8
        elif current_price > ma20:
            buy_prob += 6
            tech_score += 4
        elif current_price < ma20:
            sell_prob += 6
            tech_score -= 4
    
    # Golden/Death cross (MA50 vs MA200)
    if ma50 is not None and ma200 is not None:
        if ma50 > ma200:
            buy_prob += 8
            tech_score += 5
        else:
            sell_prob += 8
            tech_score -= 5
    
    # Price momentum - more sensitive
    if change_1d is not None:
        if change_1d > 2:
            buy_prob += 5
            tech_score += 3
        elif change_1d < -2:
            sell_prob += 5
            tech_score -= 3
    
    if change_5d is not None:
        if change_5d > 3:
            buy_prob += 5
            tech_score += 3
        elif change_5d < -3:
            sell_prob += 5
            tech_score -= 3
    
    # Trend
    if trend == "UP":
        buy_prob += 5
        tech_score += 3
    elif trend == "DOWN":
        sell_prob += 5
        tech_score -= 3
    
    # News sentiment - more weight
    if news:
        sentiments = []
        for item in news[:10]:  # Last 10 news
            s = item.get("sentiment", "NEUTRAL")
            if s == "POSITIVE":
                sentiments.append(1)
            elif s == "NEGATIVE":
                sentiments.append(-1)
            else:
                sentiments.append(0)
        
        if sentiments:
            avg_sent = sum(sentiments) / len(sentiments)
            # Convert to 0-100 scale
            sent_score = int(50 + avg_sent * 30)
            sent_score = max(0, min(100, sent_score))
            
            if avg_sent > 0.3:
                buy_prob += 8
            elif avg_sent < -0.3:
                sell_prob += 8
            elif avg_sent > 0:
                buy_prob += 3
            elif avg_sent < 0:
                sell_prob += 3
    
    # Clamp probabilities
    buy_prob = max(0, min(100, buy_prob))
    sell_prob = max(0, min(100, sell_prob))
    tech_score = max(0, min(100, tech_score))
    sent_score = max(0, min(100, sent_score))
    
    return {
        "buy_probability": round(buy_prob, 1),
        "sell_probability": round(sell_prob, 1),
        "technical_score": tech_score,
        "sentiment_score": sent_score,
        "rsi": rsi,
        "trend": trend,
    }

# ============================================================================
# DATA QUALITY SCORING
# ============================================================================

def calculate_data_quality(data: Dict[str, Any]) -> int:
    """Calculate data quality score (0-100) based on available data.
    
    Factors:
    - Price data available (30 pts)
    - Technical indicators available (20 pts)
    - News available (15 pts)
    - Fundamental data available (15 pts)
    - Volume data available (10 pts)
    - Symbol mapping correct (10 pts)
    """
    score = 0
    
    price_data = data.get("price", {})
    technicals = data.get("technicals", {}) if isinstance(data, dict) else {}
    info = data.get("info", {}) if isinstance(data, dict) else {}
    news = data.get("news", [])
    
    # Price data (30 pts)
    if price_data.get("price") is not None:
        score += 30
    
    # Technical indicators (20 pts)
    tech_count = sum(1 for k in ['rsi', 'ma20', 'ma50', 'ma200', 'change_1d_pct', 'change_5d_pct'] 
                     if technicals.get(k) is not None)
    score += min(20, tech_count * 3)
    
    # News (15 pts)
    if news:
        score += min(15, len(news) * 3)
    
    # Fundamental data (15 pts)
    fund_count = sum(1 for k in ['marketCap', 'volume', 'trailingPE', 'dividendYield', 'beta']
                     if info.get(k) is not None)
    score += min(15, fund_count * 3)
    
    # Volume (10 pts)
    if data.get("volume") is not None:
        score += 10
    
    # Symbol mapping (10 pts) - if we have a yahoo_symbol different from broker_symbol
    if data.get("yahoo_symbol") and data.get("yahoo_symbol") != data.get("broker_symbol"):
        score += 10
    elif data.get("yahoo_symbol"):
        score += 5
    
    # Determine status
    if score >= 80:
        dq_status = "OK"
    elif score >= 50:
        dq_status = "PARTIAL"
    elif score >= 30:
        dq_status = "LOW"
    else:
        dq_status = "NO_PRICE_DATA"
    
    data["dq_status"] = dq_status
    return score


# ============================================================================
# RECOMMENDATION CALCULATION
# ============================================================================

def calculate_recommendation(data: Dict[str, Any], signals: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate recommendation based on signals, data quality, and settings.
    
    Uses a weighted scoring system:
    - Value score (fundamentals): 30%
    - Momentum score (technical): 40%
    - Sentiment score (news): 30%
    """
    dq = data.get("dq_status", "PARTIAL")
    price_data = data.get("price", {}) or {}
    info = data.get("info", {}) or {}
    news = data.get("news", [])
    
    # Get thresholds from settings
    buy_threshold = settings.get("buy_probability_threshold", 60)
    sell_threshold = settings.get("sell_probability_threshold", 70)
    
    buy_prob = signals.get("buy_probability", 50)
    sell_prob = signals.get("sell_probability", 50)
    tech_score = signals.get("technical_score", 50)
    sent_score = signals.get("sentiment_score", 50)
    rsi = signals.get("rsi")
    
    # Value score (fundamentals)
    value_score = 50
    pe = info.get("trailingPE")
    pb = info.get("priceToBook")
    div_yield = info.get("dividendYield")
    beta = info.get("beta")
    
    if pe is not None and pe > 0:
        if pe < 15:
            value_score += 15
        elif pe > 30:
            value_score -= 10
    if pb is not None and pb > 0:
        if pb < 1.5:
            value_score += 10
        elif pb > 5:
            value_score -= 10
    if div_yield is not None and div_yield > 0.03:
        value_score += 10
    if beta is not None:
        if beta < 1.0:
            value_score += 5
        elif beta > 1.5:
            value_score -= 5
    
    value_score = max(0, min(100, value_score))
    
    # Momentum score (technical)
    momentum_score = tech_score
    
    # Sentiment score
    sentiment_score = sent_score
    
    # Weighted total
    total_score = int(
        value_score * 0.30 +
        momentum_score * 0.40 +
        sentiment_score * 0.30
    )
    
    # Determine recommendation
    recommendation = "WATCH"
    comment_parts = []
    
    if dq in ("OK", "PARTIAL"):
        # Use buy/sell probability as primary signal, total_score as confirmation
        if buy_prob >= 65:
            recommendation = "BUY"
            comment_parts.append(f"Strong buy signal (prob={buy_prob:.0f}%, score={total_score})")
        elif buy_prob >= 55:
            recommendation = "BUY"
            comment_parts.append(f"Buy signal (prob={buy_prob:.0f}%, score={total_score})")
        elif sell_prob >= 65:
            recommendation = "SELL"
            comment_parts.append(f"Strong sell signal (prob={sell_prob:.0f}%, score={total_score})")
        elif sell_prob >= 55:
            recommendation = "SELL"
            comment_parts.append(f"Sell signal (prob={sell_prob:.0f}%, score={total_score})")
        elif 45 <= buy_prob <= 55 and 45 <= sell_prob <= 55:
            recommendation = "HOLD"
            comment_parts.append(f"Neutral, hold (buy={buy_prob:.0f}%, sell={sell_prob:.0f}%, score={total_score})")
        else:
            recommendation = "WATCH"
            comment_parts.append(f"Mixed signals, monitor (buy={buy_prob:.0f}%, sell={sell_prob:.0f}%, score={total_score})")
    else:
        recommendation = "WATCH"
        comment_parts.append(f"Low data quality ({dq}) - cannot determine")
    
    # Add RSI context
    if rsi is not None:
        if rsi < 30:
            comment_parts.append("RSI oversold")
        elif rsi > 70:
            comment_parts.append("RSI overbought")
    
    comment = " | ".join(comment_parts)
    
    # Average news sentiment
    avg_sentiment = None
    if news:
        sents = []
        for item in news[:10]:
            s = item.get("sentiment", "NEUTRAL")
            if s == "POSITIVE":
                sents.append(1)
            elif s == "NEGATIVE":
                sents.append(-1)
            else:
                sents.append(0)
        if sents:
            avg_sentiment = round(sum(sents) / len(sents), 2)
    
    return {
        "recommendation": recommendation,
        "data_quality": dq,
        "score_value": value_score,
        "score_momentum": momentum_score,
        "score_sentiment": sentiment_score,
        "total_score": total_score,
        "comment": comment,
        "avg_sentiment": avg_sentiment,
        "rsi": rsi
    }


def action_label_for_asset(data: Dict[str, Any]) -> str:
    """Human-friendly action label. Not an order instruction."""
    s = data.get("summary", {})
    group = data.get("group", "")
    dq = s.get("data_quality_score", 0)
    buy = s.get("buy_probability", 0)
    sell = s.get("sell_probability", 0)
    risk = s.get("risk_score", 0)

    if dq < 60:
        return "NO_SIGNAL_DATA_LOW"
    if sell >= 70 or risk >= 70:
        return "RISK_REVIEW"
    if buy >= 60:
        return "BUY_WATCH_CONFIRM"
    if group == "SHORT_TERM_TRADING" and (buy >= 25 or abs(s.get("change_1d_pct") or 0) >= 4):
        return "SHORT_TERM_WATCH"
    if group in {"LONG_RUN_DCA", "TECH_PIE"} and buy >= 20:
        return "DCA_WATCH"
    return "HOLD_OR_WAIT"


def compute_stop_levels(data: Dict[str, Any]) -> Dict[str, Any]:
    """Calculate simple risk levels from current price and moving averages."""
    p = data.get("price", {})
    price = p.get("price")
    if not price:
        return {"stop_loss": None, "take_profit": None, "invalidation": "N/A"}

    group = data.get("group", "")
    ma20 = p.get("ma20")
    ma50 = p.get("ma50")
    ma100 = p.get("ma100")

    if group == "SHORT_TERM_TRADING":
        # tighter stop for short-term ideas
        candidates = [price * 0.94]
        if ma20 and ma20 < price:
            candidates.append(ma20 * 0.985)
        stop = max(candidates)
        tp = price * 1.08
        invalid = f"short idea invalid below ~{stop:.2f}"
    else:
        # wider stop/invalidation for long-run ideas
        candidates = [price * 0.90]
        if ma50 and ma50 < price:
            candidates.append(ma50 * 0.97)
        elif ma100 and ma100 < price:
            candidates.append(ma100 * 0.97)
        stop = max(candidates)
        tp = price * 1.15
        invalid = f"long-run thesis review below ~{stop:.2f}"

    return {
        "stop_loss": round(stop, 2),
        "take_profit": round(tp, 2),
        "invalidation": invalid,
    }


def format_news_items(items: List[Dict[str, Any]], limit: int = 3) -> List[str]:
    """Compact bullet lines for markdown news sections."""
    lines = []
    for item in items[:limit]:
        title = (item.get("title") or "").strip()
        domain = item.get("source_domain") or extract_domain(item.get("url", ""))
        sentiment = item.get("sentiment")
        if not sentiment:
            sentiment, _ = classify_sentiment(title + " " + (item.get("snippet") or ""))
        source = f" ({domain})" if domain else ""
        lines.append(f"- {title}{source} — sentiment: {sentiment}")
    return lines






# ============================================================================
# MAIN EXECUTION
# ============================================================================

def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Portfolio AI Assistant - automatic scheduled portfolio reporting (read-only)."
    )
    ap.add_argument("--config", default="portfolio_config.json", help="Config file path")
    ap.add_argument("--scheduled", action="store_true",
                    help="Full automatic scheduled run (default pipeline, never prompts).")
    ap.add_argument("--no-ai", action="store_true",
                    help="Skip AI warmup and AI generation; deterministic reports only (AI status DISABLED).")
    ap.add_argument("--no-interactive", action="store_true",
                    help="Compatibility flag: the tool never prompts for input; always non-interactive.")
    ap.add_argument("--validate-only", action="store_true",
                    help="Offline config validation only (no network); exit 2 on errors.")
    ap.add_argument("--check-connectivity", action="store_true",
                    help="With --validate-only: also probe configured AI backends (uses network).")
    ap.add_argument("--migrate-config", action="store_true",
                    help="Migrate legacy config (timestamped backup first), validate output, then exit.")
    ap.add_argument("--debug", action="store_true",
                    help="DEBUG log level with phase timings and traces (secrets redacted).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Run collection/scoring/rendering in memory; publish no report files.")
    ap.add_argument("--group", help="Analyze only assets of this group (recorded in manifest).")
    ap.add_argument("--asset", help="Analyze only this asset (broker_symbol, case-insensitive).")
    ap.add_argument("--json-only", action="store_true",
                    help="Publish only JSON artifacts (no Markdown reports).")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    """Main entry point. Returns a RunExit code (see v4_hardening.RunExit)."""
    args = parse_args(argv)
    if args.debug:
        logger.setLevel(logging.DEBUG)
        for handler in logger.handlers:
            handler.setLevel(logging.DEBUG)

    run_id = V4.new_run_id()
    started_utc = datetime.now(timezone.utc)

    print("\n" + "=" * 70)
    print("[PORTFOLIO AI ASSISTANT - RELIABLE INVESTMENT MONITORING]")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Run ID: {run_id}")
    print("=" * 70 + "\n")
    logger.info("run_id=%s phase=startup status=START version=%s", run_id, V4.APP_VERSION)

    # === STEP 1: LOAD CONFIG (no network yet) ===
    print("[STEP 1] Loading configuration...")
    try:
        raw_config_text = Path(args.config).read_text(encoding="utf-8")
    except Exception as exc:
        print(f"[ERROR] Cannot read config '{args.config}': {exc}")
        logger.error("run_id=%s phase=config status=FAIL reason=unreadable", run_id)
        return int(V4.RunExit.CONFIG_ERROR)
    try:
        raw_config = json.loads(raw_config_text)
    except Exception as exc:
        print(f"[ERROR] Invalid JSON in '{args.config}': {exc}")
        logger.error("run_id=%s phase=config status=FAIL reason=invalid-json", run_id)
        return int(V4.RunExit.CONFIG_ERROR)

    try:
        config = migrate_legacy_config(raw_config)  # Auto-migrate in memory if needed
    except Exception as exc:
        print(f"[ERROR] Config migration failed: {V4.redact_secrets(exc)}")
        return int(V4.RunExit.CONFIG_ERROR)

    env = load_environment_variables()  # local only: reads api.env into os.environ
    errors, warnings = V4.validate_config_full(config, env=dict(os.environ))
    for warning in warnings:
        print(f"[CONFIG WARNING] {warning}")
        logger.warning("run_id=%s phase=config status=WARN detail=%s", run_id, warning)

    # === STEP 2: MIGRATION (files only, never combined with a data run) ===
    if args.migrate_config:
        return _run_migrate(args, raw_config_text, config, run_id)

    # === STEP 3: VALIDATION ONLY (offline unless --check-connectivity) ===
    if args.validate_only:
        return _run_validate(args, config, errors, warnings, run_id)

    if errors:
        print("[ERROR] Config validation failed:")
        for error in errors:
            print(f"  - {error}")
        logger.error("run_id=%s phase=config status=FAIL errors=%d", run_id, len(errors))
        return int(V4.RunExit.CONFIG_ERROR)
    print("[OK] Config valid")
    
    settings = config.get("settings", {})
    # Merge ai_backends from config root into settings for AI calls
    if "ai_backends" in config:
        settings["ai_backends"] = config["ai_backends"]
    assets = config.get("assets", [])
    portfolio_rules = config.get("portfolio_rules", {})

    # === SINGLE-RUN LOCK (no overlapping scheduled runs) ===
    runtime_dir = (settings.get("runtime_dir") or "runtime")
    run_lock = V4.RunLock(runtime_dir=runtime_dir)
    held, lock_info = run_lock.acquire(run_id)
    if not held:
        print(f"[SKIP] Another run is active ({lock_info.get('reason')}); not starting a second analysis.")
        logger.warning("run_id=%s phase=lock status=SKIP reason=%s", run_id, lock_info.get("reason"))
        return int(V4.RunExit.ALREADY_RUNNING)

    try:
        return _run_pipeline(args, config, settings, assets, portfolio_rules, env, run_id, started_utc)
    except Exception as exc:
        logger.exception("run_id=%s phase=run status=FAIL error=%s", run_id, V4.redact_secrets(exc))
        print(f"[FATAL] Unhandled error: {V4.redact_secrets(exc)}")
        print("No report files were published by this run.")
        return int(V4.RunExit.FATAL)
    finally:
        run_lock.release()


def _run_migrate(args, raw_config_text: str, config: Dict[str, Any], run_id: str) -> int:
    """Migrate legacy config: timestamped backup, migrated output, output validation."""
    src_path = Path(args.config)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_dir = src_path.parent / "config_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{src_path.stem}_{stamp}.bak.json"
    backup_path.write_text(raw_config_text, encoding="utf-8")
    print(f"[MIGRATION] Backup of original config: {backup_path.resolve()}")

    had_holdings = "holdings" in json.loads(raw_config_text) if raw_config_text.strip() else False
    n_before = len(json.loads(raw_config_text).get("assets", [])) if raw_config_text.strip() else 0
    migrated_path = src_path.parent / "portfolio.migrated.json"
    migrated_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[MIGRATION] Migrated config written: {migrated_path.resolve()}")
    print(f"[MIGRATION] Changes: legacy 'holdings' present={had_holdings}; "
          f"assets {n_before} -> {len(config.get('assets', []))}")

    out_errors, out_warnings = V4.validate_config_full(config, env=dict(os.environ))
    for warning in out_warnings:
        print(f"[CONFIG WARNING] {warning}")
    if out_errors:
        print("[MIGRATION ERROR] Migrated config is invalid:")
        for error in out_errors:
            print(f"  - {error}")
        logger.error("run_id=%s phase=migrate status=FAIL errors=%d", run_id, len(out_errors))
        return int(V4.RunExit.CONFIG_ERROR)
    print("[OK] Migrated config is valid")
    logger.info("run_id=%s phase=migrate status=OK backup=%s", run_id, backup_path.name)
    return int(V4.RunExit.SUCCESS)


def _run_validate(args, config: Dict[str, Any], errors: List[str],
                  warnings: List[str], run_id: str) -> int:
    """Offline validation (no network) + optional explicit connectivity probe."""
    print("[VALIDATE] Offline config validation (no network calls)...")
    if errors:
        print("[VALIDATE ERROR] Config is invalid:")
        for error in errors:
            print(f"  - {error}")
        logger.error("run_id=%s phase=validate status=FAIL errors=%d", run_id, len(errors))
        return int(V4.RunExit.CONFIG_ERROR)
    print("[OK] Config valid (schema, IDs, aliases, thresholds, timeouts, backend references)")

    if args.check_connectivity:
        print("[VALIDATE] Connectivity probe (explicitly requested)...")
        settings = config.get("settings", {})
        backends = settings.get("ai_backends", config.get("ai_backends", {}))
        chain = settings.get("ai_chain_alpha") or ["alpha", "fallback"]
        probed = 0
        for key in chain:
            cfg = backends.get(key, {})
            if not cfg or not cfg.get("enabled", True):
                continue
            ok = _probe_backend(cfg.get("base_url", ""), cfg.get("provider", "ollama"),
                                api_key=_resolve_env(cfg.get("api_key")))
            print(f"  - {key}: {'OK' if ok else 'UNREACHABLE'}")
            probed += 1
        if not probed:
            print("  (no enabled backends configured)")
    logger.info("run_id=%s phase=validate status=OK", run_id)
    return int(V4.RunExit.SUCCESS)


# ============================================================================
# V4 CANONICAL RESULT + REPORTS (one calculation -> JSON + MD + manifest)
# ============================================================================

def _is_tradable(data: Dict[str, Any]) -> bool:
    GOOD_DQ = ("OK", "PARTIAL", "SYMBOL_MISMATCH")
    return data.get("dq_status") in GOOD_DQ and bool((data.get("mapping") or {}).get("is_actionable", False))


def _validated_value(data: Dict[str, Any]) -> Optional[float]:
    """External market value ONLY when mapping is actionable and currency known."""
    mapping = data.get("mapping") or {}
    pr = data.get("price") or {}
    t212 = data.get("trading212") or {}
    if not mapping.get("is_actionable"):
        return None
    try:
        price = float(pr.get("price"))
        qty = float(t212.get("quantity"))
        if price <= 0 or not pr.get("currency"):
            return None
        return round(price * qty, 2)
    except (TypeError, ValueError):
        return None


def _display_value(data: Dict[str, Any]) -> str:
    """Broker-provided value first (authoritative, marked b); validated external (v); else n/a."""
    t212 = data.get("trading212") or {}
    if t212.get("market_value") is not None:
        try:
            return f"{float(t212['market_value']):,.2f} (b)"
        except (TypeError, ValueError):
            pass
    val = _validated_value(data)
    return f"{val:,.2f} (v)" if val is not None else "n/a"


def _asset_to_json(data: Dict[str, Any]) -> Dict[str, Any]:
    rec = data.get("recommendation") or {}
    s = data.get("summary") or {}
    pr = data.get("price") or {}
    t212 = data.get("trading212") or {}
    return {
        "ticker":             data.get("broker_symbol"),
        "name":               data.get("name"),
        "group":              data.get("group"),
        "current_price":      pr.get("price"),
        "t212_price":         t212.get("current_price"),
        "change_1d_pct":      pr.get("change_1d_pct"),
        "currency":           pr.get("currency"),
        "t212_currency":      t212.get("currencyCode") or t212.get("currency"),
        "data_quality":       rec.get("data_quality") or data.get("dq_status", "PARTIAL"),
        "data_quality_num":   data.get("data_quality", 0),
        "data_age_days":      data.get("data_age_days"),
        "tech_complete":      data.get("tech_complete", True),
        "action":             data.get("action"),
        "legacy_action":      data.get("legacy_action"),
        "blockers":           data.get("blockers", []),
        "action_reasons":     data.get("action_reasons", []),
        "ai_recommendation":  rec.get("recommendation", "WATCH"),  # legacy compat
        "score_value":        rec.get("score_value", 0),
        "score_momentum":     rec.get("score_momentum", 0),
        "score_sentiment":    rec.get("score_sentiment", 0),
        "total_score":        rec.get("total_score", 0),
        "comment":            rec.get("comment", ""),
        "buy_probability":    s.get("buy_probability", 0),
        "sell_probability":   s.get("sell_probability", 0),
        "technical_score":    s.get("technical_score", 0),
        "avg_sentiment":      rec.get("avg_sentiment"),
        "sentiment_fallback": bool(data.get("sentiment_fallback", False)),
        "rsi":                rec.get("rsi"),
        "news_count":         len(data.get("news", [])),
        "news":               data.get("news", []),
        "mapping":            data.get("mapping"),
        "levels":             data.get("levels"),
        "levels_reason":      data.get("levels_reason"),
        "broker_market_value": t212.get("market_value"),
        "validated_market_value": _validated_value(data),
        "t212_position": {
            "quantity":       t212.get("quantity"),
            "average_price":  t212.get("average_price"),
            "pnl":            t212.get("pnl"),
            "pnl_pct":        t212.get("pnl_pct"),
        } if t212 else None,
    }


def _build_result(*, run_id, started_utc, finished_utc, config, settings, args,
                  collected_data, tradable, blocked, broker_sync_status, broker_failed,
                  global_news, discovery_news, analysis_text, ai_status, timings):
    """Assemble the canonical result object. All outputs derive from this alone."""
    from collections import Counter
    map_status = Counter()
    map_method = Counter()
    dq_states = Counter()
    actions = Counter()
    yf_ok = yf_fail = 0
    news_total = 0
    for d in collected_data:
        m = d.get("mapping") or {}
        map_status[m.get("mapping_status", "?")] += 1
        map_method[m.get("mapping_method", "?")] += 1
        dq_states[d.get("dq_status", "?")] += 1
        actions[d.get("action", "?")] += 1
        if (d.get("price") or {}).get("price"):
            yf_ok += 1
        else:
            yf_fail += 1
        news_total += len(d.get("news", []))

    duration_s = round((finished_utc - started_utc).total_seconds(), 1)
    json_output = {
        "schema_version": V4.SCHEMA_VERSION,
        "app_version": V4.APP_VERSION,
        "run_id": run_id,
        "timestamp": finished_utc.isoformat(),
        "started_at": started_utc.isoformat(),
        "finished_at": finished_utc.isoformat(),
        "duration_seconds": duration_s,
        "timezone": V4.local_tzname(),
        "git_commit": V4.git_commit(),
        "config_hash": V4.config_hash(config),
        "config": {
            "model": (ai_status.get("served_by") or {}).get("model") if isinstance(ai_status.get("served_by"), dict) else settings.get("model"),
            "buy_threshold": settings.get("buy_probability_threshold", 45),
            "sell_threshold": settings.get("sell_probability_threshold", 55),
        },
        "execution": {
            "scheduled": bool(getattr(args, "scheduled", False)),
            "group": getattr(args, "group", None),
            "asset": getattr(args, "asset", None),
            "no_ai": bool(getattr(args, "no_ai", False)),
            "dry_run": bool(getattr(args, "dry_run", False)),
            "json_only": bool(getattr(args, "json_only", False)),
            "filtered": bool(getattr(args, "group", None) or getattr(args, "asset", None)),
        },
        "summary": {
            "broker_positions_loaded": broker_sync_status.get("positions_count", 0),
            "assets_analyzed": len(tradable),
            "broker_only_assets": len(blocked),
            "actions": dict(actions),
        },
        "mapping_summary": {
            "total": len(collected_data),
            "actionable": sum(1 for d in collected_data if (d.get("mapping") or {}).get("is_actionable", False)),
            "blocked": sum(1 for d in collected_data if not (d.get("mapping") or {}).get("is_actionable", False)),
            "by_status": dict(map_status),
            "by_method": dict(map_method),
        },
        "dq_summary": dict(dq_states),
        "providers": {
            "trading212": {
                "ok": bool(broker_sync_status.get("ok")),
                "failed": bool(broker_failed),
                "endpoint": broker_sync_status.get("endpoint_used"),
                "positions": broker_sync_status.get("positions_count", 0),
                "error": broker_sync_status.get("error"),
                "fetched_at": broker_sync_status.get("fetched_at"),
            },
            "yfinance": {"ok": yf_ok, "failed": yf_fail},
            "ddgs_news": {"per_ticker": news_total, "global": len(global_news),
                          "discovery": len(discovery_news),
                          "skipped_global": bool(settings.get("skip_global_news", False)),
                          "skipped_discovery": bool(settings.get("skip_discovery_news", False))},
            "ai": ai_status,
        },
        "timings": dict(timings),
        "actions": {
            "add_candidates":    [_asset_to_json(d) for d in tradable if d.get("action") == V4.ACTION_ADD],
            "reduce_candidates": [_asset_to_json(d) for d in tradable if d.get("action") == V4.ACTION_REDUCE],
            "hold":              [_asset_to_json(d) for d in tradable if d.get("action") == V4.ACTION_HOLD],
            "wait":              [_asset_to_json(d) for d in tradable if d.get("action") == V4.ACTION_WAIT],
            "review":            [_asset_to_json(d) for d in tradable if d.get("action") == V4.ACTION_REVIEW],
        },
        "analyzed_assets":   [_asset_to_json(d) for d in tradable],
        "broker_only_assets": [_asset_to_json(d) for d in blocked],
        "broker_sync_status": broker_sync_status,
        "assets": [_asset_to_json(d) for d in collected_data],  # backwards compat
        "ai_analysis": analysis_text,
    }
    return {
        "run_id": run_id,
        "json": V4.json_safe(json_output),
        "tradable": tradable,
        "blocked": blocked,
        "analysis_text": analysis_text,
        "ai_status": ai_status,
        "timings": dict(timings),
        "counts": {"actions": dict(actions), "dq": dict(dq_states),
                   "yf_ok": yf_ok, "yf_fail": yf_fail, "news_total": news_total},
        "news": {"global": global_news, "discovery": discovery_news},
        "broker_cash_ok": bool(broker_sync_status.get("account_total")),
    }


def _collect_warnings(result, *, ai_failed: bool, broker_failed: bool) -> List[str]:
    warns = []
    ms = result["json"]["mapping_summary"]
    if ms["blocked"]:
        warns.append(f"{ms['blocked']} asset(s) blocked by mapping/data gates (see reconciliation)")
    prov = result["json"]["providers"]
    if broker_failed:
        warns.append("Trading212 sync failed; broker data unavailable/stale")
    if not result["broker_cash_ok"] and prov["trading212"]["ok"]:
        warns.append("Broker cash totals unavailable")
    if prov["yfinance"]["failed"]:
        warns.append(f"{prov['yfinance']['failed']} asset(s) without Yahoo market data")
    if prov["ddgs_news"]["per_ticker"] == 0:
        warns.append("No per-ticker news collected")
    if ai_failed:
        warns.append("AI backends unavailable; deterministic scores only")
    stale = result["json"]["dq_summary"].get(V4.DQ_STALE, 0)
    if stale:
        warns.append(f"{stale} asset(s) with stale market data")
    return warns


def _attention_items(result, limit: int = 10) -> List[str]:
    """Highest-priority items first: mapping blocks, then market negatives."""
    items = []
    blocked = result["blocked"]
    for d in blocked:
        m = d.get("mapping") or {}
        if m.get("mapping_status") in (V4.STATUS_SUSPECT, V4.STATUS_CURRENCY, V4.STATUS_UNRESOLVED):
            sym = d.get("broker_symbol", "?")
            items.append(f"REVIEW MAPPING **{sym}** — {(m.get('mapping_reason') or '')[:140]}")
            if len(items) >= 4:
                break
    for d in result["tradable"]:
        if d.get("action") == V4.ACTION_REDUCE:
            s = d.get("summary") or {}
            items.append(f"REDUCE candidate **{d.get('broker_symbol')}** ({d.get('name', '')}) — "
                         f"sell {s.get('sell_probability', 0):.0f}% vs buy {s.get('buy_probability', 0):.0f}%")
            if len(items) >= 7:
                break
    for d in result["tradable"]:
        if d.get("action") == V4.ACTION_WAIT and (d.get("summary") or {}).get("rsi", 0) >= 70:
            items.append(f"Overbought **{d.get('broker_symbol')}** (RSI {(d.get('summary') or {}).get('rsi'):.1f}) — wait for entry")
            if len(items) >= 9:
                break
    for d in blocked:
        if (d.get("trading212") and d.get("action") == V4.ACTION_NO_DATA
                and len(items) < limit):
            items.append(f"No market data for holding **{d.get('broker_symbol')}** — broker-only, kept as-is")
    return items[:limit]


def _action_row(d: Dict[str, Any]) -> str:
    sym = d.get("broker_symbol", "?")
    name = str(d.get("name") or sym).replace("|", "/")[:28]
    action = d.get("action", "?")
    dq = d.get("data_quality") or d.get("dq_status") or 0
    reasons = d.get("action_reasons") or []
    reason = (reasons[0] if reasons else "")[:110].replace("|", "/")
    return f"| {sym} | {name} | {action} | {dq} | {reason} | {_display_value(d)} |"


def build_main_report(result) -> str:
    """Concise daily dashboard: health first, mapping problems before signals."""
    js = result["json"]
    smry = js["summary"]
    prov = js["providers"]
    t212 = prov["trading212"]
    lines = []
    ts = js.get("timestamp", "")[:16].replace("T", " ")
    lines.append("# Portfolio Report")
    lines.append(f"Generated: {ts} | Run: `{js['run_id']}` | Status: **{js.get('run_status', '?')}**")
    filt = js["execution"]
    if filt.get("filtered"):
        lines.append(f"_Filtered analysis (group={filt.get('group')}, asset={filt.get('asset')}) — "
                     f"not full-portfolio conclusions._")
    lines.append(f"_Data: market data {ts[:10]}; news last 48h; "
                 f"broker sync {t212.get('fetched_at', 'n/a')[:16] if t212.get('fetched_at') else 'n/a'}._")
    lines.append("")

    lines.append("## Portfolio snapshot")
    if t212.get("ok"):
        lines.append(f"- Broker sync: **OK** ({t212.get('endpoint', '?')})")
    else:
        lines.append(f"- Broker sync: **FAILED** — {t212.get('error') or 'unavailable'} (positions below are last-known/absent)")
    bs = js["broker_sync_status"]
    if bs.get("account_total"):
        lines.append(f"- Account value (broker): **{bs['account_total']:,.2f} EUR** | "
                     f"P&L: **{bs.get('account_pnl', 0):+,.2f} EUR** | Free cash: **{bs.get('cash_free', 0):,.2f} EUR**")
    else:
        lines.append("- Account value (broker): n/a")
    lines.append(f"- Positions loaded: {smry.get('broker_positions_loaded', 0)} | "
                 f"Validly analysed: {smry.get('assets_analyzed', 0)} | "
                 f"Blocked (mapping/data): {js['mapping_summary']['blocked']} | "
                 f"Broker-only/no-market-data: {smry.get('broker_only_assets', 0)}")
    acts = smry.get("actions", {})
    lines.append(f"- Actions: ADD {acts.get('ADD_CANDIDATE', 0)} | HOLD {acts.get('HOLD', 0)} | "
                 f"WAIT {acts.get('WAIT', 0)} | REVIEW {acts.get('REVIEW', 0)} | "
                 f"REDUCE {acts.get('REDUCE_CANDIDATE', 0)} | NO_DATA {acts.get('DATA_UNAVAILABLE', 0)} | "
                 f"MAPPING {acts.get('REVIEW_MAPPING', 0)}")
    lines.append("")

    lines.append("## Today's attention")
    attention = _attention_items(result)
    if attention:
        for item in attention:
            lines.append(f"- {item}")
    else:
        lines.append("- Nothing urgent: no mapping blocks, no reduce candidates, no overbought waits.")
    lines.append("")

    tradable = result["tradable"]
    blocked = result["blocked"]

    def _table(title, rows):
        lines.append(f"## {title}")
        if not rows:
            lines.append("- _none_")
            lines.append("")
            return
        lines.append("| Symbol | Name | Action | DQ | Reason | Value |")
        lines.append("|--------|------|--------|----|--------|-------|")
        lines.extend(rows)
        lines.append("")

    review_rows = [_action_row(d) for d in blocked
                   if d.get("action") in (V4.ACTION_MAPPING, V4.ACTION_NO_DATA)][:15]
    _table("Actions — review mapping / data issues", review_rows)
    add_rows = sorted(
        [d for d in tradable if d.get("action") == V4.ACTION_ADD],
        key=lambda d: ((d.get("summary") or {}).get("buy_probability", 0)
                       - (d.get("summary") or {}).get("sell_probability", 0)),
        reverse=True)
    _table("Actions — add candidates", [_action_row(d) for d in add_rows])
    hw_rows = ([d for d in tradable if d.get("action") == V4.ACTION_HOLD][:10]
               + [d for d in tradable if d.get("action") == V4.ACTION_WAIT][:10])
    _table("Actions — hold / wait", [_action_row(d) for d in hw_rows])
    rr_rows = ([d for d in tradable if d.get("action") == V4.ACTION_REDUCE]
               + [d for d in tradable if d.get("action") == V4.ACTION_REVIEW])
    _table("Actions — review / reduce candidates", [_action_row(d) for d in rr_rows])

    lines.append("## Broker reconciliation")
    ms = js["mapping_summary"]
    lines.append(f"- Actionable mappings: {ms['actionable']} | Blocked: {ms['blocked']}")
    lines.append(f"- By method: {', '.join(f'{k}={v}' for k, v in sorted(ms['by_method'].items()))}")
    lines.append(f"- By status: {', '.join(f'{k}={v}' for k, v in sorted(ms['by_status'].items()))}")
    suspect = [d.get("broker_symbol", "?") for d in blocked
               if (d.get("mapping") or {}).get("mapping_status") in
               (V4.STATUS_SUSPECT, V4.STATUS_CURRENCY, V4.STATUS_UNRESOLVED)][:12]
    if suspect:
        lines.append(f"- Suspect/unresolved: {', '.join(suspect)} (details in data_quality.md)")
    lines.append("")

    lines.append("## Provider and data notes")
    lines.append(f"- Yahoo Finance: {prov['yfinance']['ok']} ok / {prov['yfinance']['failed']} failed")
    dn = prov["ddgs_news"]
    lines.append(f"- News: {dn['per_ticker']} per-ticker / {dn['global']} global / {dn['discovery']} discovery "
                 f"(48h window)")
    ai = prov["ai"]
    if ai.get("mode") == "DISABLED":
        lines.append("- AI: DISABLED (--no-ai); scores are deterministic")
    elif ai.get("served_by"):
        sb = ai["served_by"]
        lines.append(f"- AI: {sb.get('backend')} / {sb.get('model')} ({sb.get('latency_s', '?')}s)")
    else:
        lines.append("- AI: unavailable this run; scores are deterministic")
    if js.get("warnings"):
        lines.append("- Warnings:")
        for warning in js["warnings"]:
            lines.append(f"  - {warning}")
    lines.append("")
    lines.append("_Analytical output for information only — not financial advice. "
                 "No orders are placed by this tool._")
    lines.append("")

    lines.append("## Appendix — all assets (compact)")
    lines.append("| Symbol | Group | Price | Buy% | Sell% | DQ | Action | Value |")
    lines.append("|--------|-------|-------|------|-------|----|--------|-------|")
    for d in sorted(collected_all(result), key=lambda x: (x.get("broker_symbol") or "?")):
        s = d.get("summary") or {}
        pr = d.get("price") or {}
        price = fmt(pr.get("price"), 2)
        lines.append(f"| {d.get('broker_symbol', '?')} | {(d.get('group') or '?')[:18]} | {price} | "
                     f"{s.get('buy_probability', 0):.0f} | {s.get('sell_probability', 0):.0f} | "
                     f"{d.get('data_quality', 0)} | {d.get('action', '?')} | {_display_value(d)} |")
    lines.append("")
    return "\n".join(lines)


def collected_all(result) -> list:
    return result["tradable"] + result["blocked"]


def build_dq_report(result) -> str:
    """Technical audit: providers, mappings, price comparisons, blocks."""
    js = result["json"]
    lines = []
    lines.append("# Data Quality Audit")
    lines.append(f"Run: `{js['run_id']}` | {js.get('timestamp', '')[:16].replace('T', ' ')}")
    lines.append("")
    lines.append("## Providers")
    prov = js["providers"]
    t212 = prov["trading212"]
    lines.append(f"- Trading212: {'OK' if t212.get('ok') else 'FAILED'} "
                 f"(endpoint={t212.get('endpoint')}, positions={t212.get('positions')}, "
                 f"error={t212.get('error') or 'none'})")
    lines.append(f"- Yahoo Finance: {prov['yfinance']['ok']} ok / {prov['yfinance']['failed']} failed")
    dn = prov["ddgs_news"]
    lines.append(f"- DDGS news: per-ticker={dn['per_ticker']} global={dn['global']} discovery={dn['discovery']} "
                 f"(skipped_global={dn['skipped_global']}, skipped_discovery={dn['skipped_discovery']})")
    ai = prov["ai"]
    lines.append(f"- AI: mode={ai.get('mode')}")
    for stage in ai.get("stages", []):
        lines.append(f"  - {stage.get('stage')}: {stage.get('backend')}/{stage.get('model')} "
                     f"ok={stage.get('ok')} {stage.get('latency_s', '?')}s {stage.get('error') or ''}")
    lines.append(f"- Timings (s): {', '.join(f'{k}={v}' for k, v in js.get('timings', {}).items())}")
    lines.append("")
    lines.append("## Mapping audit")
    ms = js["mapping_summary"]
    lines.append(f"- Actionable: {ms['actionable']} | Blocked: {ms['blocked']}")
    lines.append(f"- By method: {', '.join(f'{k}={v}' for k, v in sorted(ms['by_method'].items()))}")
    lines.append(f"- By status: {', '.join(f'{k}={v}' for k, v in sorted(ms['by_status'].items()))}")
    lines.append("")
    lines.append("### Suspect / unresolved mappings (broker value kept, external value NOT used)")
    shown = 0
    for d in result["blocked"]:
        m = d.get("mapping") or {}
        if m.get("mapping_status") not in (V4.STATUS_SUSPECT, V4.STATUS_CURRENCY, V4.STATUS_UNRESOLVED):
            continue
        t212pos = d.get("trading212") or {}
        pr = d.get("price") or {}
        lines.append(f"- **{d.get('broker_symbol', '?')}** [{m.get('mapping_method')}/{m.get('mapping_confidence')}] "
                     f"status={m.get('mapping_status')}")
        lines.append(f"  - reason: {m.get('mapping_reason')}")
        lines.append(f"  - broker price={t212pos.get('current_price')} {m.get('broker_currency') or '?'} vs "
                     f"market price={pr.get('price')} {m.get('market_currency') or '?'} "
                     f"(diff={m.get('price_difference_pct')}%, currency={m.get('currency_comparison_status')})")
        shown += 1
        if shown >= 20:
            break
    if not shown:
        lines.append("- none")
    lines.append("")
    lines.append("### Assets without market data")
    nodata = [d for d in result["blocked"] if not (d.get("price") or {}).get("price")]
    if nodata:
        for d in nodata[:20]:
            t212pos = d.get("trading212") or {}
            lines.append(f"- {d.get('broker_symbol', '?')} ({d.get('name', '')}): "
                         f"dq={d.get('dq_status')} action={d.get('action')} "
                         f"broker qty={t212pos.get('quantity')} market_value={t212pos.get('market_value')}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("### Stale / incomplete technicals")
    stale = [d for d in collected_all(result) if d.get("dq_status") == V4.DQ_STALE]
    incomp = [d for d in collected_all(result) if not d.get("tech_complete", True)]
    lines.append(f"- stale (>{result.get('stale_days', 7)}d): "
                 + (", ".join(d.get("broker_symbol", "?") for d in stale[:15]) or "none"))
    lines.append("- incomplete technicals (< RSI window): "
                 + (", ".join(d.get("broker_symbol", "?") for d in incomp[:15]) or "none"))
    lines.append("")
    lines.append("### Blocked from recommendations")
    for d in result["blocked"][:20]:
        lines.append(f"- {d.get('broker_symbol', '?')}: action={d.get('action')} "
                     f"blockers={'; '.join(d.get('blockers', [])) or 'n/a'}")
    lines.append("")
    if js.get("warnings"):
        lines.append("### Run warnings")
        for warning in js["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")
    return "\n".join(lines)


def _build_manifest(result, published: Dict[str, str]) -> Dict[str, Any]:
    js = result["json"]
    return V4.json_safe({
        "schema_version": V4.SCHEMA_VERSION,
        "app_version": V4.APP_VERSION,
        "run_id": js["run_id"],
        "exit_code": js.get("exit_code"),
        "run_status": js.get("run_status"),
        "started_at": js.get("started_at"),
        "finished_at": js.get("finished_at"),
        "duration_seconds": js.get("duration_seconds"),
        "timezone": js.get("timezone"),
        "git_commit": js.get("git_commit"),
        "config_hash": js.get("config_hash"),
        "execution": js.get("execution"),
        "counts": {"actions": js["summary"].get("actions", {}),
                   "mapping": js["mapping_summary"],
                   "dq": js["dq_summary"]},
        "providers": js.get("providers"),
        "timings": js.get("timings"),
        "warnings": js.get("warnings", []),
        "files": published,
        "backends": V4.sanitize_backends(
            (result.get("settings") or {}).get("ai_backends", {})),
    })


def publish_run(result, main_md: str, dq_md: str, manifest: Dict[str, Any],
                output_folder: str = "reports", json_only: bool = False) -> Dict[str, str]:
    """Atomically publish latest/ + archive/ + legacy compat copies. Returns {label: path}."""
    out = Path(output_folder)
    latest = out / "latest"
    day = datetime.now().strftime("%Y-%m-%d")
    rundir = out / "archive" / day / result["run_id"]
    published: Dict[str, str] = {}

    payloads = [("portfolio_report.json", V4.json_safe(result["json"]), True),
                ("run_manifest.json", manifest, True)]
    if not json_only:
        payloads[0:0] = [("portfolio_report.md", main_md, False),
                         ("data_quality.md", dq_md, False)]

    for name, content, is_json in payloads:
        if is_json:
            V4.atomic_write_json(latest / name, content)
            V4.atomic_write_json(rundir / name, content)
        else:
            V4.atomic_write_text(latest / name, content)
            V4.atomic_write_text(rundir / name, content)
        published[name] = str((latest / name).resolve())

    # Legacy compatibility copies (same content, old well-known names)
    V4.atomic_write_json(out / "portfolio_analysis.json", V4.json_safe(result["json"]))
    published["legacy JSON"] = str((out / "portfolio_analysis.json").resolve())
    if not json_only:
        V4.atomic_write_text(out / "portfolio_analysis.md", main_md)
        published["legacy Markdown"] = str((out / "portfolio_analysis.md").resolve())

    # Snapshot the run log next to the archive (best effort)
    try:
        latest_log = Path("logs") / "latest_run.log"
        if latest_log.exists():
            (rundir / "latest_run.log").write_bytes(latest_log.read_bytes())
    except OSError:
        pass
    return published


def _run_pipeline(args, config, settings, assets, portfolio_rules, env,
                  run_id: str, started_utc) -> int:
    """Full automatic pipeline. Returns a RunExit code. Lock held by caller."""
    broker_positions = {
        "trading212": [],
        "revolut": []
    }
    broker_sync_status = {
        "enabled": False,
        "ok": False,
        "positions_count": 0,
        "error": "disabled",
    }

    trading212_enabled = (
        bool(settings.get("use_trading212_api", False))
        or bool(env.get("trading212_enabled"))
    )

    timings: Dict[str, float] = {}
    t_broker0 = time.perf_counter()
    broker_failed = False
    if trading212_enabled and env.get("trading212_api_key"):
        print("[BROKER] Fetching Trading 212 positions...")
        with V4.phase(logger, run_id, "broker_sync") as stat:
            sync = fetch_trading212_with_retry(
                env["trading212_api_key"],
                api_secret=env.get("trading212_api_secret"),
                api_base=env.get("trading212_api_base", "https://live.trading212.com"),
            )
            broker_positions["trading212"] = sync.get("positions", [])
            broker_sync_status = sync.get("status", broker_sync_status)
            stat["positions"] = len(broker_positions["trading212"])
            stat["endpoint"] = broker_sync_status.get("endpoint_used")
            if broker_sync_status.get("ok"):
                print(f"[BROKER] Trading 212 positions loaded: {len(broker_positions['trading212'])}")

                # Authoritative EUR totals from the read-only cash API
                cash = fetch_trading212_cash(
                    env.get("trading212_api_key", ""),
                    api_secret=env.get("trading212_api_secret"),
                    api_base=env.get("trading212_api_base", "https://live.trading212.com"),
                )
                if cash.get("ok"):
                    broker_sync_status["account_total"] = cash["total"]
                    broker_sync_status["account_pnl"] = cash["result"]
                    broker_sync_status["cash_free"] = cash["free"]
                    print(f"[BROKER] Account: {cash['total']:.2f} EUR | "
                          f"P&L: {cash['result']:+.2f} EUR | "
                          f"Free cash: {cash['free']:.2f} EUR")
                    stat["account_total"] = round(cash["total"], 2)
                else:
                    print(f"[BROKER WARNING] Cash API fetch failed: {cash.get('error')}")
                    stat["cash_error"] = True
            else:
                broker_failed = True
                stat["status"] = "FAIL"
                stat["error"] = V4.redact_secrets(broker_sync_status.get("error"))
                print(f"[BROKER WARNING] Trading 212 sync failed: {broker_sync_status.get('error')}")
    else:
        print("[BROKER] Trading 212 disabled or API key missing")

    if env.get("revolut_file") and Path(env.get("revolut_file")).exists():
        print("[BROKER] Loading Revolut manual data...")
        try:
            broker_positions["revolut"] = load_revolut_data(env["revolut_file"])
            print(f"[BROKER] Revolut records loaded: {len(broker_positions['revolut'])}")
        except Exception as e:
            print(f"[BROKER WARNING] Revolut manual import failed: {e}")

    timings["broker_s"] = round(time.perf_counter() - t_broker0, 2)

    with V4.phase(logger, run_id, "mapping") as stat:
        assets = merge_broker_data(assets, broker_positions, symbol_aliases=config.get("symbol_aliases", {}))
        symbol_aliases = config.get("symbol_aliases", {})
        assets = auto_discover_unmatched_positions(assets, broker_positions, settings, symbol_aliases)
        audits = [a.get("mapping") for a in assets if isinstance(a.get("mapping"), dict)]
        stat["assets"] = len(assets)
        stat["audited"] = len(audits)

    # === STEP 4: AI WARMUP (background, non-blocking; skipped with --no-ai) ===
    # Probe + model preload runs in parallel with data collection below.
    # Offline endpoints fail fast (ai_probe_timeout); model load caps:
    # Ollama 5 min (ollama_load_timeout), LM Studio 15 min (lmstudio_load_timeout).
    if args.no_ai:
        ai_warmup = None
        print("[STEP 2] AI disabled (--no-ai): skipping warmup and AI generation.")
    else:
        print("[STEP 2] Starting AI warmup in background (LM Studio -> Ollama -> OpenRouter -> Gemini -> Mistral)...")
        ai_warmup = start_ai_warmup(settings)
        print("        Data collection continues meanwhile; AI step uses the warmed backend.")

    # === STEP 5: FILTER ASSETS ===
    print(f"\n[STEP 3] Loading assets ({len(assets)} total)...")
    if args.group:
        if not any((a.get("group") or "") == args.group for a in assets):
            print(f"[ERROR] Unknown group '{args.group}': no configured or discovered asset has this group.")
            logger.error("run_id=%s phase=filter status=FAIL reason=unknown-group", run_id)
            return int(V4.RunExit.CONFIG_ERROR)
        assets = [a for a in assets if a.get("group") == args.group]
        print(f"  Filtered to group '{args.group}': {len(assets)} assets")

    if args.asset:
        wanted = (args.asset or "").upper()
        if not any(((a.get("broker_symbol") or "").upper() == wanted) for a in assets):
            print(f"[ERROR] Unknown asset '{args.asset}': no configured or discovered asset matches.")
            logger.error("run_id=%s phase=filter status=FAIL reason=unknown-asset", run_id)
            return int(V4.RunExit.CONFIG_ERROR)
        assets = [a for a in assets if (a.get("broker_symbol") or "").upper() == wanted]
        print(f"  Filtered to asset '{args.asset}': {len(assets)} assets")
    
    if not assets:
        print("[ERROR] No assets to analyze")
        return
    
# === STEP 6: COLLECT DATA ===
    print(f"\n[STEP 4] Collecting data for {len(assets)} assets...")
    t_market0 = time.perf_counter()
    
    # Determine which assets get per-ticker news (priority: T212 holdings, then high-priority watchlist)
    news_limit = int(settings.get("max_news_per_asset_limit", 30))
    # Prioritize: T212 holdings first, then assets with BUY/SELL recommendation, then by data quality
    def asset_priority(a):
        has_t212 = bool(a.get("trading212"))
        is_auto = a.get("auto_discovered", False)
        rec = (a.get("recommendation") or {}).get("recommendation", "WATCH")
        dq = a.get("data_quality", 0)
        return (has_t212 and not is_auto, rec in ("BUY", "SELL"), dq)
    
    sorted_assets = sorted(assets, key=asset_priority, reverse=True)
    news_eligible = set(a.get("broker_symbol") for a in sorted_assets[:news_limit])
    
    collected_data = []
    for i, asset in enumerate(assets, 1):
        broker_sym = asset.get("broker_symbol", "?")
        # Only collect per-ticker news for priority assets
        asset_for_collection = asset.copy()
        if broker_sym not in news_eligible:
            asset_for_collection = asset.copy()
            asset_for_collection["_skip_news"] = True
        
        print(f"  [{i}/{len(assets)}] {broker_sym}...", end=" ", flush=True)
        try:
            # Collect market data
            market_data = collect_asset_data(asset_for_collection, settings)
            # If we skipped news, restore empty list
            if asset_for_collection.get("_skip_news"):
                market_data["news"] = []
            signals      = calculate_signals(market_data.get("price", {}), market_data.get("news", []), settings)
            data_quality = calculate_data_quality(market_data)  # also sets data["dq_status"]
            dq_str       = market_data.get("dq_status", "PARTIAL")

            # Start with original asset data, then overlay market data
            data = asset.copy()
            data.update(market_data)

            # --- V4: mapping audit + price gate inputs ---
            mapping = _mapping_of(asset)
            t212 = data.get("trading212") or {}
            if t212 and not mapping.broker_currency:
                mapping.broker_currency = t212.get("currencyCode") or t212.get("currency")

            # T212 internal codes bez Yahoo dát — SKIP scoring pipeline
            price_dict = data.get("price", {})
            price_val = price_dict.get("price") if isinstance(price_dict, dict) else price_dict
            if (data.get("auto_discovered")
                    and settings.get("skip_no_data_auto_discovered", True)
                    and data_quality < 20
                    and not price_val
                    and dq_str in ("NO_PRICE_DATA", "BROKER_ONLY")):
                if t212:
                    mapping.mapping_status = V4.STATUS_BROKER_ONLY
                    mapping.mapping_reason = ("Broker-only internal ticker, no Yahoo Finance market data; "
                                              "kept as broker-provided position only.")
                    mapping.is_actionable = False
                data["mapping"] = mapping.to_dict()
                data["signals"]     = signals
                data["data_quality"] = data_quality
                data["summary"]     = {"status": "NO_YAHOO_DATA", "label": "T212_INTERNAL_TICKER",
                                       "reason": "T212 internal ticker — not listed on Yahoo Finance"}
                data["recommendation"] = {
                    "recommendation": "WATCH",
                    "data_quality":   "BROKER_ONLY",
                    "total_score":    0, "score_value": 0, "score_momentum": 0, "score_sentiment": 0,
                    "comment":        "Žiadne cenové dáta (T212 interný kód). Nedokupovať.",
                }
                data["action"] = V4.ACTION_NO_DATA
                data["legacy_action"] = "WATCH"
                data["blockers"] = ["data:BROKER_ONLY"]
                data["action_reasons"] = ["No market data for broker-only ticker."]
                data["levels"] = None
                data["levels_reason"] = "blocked-data-quality"
                collected_data.append(data)
                print("[SKIP — T212 internal ticker, no Yahoo data]")
                continue

            # Crypto/iné tickery kde yfinance vrátil čiastočné/žiadne dáta
            price_dict = data.get("price", {})
            price_val = price_dict.get("price") if isinstance(price_dict, dict) else price_dict
            has_price = bool(price_val)
            if not has_price and dq_str == "NO_PRICE_DATA":
                log_suffix = "[OK_WITH_WARNINGS — no yfinance price, treated as WATCH only]"
            else:
                log_suffix = "[OK]"

            # --- V4: broker-vs-market price gate (SYNL-class protection) ---
            yahoo_ccy = price_dict.get("currency") if isinstance(price_dict, dict) else None
            if t212:
                mapping = V4.apply_price_gate(
                    mapping,
                    t212.get("current_price"), mapping.broker_currency,
                    price_val, yahoo_ccy,
                    warn_pct=float(settings.get("mapping_price_warn_pct", 15.0)),
                    block_pct=float(settings.get("mapping_price_block_pct", 35.0)),
                )
            # --- V4: mapping state overrides numeric data-quality state ---
            if mapping.mapping_status == V4.STATUS_SUSPECT:
                dq_str = V4.DQ_MAPPING_SUSPECT
            elif mapping.mapping_status == V4.STATUS_CURRENCY:
                dq_str = V4.DQ_CURRENCY
            elif mapping.mapping_status == V4.STATUS_UNRESOLVED and t212:
                dq_str = V4.DQ_UNRESOLVED
            elif t212 and not has_price and dq_str in ("NO_PRICE_DATA", "BROKER_ONLY"):
                dq_str = V4.DQ_BROKER_ONLY
            # --- V4: staleness gate (history end date vs today) ---
            bars = price_dict.get("history_bars") if isinstance(price_dict, dict) else None
            last_bar = price_dict.get("last_bar") if isinstance(price_dict, dict) else None
            min_bars = int(settings.get("min_history_bars", 14))
            tech_complete = (price_dict.get("rsi") is not None) and (bars or 0) >= min_bars
            stale_days = int(settings.get("stale_days", 7))
            if has_price and last_bar:
                try:
                    last_dt = datetime.fromisoformat(str(last_bar))
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    age_days = (datetime.now(timezone.utc) - last_dt).days
                    data["data_age_days"] = age_days
                    if age_days > stale_days:
                        dq_str = V4.DQ_STALE
                except Exception:
                    pass
            data["mapping"] = mapping.to_dict()
            data["dq_status"] = dq_str
            data["tech_complete"] = tech_complete

            summary = build_asset_summary(data, signals, data_quality, settings)
            data["signals"]      = signals
            data["data_quality"] = data_quality
            data["summary"]      = summary
            data["recommendation"] = calculate_recommendation(data, signals, settings)
            data["sentiment_fallback"] = not bool(data.get("news"))

            # --- V4: canonical action with hard gates (scores stay for JSON compat) ---
            rec = data["recommendation"] or {}
            summ = data["summary"] or {}
            final = V4.finalize_action(
                legacy=rec.get("recommendation", "WATCH"),
                buy_prob=summ.get("buy_probability", 50.0),
                sell_prob=summ.get("sell_probability", 50.0),
                tech_score=summ.get("technical_score", 50),
                rsi=summ.get("rsi"),
                has_price=has_price, price=price_val,
                tech_complete=tech_complete,
                mapping_status=mapping.mapping_status,
                mapping_reason=mapping.mapping_reason,
                dq_status=dq_str,
                buy_thr=float(settings.get("buy_probability_threshold", 45)),
                sell_thr=float(settings.get("sell_probability_threshold", 55)),
                overbought=float(settings.get("rsi_overbought", 70)),
                oversold=float(settings.get("rsi_oversold", 30)),
                add_margin=float(settings.get("action_add_margin", 5)),
                reduce_margin=float(settings.get("action_reduce_margin", 5)),
                tech_floor=float(settings.get("action_tech_floor", 45)),
                hold_band=float(settings.get("action_hold_band", 10)),
            )
            data["action"] = final["action"]
            data["legacy_action"] = final["legacy_action"]
            data["blockers"] = final["blockers"]
            data["action_reasons"] = final["reasons"]

            # --- V4: stop-loss / take-profit gate (informational only, never orders) ---
            lvl_ok, lvl_reason = V4.levels_allowed(
                group=data.get("group"), mapping_status=mapping.mapping_status,
                dq_status=dq_str, price=price_val, currency=yahoo_ccy)
            if lvl_ok:
                levels = compute_stop_levels(data)
                levels.update({
                    "method": "short-term-ma-percent",
                    "currency": yahoo_ccy,
                    "reference_price": price_val,
                    "as_of": datetime.now(timezone.utc).isoformat(),
                    "note": "Informational analysis only; not a brokerage instruction.",
                })
                data["levels"] = levels
                data["levels_reason"] = lvl_reason
            else:
                data["levels"] = None
                data["levels_reason"] = lvl_reason

            collected_data.append(data)
            print(log_suffix)
        except Exception as e:
            print(f"[ERROR: {e}]")

    # === STEP 5: SCORES & RECOMMENDATIONS (canonical V4 actions) ===
    timings["market_s"] = round(time.perf_counter() - t_market0, 2)
    def _act(label):
        return [d for d in collected_data if d.get("action") == label]
    n_add     = len(_act(V4.ACTION_ADD))
    n_hold    = len(_act(V4.ACTION_HOLD))
    n_wait    = len(_act(V4.ACTION_WAIT))
    n_review  = len(_act(V4.ACTION_REVIEW))
    n_reduce  = len(_act(V4.ACTION_REDUCE))
    n_nodata  = len(_act(V4.ACTION_NO_DATA))
    n_maprev  = len(_act(V4.ACTION_MAPPING))
    print(f"\n[STEP 5] Scores & recommendations...")
    print(f"  Assets: {len(collected_data)} | ADD: {n_add} | HOLD: {n_hold} | WAIT: {n_wait} | "
          f"REVIEW: {n_review} | REDUCE: {n_reduce} | NO_DATA: {n_nodata} | MAPPING: {n_maprev}")
    logger.info("run_id=%s phase=scoring status=OK assets=%d add=%d hold=%d wait=%d review=%d reduce=%d nodata=%d mapping=%d",
                run_id, len(collected_data), n_add, n_hold, n_wait, n_review, n_reduce, n_nodata, n_maprev)

    print(f"\n[STEP 6] Collecting broad market/discovery news...")
    t_news0 = time.perf_counter()
    with V4.phase(logger, run_id, "news") as stat:
        global_news    = [] if settings.get("skip_global_news", False) else collect_global_news(settings)
        discovery_news = [] if settings.get("skip_discovery_news", False) else collect_discovery_news(settings)
        trump_news     = {}   # Trump radar — add collect_trump_news() call here to enable
        stat["global"] = len(global_news)
        stat["discovery"] = len(discovery_news)
    timings["news_s"] = round(time.perf_counter() - t_news0, 2)
    print(f"  Global news: {len(global_news)} | Discovery ideas: {len(discovery_news)}")

    print(f"\n[STEP 7] Building canonical result...")
    tradable_ids = set()
    tradable = []
    for d in collected_data:
        if _is_tradable(d):
            tradable.append(d)
            tradable_ids.add(id(d))
    blocked = [d for d in collected_data if id(d) not in tradable_ids]

    # --- AI summary of validated facts only (non-fatal, never mutates scores) ---
    ai_trace: List[Dict[str, Any]] = []
    ai_failed = False
    if args.no_ai:
        analysis_text = "[AI analysis disabled]\n\nScores are calculated deterministically."
        ai_status = {"mode": "DISABLED", "stages": [], "failed": False,
                     "served_by": None, "note": "--no-ai flag"}
    else:
        print("  Calling AI for analysis (warmed backend first)...")
        t_ai0 = time.perf_counter()
        prompt = build_prompt(collected_data, portfolio_rules, settings)
        analysis_text = run_ai_pipeline(prompt, settings, warmup=ai_warmup, trace=ai_trace)
        timings["ai_s"] = round(time.perf_counter() - t_ai0, 2)
        if analysis_text.startswith("ERROR:"):
            ai_failed = True
            print(f"  [AI] {analysis_text}")
            analysis_text = ("[AI analysis unavailable - no backend reachable]\n\n"
                             "Scores are calculated deterministically.")
        served = next((t for t in ai_trace if t.get("ok")), None)
        ai_status = {"mode": "ENABLED", "stages": ai_trace, "failed": ai_failed,
                     "served_by": served}
    logger.info("run_id=%s phase=ai status=%s stages=%d",
                run_id, ai_status["mode"] if ai_failed is False and args.no_ai else ("FAIL" if ai_failed else "OK"),
                len(ai_trace))

    # --- canonical result object: every output derives from this alone ---
    finished_utc = datetime.now(timezone.utc)
    result = _build_result(
        run_id=run_id, started_utc=started_utc, finished_utc=finished_utc,
        config=config, settings=settings, args=args,
        collected_data=collected_data, tradable=tradable, blocked=blocked,
        broker_sync_status=broker_sync_status, broker_failed=broker_failed,
        global_news=global_news, discovery_news=discovery_news,
        analysis_text=analysis_text, ai_status=ai_status, timings=timings)
    result["stale_days"] = int(settings.get("stale_days", 7))
    result["settings"] = {"ai_backends": settings.get("ai_backends", {})}

    # --- exit code + run status (computed before publishing) ---
    run_warnings = _collect_warnings(result, ai_failed=ai_failed, broker_failed=broker_failed)
    if broker_failed:
        exit_code = int(V4.RunExit.BROKER_FAILED)
    elif run_warnings:
        exit_code = int(V4.RunExit.PARTIAL)
    else:
        exit_code = int(V4.RunExit.SUCCESS)
    result["json"]["exit_code"] = exit_code
    result["json"]["run_status"] = V4.run_status_for_exit(exit_code, bool(run_warnings))
    result["json"]["warnings"] = run_warnings
    result["exit_code"] = exit_code

    # --- render + publish (atomic) ---
    t_render0 = time.perf_counter()
    main_md = build_main_report(result)
    dq_md = build_dq_report(result)
    manifest = _build_manifest(result, {})
    if args.dry_run:
        print("\n[DRY RUN: no report files published] Collection, scoring and rendering ran in memory only.")
        logger.info("run_id=%s phase=publish status=SKIP reason=dry-run", run_id)
    else:
        print(f"\n[STEP 8] Publishing reports (atomic)...")
        with V4.phase(logger, run_id, "publish") as stat:
            published = publish_run(result, main_md, dq_md, manifest,
                                    output_folder=settings.get("output_folder", "reports"),
                                    json_only=args.json_only)
            manifest = _build_manifest(result, published)
            # Re-publish manifest with final file list (manifest itself is new information)
            out_folder = settings.get("output_folder", "reports")
            V4.atomic_write_json(Path(out_folder) / "latest" / "run_manifest.json", manifest)
            V4.atomic_write_json(
                Path(out_folder) / "archive" / datetime.now().strftime("%Y-%m-%d") / run_id / "run_manifest.json",
                manifest)
            stat["files"] = len(published)
        for label, path in published.items():
            print(f"[OK] {label}: {path}")
    timings["render_s"] = round(time.perf_counter() - t_render0, 2)

    # === SUMMARY ===
    smry = result["json"]["summary"]
    print("\n" + "=" * 70)
    print("[ANALYSIS COMPLETE]")
    print(f"Broker positions loaded:        {smry.get('broker_positions_loaded', 0)}")
    print(f"Assets analyzed (market data):  {smry.get('assets_analyzed', 0)}")
    print(f"Blocked (mapping/data):         {result['json']['mapping_summary']['blocked']}")
    acts = smry.get("actions", {})
    print(f"ADD: {acts.get('ADD_CANDIDATE', 0)} | HOLD: {acts.get('HOLD', 0)} | "
          f"WAIT: {acts.get('WAIT', 0)} | REVIEW: {acts.get('REVIEW', 0)} | "
          f"REDUCE: {acts.get('REDUCE_CANDIDATE', 0)} | NO_DATA: {acts.get('DATA_UNAVAILABLE', 0)} | "
          f"MAPPING: {acts.get('REVIEW_MAPPING', 0)}")
    print(f"Exit code: {exit_code} ({result['json']['run_status']})")
    print("=" * 70 + "\n")
    logger.info("run_id=%s final_status=%s exit_code=%d duration_s=%.1f",
                run_id, result["json"]["run_status"], exit_code,
                result["json"]["duration_seconds"])
    return exit_code


if __name__ == "__main__":
    sys.exit(main())