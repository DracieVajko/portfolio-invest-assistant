#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Portfolio AI Assistant - Reliable Investment Monitoring System

This tool analyzes a local investment portfolio with minimal hallucination risk.
It validates all external dependencies, data sources, and AI model availability
before generating reports. Designed for daily monitoring with local Ollama models.

Key features:
- Ollama model validation before use
- Broker symbol to Yahoo Finance mapping
- Data quality scoring (0-100)
- Deterministic probability calculations
- Sentiment analysis with keyword classification
- News relevance filtering
- Machine-readable JSON + human-readable Markdown output
"""

# --------------------------------------------------------------------
# Imports
# --------------------------------------------------------------------

import argparse
import json
import logging
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import numpy as np
import requests
import yfinance as yf

# python-dotenv is optional — graceful fallback if not installed
try:
    from dotenv import load_dotenv  # type: ignore
    _HAS_DOTENV = True
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore
    _HAS_DOTENV = False

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

# --------------------------------------------------------------------
# HTTP Session for yfinance (browser-like headers)
# --------------------------------------------------------------------

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
})
yf.set_tz_cache_location("data/cache")  # optional, for cache stability

# DuckDuckGo search - single import attempt
try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None


def ddg_search(query: str, max_results: int = 5, news: bool = False) -> List[Dict[str, Any]]:
    """Search DuckDuckGo for news or general results.
    
    Args:
        query: Search query
        max_results: Maximum number of results
        news: If True, search news; otherwise general web
        
    Returns:
        List of result dictionaries with keys: title, url, snippet, date
    """
    if not DDGS:
        return []
    try:
        results = []
        ddgs = DDGS()
        if news:
            gen = ddgs.news(query, max_results=max_results, region='us', safesearch='moderate')
        else:
            gen = ddgs.text(query, max_results=max_results, region='us', safesearch='moderate')
        
        for r in gen:
            # Handle different return formats from DDGS library
            if isinstance(r, dict):
                # Normalize keys
                result = {
                    'title': r.get('title') or r.get('headline') or '',
                    'url': r.get('url') or r.get('link') or '',
                    'snippet': r.get('body') or r.get('snippet') or r.get('description') or '',
                    'date': r.get('date') or r.get('published') or '',
                }
                if result['title'] and result['url']:
                    results.append(result)
            elif isinstance(r, (list, tuple)) and len(r) >= 2:
                # Handle tuple/list format (title, url, ...)
                results.append({
                    'title': str(r[0]),
                    'url': str(r[1]),
                    'snippet': str(r[2]) if len(r) > 2 else '',
                    'date': '',
                })
        return results
    except Exception as e:
        # DDGS library has known issues with unpacking, but often returns partial results
        # Only log at debug level to avoid noise
        logger.debug(f"ddg_search partial failure: {e}")
        return results if 'results' in locals() and results else []


# ============================================================================
# CONFIGURATION & VALIDATION
# ============================================================================

def load_config(path: str = "portfolio_config.json") -> Dict[str, Any]:
    """Load and validate portfolio configuration."""
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    return config




# --------------------------------------------------------------------
# Data Collection
# --------------------------------------------------------------------

# Simple in-memory cache for yfinance ticker data
_yf_cache = {}

def collect_asset_data(asset: Dict[str, Any], settings: Dict[str, Any] = None) -> Dict[str, Any]:
    """Retrieve price, basic info, technical indicators, and news for an asset.
    Returns a dict with keys: price (dict with current, MA, RSI), market_cap, volume, news, info.
    Missing values are None.
    """
    settings = settings or {}
    symbol = asset.get("yahoo_symbol") or asset.get("broker_symbol")
    if not symbol:
        return {}

    try:
        # Use cached ticker data if available
        cache_key = symbol.upper()
        if cache_key in _yf_cache:
            cached = _yf_cache[cache_key]
            ticker = cached['ticker']
            info = cached['info']
            hist = cached['hist']
        else:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            hist_period = settings.get("days_price_history", "3mo")
            hist = ticker.history(period=hist_period, interval="1d")
            _yf_cache[cache_key] = {'ticker': ticker, 'info': info, 'hist': hist}
        info = ticker.info

        # Current price
        price = info.get('regularMarketPrice')
        market_cap = info.get('marketCap')
        volume = info.get('volume')

        # Historical data for technical indicators
        hist_period = settings.get("days_price_history", "3mo")
        hist = ticker.history(period=hist_period, interval="1d")
        technicals = {}
        if not hist.empty:
            close = hist['Close']
            technicals['change_1d_pct'] = ((close.iloc[-1] / close.iloc[-2]) - 1) * 100 if len(close) > 1 else None
            technicals['change_5d_pct'] = ((close.iloc[-1] / close.iloc[-6]) - 1) * 100 if len(close) > 5 else None

            # Moving averages
            technicals['ma20'] = close.rolling(20).mean().iloc[-1] if len(close) >= 20 else None
            technicals['ma50'] = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
            technicals['ma200'] = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None

            # RSI (14)
            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            technicals['rsi'] = (100 - (100 / (1 + rs))).iloc[-1] if len(close) >= 14 else None

            # Trend hint
            if technicals.get('ma20') and technicals.get('ma50'):
                technicals['trend_hint'] = "UP" if technicals['ma20'] > technicals['ma50'] else "DOWN"
            else:
                technicals['trend_hint'] = "NEUTRAL"

            # Currency
            technicals['currency'] = info.get('currency', 'USD')

        # Return price as dict for compatibility with existing code
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
        }
        
        # Collect per-ticker news (2-5 items, last 48h)
        aliases = asset.get("aliases", []) + [asset.get("name", ""), symbol]
        ticker_news = collect_ticker_news(
            symbol, 
            aliases=aliases, 
            max_items=int(settings.get("max_news_per_asset", 5)),
            max_age_hours=int(settings.get("max_news_age_hours", 48))
        )
        
        return {
            'price': price_dict,
            'market_cap': market_cap,
            'volume': volume,
            'info': info,
            'technicals': technicals,
            'news': ticker_news
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
    elif _HAS_DOTENV and load_dotenv:
        try:
            load_dotenv(override=True)
        except Exception:
            pass

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
    }

    if not api_key:
        status["error"] = "Missing TRADING212_API_KEY"
        return {"positions": [], "status": status}

    base = (api_base or "https://live.trading212.com").rstrip("/")
    # Ensure we strip any /api/v0 suffix the user may have added
    if base.endswith("/api/v0"):
        base = base[: -len("/api/v0")]

    # T212 API v0 (current): HTTP Basic Auth = base64(api_key:api_secret)
    import base64
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
                return {"positions": [], "status": status}

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

    status["error"] = last_error or "Trading 212 API request failed"
    return {"positions": [], "status": status}


def load_revolut_data(file_path: str):
    """Parse Revolut positions from manual research file."""
    if not file_path or not Path(file_path).exists():
        return []
    
    positions = []
    file_path = Path(file_path)
    
    try:
        if file_path.suffix.lower() == ".json":
            import json
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


def fetch_tradingview_technical_data(symbol: str):
    """Lightweight public TradingView availability check (no login, no scraping private data)."""
    if not symbol:
        return {"source": "TradingView", "symbol": symbol, "data_available": False}

    # TradingView public symbol pages usually use EXCHANGE-SYMBOL.
    tv_slug = symbol.replace(":", "-")
    url = f"https://www.tradingview.com/symbols/{tv_slug}/"
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=8)
        return {
            "source": "TradingView",
            "symbol": symbol,
            "url": url,
            "trust_level": "LOW",
            "data_available": response.status_code == 200,
            "status_code": response.status_code,
        }
    except Exception as e:
        return {"source": "TradingView", "symbol": symbol, "url": url, "trust_level": "LOW", "error": str(e)}


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
        })

    if discovered:
        print(f"[BROKER] Auto-discovered {len(discovered)} T212 position(s) not in config: "
              f"{', '.join(d['broker_symbol'] for d in discovered)}")

    return assets + discovered


def merge_broker_data(assets, broker_positions):
    """Merge broker position data with portfolio assets."""
    enhanced_assets = []
    
    # Build lookup maps for T212 positions
    t212_by_clean = {}
    t212_by_raw = {}
    for pos in broker_positions.get("trading212", []):
        clean = (pos.get("broker_symbol_clean") or "")
        raw = (pos.get("broker_symbol") or "")
        if clean:
            t212_by_clean[clean.upper()] = pos
        if raw:
            t212_by_raw[raw.upper()] = pos
    
    for asset in assets:
        broker_symbol = (asset.get("broker_symbol", "") or "").upper()
        enhanced = asset.copy()
        
        # Try exact match on normalized ticker first
        if broker_symbol in t212_by_clean:
            enhanced["trading212"] = t212_by_clean[broker_symbol]
        elif broker_symbol in t212_by_raw:
            enhanced["trading212"] = t212_by_raw[broker_symbol]
        else:
            # Try prefix match for edge cases
            for raw_sym, pos in t212_by_raw.items():
                if raw_sym.startswith(broker_symbol + "_"):
                    enhanced["trading212"] = pos
                    break
        
        # Revolut
        for pos in broker_positions.get("revolut", []):
            if (pos.get("broker_symbol", "") or "").upper() == broker_symbol:
                enhanced["revolut"] = pos
                break
        
        enhanced_assets.append(enhanced)
    
    return enhanced_assets


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


def validate_config(config: Dict[str, Any]) -> List[str]:
    """Validate config schema. Returns list of errors (empty if valid)."""
    errors = []
    
    if "settings" not in config:
        errors.append("Missing 'settings' section")
    
    if "assets" not in config:
        errors.append("Missing 'assets' section. Use --migrate-config to convert old format.")
    else:
        assets = config["assets"]
        if not isinstance(assets, list):
            errors.append("'assets' must be an array")
        
        seen_symbols = set()
        for idx, asset in enumerate(assets):
            if "broker_symbol" not in asset:
                errors.append(f"Asset {idx} missing 'broker_symbol'")
            elif asset["broker_symbol"] in seen_symbols:
                errors.append(f"Duplicate broker_symbol: {asset['broker_symbol']}")
            else:
                seen_symbols.add(asset["broker_symbol"])
            
            if "yahoo_symbol" not in asset:
                errors.append(f"Asset {idx} missing 'yahoo_symbol'")
            
            if "name" not in asset:
                errors.append(f"Asset {idx} missing 'name'")
    
    return errors


def safe_float(x):
    """Convert to float safely, return None if invalid."""
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None


def pct(a, b):
    """Calculate percentage change from b to a."""
    if a is None or b is None or b == 0:
        return None
    return (a / b - 1) * 100


def fmt(v, digits=2, suffix=""):
    """Format float to string."""
    if v is None:
        return "N/A"
    return f"{v:.{digits}f}{suffix}"


def recommendation_label(asset: Dict[str, Any]) -> str:
    rec = asset.get("recommendation") or {}
    label = (rec.get("recommendation") or "WATCH").upper()
    return label if label in {"BUY", "SELL", "WATCH"} else "WATCH"


def data_quality_reason(asset: Dict[str, Any]) -> str:
    reason = asset.get("dq_status") or asset.get("data_quality_reason") or "UNKNOWN"
    price = asset.get("price") or {}
    mismatch = asset.get("symbol_mismatch") or {}
    if mismatch.get("flag"):
        return f"SYMBOL_MISMATCH ({mismatch.get('ratio_text','n/a')})"
    if price.get("error"):
        return str(price.get("error"))
    return str(reason)


def detect_symbol_mismatch(asset: Dict[str, Any], ratio_limit: float = 3.0) -> Dict[str, Any]:
    t = asset.get("trading212") or {}
    p = asset.get("price") or {}
    t_price = safe_float(t.get("current_price"))
    y_price = safe_float(p.get("price"))
    out = {"flag": False, "ratio": None, "ratio_text": None}
    if not t_price or not y_price or t_price <= 0 or y_price <= 0:
        return out
    ratio = max(t_price / y_price, y_price / t_price)
    out["ratio"] = ratio
    out["ratio_text"] = f"{ratio:.2f}x"
    if ratio > ratio_limit:
        out["flag"] = True
    return out


def resolve_yahoo_symbol(asset: Dict[str, Any], settings: Dict[str, Any]) -> str:
    aliases = settings.get("symbol_aliases", {}) or {}
    broker_symbol = (asset.get("broker_symbol") or "").upper()
    current = asset.get("yahoo_symbol") or broker_symbol
    mapped = aliases.get(broker_symbol)
    if mapped:
        asset["yahoo_symbol"] = mapped
        return mapped
    return current


# ============================================================================
# OLLAMA/MODEL MANAGEMENT
# ============================================================================

def _call_provider(base_url: str, provider: str, model: str,
                   messages: list, temperature: float,
                   num_ctx: int, max_tokens: int, timeout: int) -> str:
    if provider == "lmstudio":
        payload = {"model": model, "messages": messages, "temperature": temperature,
                   "max_tokens": max_tokens or num_ctx, "stream": False}
        r = requests.post(base_url.rstrip("/") + "/chat/completions", json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    else:
        payload = {"model": model, "stream": False, "messages": messages,
                   "options": {"temperature": temperature, "num_ctx": num_ctx}}
        r = requests.post(base_url.rstrip("/") + "/api/chat", json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "").strip()


def _probe_backend(base_url: str, provider: str, timeout: int = 5) -> bool:
    try:
        ep = "/models" if provider == "lmstudio" else "/api/tags"
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


def call_stage(stage: str, system_prompt: str, user_prompt: str,
               settings: Dict[str, Any]) -> str:
    """Route prompt to correct backend for the given stage.
    ai_backends config: alpha / summary / fallback
    Each entry: {provider, base_url, model, timeout, num_ctx, temperature, enabled}
    """
    backends = settings.get("ai_backends", {})
    candidates = []
    for key in (stage, "fallback"):
        cfg = backends.get(key, {})
        if cfg and cfg.get("enabled", True): candidates.append(cfg)
    
    if not candidates:
        return f"ERROR: No enabled backends configured for stage '{stage}' or fallback."

    messages = [{"role":"system","content":system_prompt},{"role":"user","content":user_prompt}]

    for cfg in candidates:
        base_url = cfg.get("base_url",""); provider = cfg.get("provider","ollama")
        model = cfg.get("model",""); timeout = int(cfg.get("timeout",3600))
        num_ctx = int(cfg.get("num_ctx",8192)); temp = float(cfg.get("temperature",0.08))
        max_tok = int(cfg.get("max_tokens", num_ctx // 2))
        if not base_url or not model: continue
        if not _probe_backend(base_url, provider):
            print(f"  [AI] {provider.upper()} {base_url} unreachable — next"); continue
        print(f"  [AI] {stage.upper()} -> {provider.upper()} | {model[:40]} | ctx={num_ctx}")
        try:
            return _call_provider(base_url, provider, model, messages, temp, num_ctx, max_tok, timeout)
        except Exception as e:
            print(f"  [AI] {stage} error ({e}) — next backend")
    return f"ERROR: All backends failed for stage '{stage}'."


def build_ollama_url(settings: Dict[str, Any], endpoint: str = "chat") -> str:
    lms = settings.get("lm_studio_base_url","").strip()
    if lms: return lms.rstrip("/") + ("/chat/completions" if endpoint=="chat" else "/models")
    base = settings.get("ollama_base_url","http://127.0.0.1:11434").rstrip("/")
    return base + ("/api/chat" if endpoint=="chat" else "/api/tags")


def _detect_backend(settings: Dict[str, Any]) -> str:
    lms = settings.get("lm_studio_base_url","").strip()
    return "lmstudio" if lms and _probe_backend(lms,"lmstudio") else "ollama"


def list_lmstudio_models(settings: Dict[str, Any]) -> Optional[List[str]]:
    lms = settings.get("lm_studio_base_url","").strip()
    if lms and _probe_backend(lms, "lmstudio"):
        return _get_backend_models(lms, "lmstudio")
    return None


def check_lmstudio_available(settings: Dict[str, Any]) -> Tuple[bool, str]:
    lms = settings.get("lm_studio_base_url","").strip()
    if not lms:
        return False, "LM Studio base URL not configured"
    if _probe_backend(lms, "lmstudio"):
        return True, f"LM Studio OK at {lms}"
    return False, f"LM Studio unreachable at {lms}"


def get_working_model(settings: Dict[str, Any]) -> Optional[str]:
    for k in ("model","fallback_model"):
        m = settings.get(k,"")
        if m and validate_model_available(settings, m): return m
    models = list_lmstudio_models(settings)
    return models[0] if models else None


def check_ollama_available(settings: Dict[str, Any]) -> Tuple[bool, str]:
    # Kept for backward compatibility
    return check_lmstudio_available(settings)


def list_ollama_models(settings: Dict[str, Any]) -> Optional[List[str]]:
    # Kept for backward compatibility
    return list_lmstudio_models(settings)


def validate_model_available(settings: Dict[str, Any], model_name: str) -> bool:
    return any(model_name in m for m in (list_ollama_models(settings) or []))


def check_ollama_available(settings: Dict[str, Any]) -> Tuple[bool, str]:
    p = _detect_backend(settings)
    base = settings.get("lm_studio_base_url") if p=="lmstudio" else settings.get("ollama_base_url","http://127.0.0.1:11434")
    if _probe_backend(base or "", p): return True, f"{p.upper()} OK at {base}"
    return False, f"{p.upper()} unreachable at {base}"


def get_working_model(settings: Dict[str, Any]) -> Optional[str]:
    for k in ("model","fallback_model"):
        m = settings.get(k,"")
        if m and validate_model_available(settings, m): return m
    models = list_ollama_models(settings)
    return models[0] if models else None


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
                    use_summary: bool = True) -> str:
    """Alpha -> optional Summary stage on Raspi."""
    alpha_out = call_stage("alpha", _SYSTEM_ALPHA, prompt, settings)
    if alpha_out.startswith("ERROR:") or not use_summary:
        return alpha_out
    summary_out = call_stage("summary", _SYSTEM_SUMMARY,
                             f"Compress this analysis:\n\n{alpha_out}", settings)
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


from datetime import timedelta
import threading
import queue

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
            for item in DDGS().news(query, max_results=5, region='us', safesearch='moderate'):
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
            for item in DDGS().news(query, max_results=5, region='us', safesearch='moderate'):
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
    technicals = price_data.get("technicals", {}) if isinstance(price_data, dict) else {}
    info = price_data.get("info", {}) if isinstance(price_data, dict) else {}
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
        "action_label": "",  # filled by action_label_for_asset later
        "label": status,
        
        # Additional
        "news_count": len(news),
        "market_cap": info.get("marketCap"),
        "volume": price_data.get("volume"),
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
    info = price_data.get("info", {}) if isinstance(price_data, dict) else {}
    technicals = price_data.get("technicals", {}) if isinstance(price_data, dict) else {}
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


def generate_report(collected_data: List[Dict[str, Any]], analysis_text: str, settings: Dict[str, Any],
                    global_news: Optional[List[Dict[str, Any]]] = None,
                    discovery_news: Optional[List[Dict[str, Any]]] = None,
                    broker_status: Optional[Dict[str, Any]] = None,
                    trump_news: Optional[Dict[str, Any]] = None) -> str:
    """Generate final Markdown report.
    Strict split: tradable assets with market data vs broker_only assets.
    """
    global_news    = global_news    or []
    discovery_news = discovery_news or []
    broker_status  = broker_status  or {}
    trump_news     = trump_news     or {}
    lines          = []

    # ----------------------------------------------------------------
    # Split data
    # ----------------------------------------------------------------
    GOOD_DQ = ("OK", "PARTIAL", "SYMBOL_MISMATCH")
    tradable     = [d for d in collected_data if d.get("dq_status") in GOOD_DQ]
    broker_only  = [d for d in collected_data if d.get("dq_status") not in GOOD_DQ]

    buy_list   = [d for d in tradable if d.get("recommendation", {}).get("recommendation") == "BUY"]
    sell_list  = [d for d in tradable if d.get("recommendation", {}).get("recommendation") == "SELL"]
    watch_list = [d for d in tradable if d.get("recommendation", {}).get("recommendation") == "WATCH"]
    buy_strict  = [d for d in tradable if d.get("summary", {}).get("status") == "BUY_CANDIDATE"]
    sell_strict = [d for d in tradable if d.get("summary", {}).get("status") == "SELL_CANDIDATE"]

    # ----------------------------------------------------------------
    # Header
    # ----------------------------------------------------------------
    lines.append(f"# Portfolio Analysis Report")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Data Sources: yfinance, DuckDuckGo news search, optional TradingView public checks, "
                 f"optional Trading 212 read-only API")
    lines.append("")

    # ----------------------------------------------------------------
    # Broker Sync Status
    # ----------------------------------------------------------------
    lines.append("## Broker Sync Status")
    t212_ok = broker_status.get("ok", False)
    lines.append(f"- Trading 212: **{'OK' if t212_ok else 'FAILED'}**")
    if t212_ok:
        lines.append(f"- Endpoint used: `/api/v0/equity/portfolio`")
        lines.append(f"- Positions loaded: {broker_status.get('positions_count', 0)}")
        if broker_status.get("account_total"):
            lines.append(f"- Celková hodnota účtu (T212 cash API): **{broker_status['account_total']:,.2f} EUR**")
            lines.append(f"- P&L: {broker_status.get('account_pnl', 0):+,.2f} EUR | "
                         f"Voľná hotovosť: {broker_status.get('cash_free', 0):,.2f} EUR")
    else:
        lines.append(f"- Error: {broker_status.get('error', 'unknown')}")
    lines.append("")

    # ----------------------------------------------------------------
    # Summary
    # ----------------------------------------------------------------
    lines.append("## Summary")
    lines.append(f"- Total broker positions loaded: {broker_status.get('positions_count', 0)}")
    lines.append(f"- Assets analyzed with market data: **{len(tradable)}**")
    lines.append(f"- Broker-only / low-data assets: {len(broker_only)}")
    lines.append(f"- **BUY candidates (soft): {len(buy_list)}**")
    lines.append(f"- **SELL candidates (soft): {len(sell_list)}**")
    lines.append(f"- WATCH candidates: {len(watch_list)}")
    lines.append(f"- Strong BUY (strict Python): {len(buy_strict)}")
    lines.append(f"- Strong SELL (strict Python): {len(sell_strict)}")
    lines.append("- Note: soft BUY/SELL = AI score-based; strict = Python probability threshold.")
    lines.append("")

    # ----------------------------------------------------------------
    # Moje pozície (Trading212)
    # ----------------------------------------------------------------
    lines.append("## Moje pozície (Trading212)")
    positions_shown = [d for d in tradable if d.get("trading212")]
    broker_only_pos = [d for d in broker_only if d.get("trading212")]

    if positions_shown:
        total_value = 0.0; total_pnl = 0.0
        lines.append("| Symbol | Názov | Qty | Avg cena | T212 cena | Yahoo cena | Hodnota | P&L | P&L % | Rec |")
        lines.append("|--------|-------|-----|----------|-----------|------------|---------|-----|-------|-----|")
        for d in sorted(positions_shown,
                        key=lambda x: (x.get("trading212") or {}).get("current_price", 0)
                                      * (x.get("trading212") or {}).get("quantity", 0),
                        reverse=True):
            t       = d.get("trading212") or {}
            sym     = d.get("broker_symbol", "?")
            dname   = (d.get("name") or sym)[:20]
            qty     = t.get("quantity")
            avg_p   = t.get("average_price")
            t212_p  = t.get("current_price")
            yahoo_p = (d.get("price") or {}).get("price")
            live_p  = yahoo_p if yahoo_p else None
            val     = (live_p * qty) if (live_p and qty) else None
            pnl     = t.get("pnl")
            pnl_pct = t.get("pnl_pct")
            rec     = recommendation_label(d)
            if val: total_value += val
            if pnl: total_pnl  += pnl
            lines.append(
                f"| {sym} | {dname} | {fmt(qty,4)} | {fmt(avg_p)} "
                f"| {fmt(t212_p)} | {fmt(yahoo_p) if yahoo_p else 'N/A'} "
                f"| {fmt(val) if val else 'N/A'} | {fmt(pnl)} "
                f"| {fmt(pnl_pct,1,'%')} | {rec} |"
            )
        lines.append("")
        t212_total = broker_status.get("account_total", 0)
        if t212_total > 0:
            pnl_result = broker_status.get("account_pnl", 0)
            lines.append(f"**Účet T212 (cash API):** Celková hodnota = **{t212_total:,.2f} EUR** | "
                         f"P&L = **{pnl_result:+,.2f} EUR**")
        else:
            lines.append(f"**Spolu (Yahoo-resolveble):** Hodnota ≈ {total_value:,.2f} EUR | "
                         f"P&L = {total_pnl:+,.2f} EUR")
        if broker_only_pos:
            lines.append(f"⚠️ {len(broker_only_pos)} ďalších T212 pozícií nemá Yahoo dáta "
                         f"(T212 interné kódy — pozri sekciu Broker-only nižšie).")
        lines.append("")

    # ----------------------------------------------------------------
    # Unresolved tickers / broker-only
    # ----------------------------------------------------------------
    if broker_only:
        lines.append("## Unresolved tickers")
        lines.append("| Symbol | Name | DQ | Reason |")
        lines.append("|--------|------|----|--------|")
        for d in broker_only:
            sym = d.get("broker_symbol") or "?"
            name = (d.get("name") or sym).replace("|", "/")
            dq = d.get("data_quality") or d.get("data_quality_score") or 0
            reason = data_quality_reason(d).replace("|", "/")
            lines.append(f"| {sym} | {name} | {dq} | {reason} |")
        lines.append("")

    # ----------------------------------------------------------------
    # Main Asset Scores Table (iba tradable)
    # ----------------------------------------------------------------
    lines.append("## Asset Scores Table")
    lines.append("| Symbol | Name | Group | Price | 1D | Tech | Sent | Buy% | Sell% | DQ | Rec | Total |")
    lines.append("|--------|------|-------|-------|----|------|------|------|-------|----|-----|-------|")
    for d in tradable:
        s   = d.get("summary") or {}
        rec = d.get("recommendation") or {}
        sym    = d.get("broker_symbol") or s.get("broker_symbol") or "?"
        name   = (d.get("name") or s.get("name") or sym or "?").replace("|", "/")
        grp    = (d.get("group") or "?").replace("T212_AUTO_DISCOVERED", "T212 HOLDING").replace("|", "/")
        price  = fmt(s.get("current_price") or (d.get("price") or {}).get("price"), 2)
        ch1d   = fmt(s.get("change_1d_pct") or (d.get("price") or {}).get("change_1d_pct"), 1, "%")
        tech   = s.get("technical_score", 0)
        sent   = s.get("sentiment_score", 0)
        bp     = s.get("buy_probability", 0)
        sp     = s.get("sell_probability", 0)
        dq     = s.get("data_quality_score") or d.get("data_quality") or 0
        label  = rec.get("recommendation") or s.get("action_label") or s.get("status") or "WATCH"
        total  = rec.get("total_score", "")
        lines.append(f"| {sym} | {name} | {grp} | {price} | {ch1d} | {tech} | {sent} | "
                     f"{bp:.0f} | {sp:.0f} | {dq} | {label} | {total} |")
    lines.append("")

    # ----------------------------------------------------------------
    # BUY / SELL / WATCH sections
    # ----------------------------------------------------------------
    if buy_list:
        lines.append("## BUY Candidates")
        for d in sorted(buy_list, key=lambda x: (x.get("recommendation") or {}).get("total_score", 0), reverse=True):
            rec = d.get("recommendation") or {}
            sym = d.get("broker_symbol", "?")
            nm  = d.get("name", "") or sym
            sv, sm, ss, tot = rec.get("score_value",0), rec.get("score_momentum",0), rec.get("score_sentiment",0), rec.get("total_score",0)
            lines.append(f"- **{sym}** ({nm}) | Score: V{sv} M{sm} S{ss} = **{tot:+d}** | {rec.get('comment','')[:150]}")
        lines.append("")

    if sell_list:
        lines.append("## SELL Candidates")
        for d in sorted(sell_list, key=lambda x: (x.get("recommendation") or {}).get("total_score", 0)):
            rec = d.get("recommendation") or {}
            sym = d.get("broker_symbol", "?")
            nm  = d.get("name", "") or sym
            sv, sm, ss, tot = rec.get("score_value",0), rec.get("score_momentum",0), rec.get("score_sentiment",0), rec.get("total_score",0)
            lines.append(f"- **{sym}** ({nm}) | Score: V{sv} M{sm} S{ss} = **{tot:+d}** | {rec.get('comment','')[:150]}")
        lines.append("")

    if watch_list:
        lines.append("## WATCH — Top pozície vyžadujúce pozornosť")
        t212_watch = [d for d in watch_list if d.get("trading212")]
        show = sorted(t212_watch, key=lambda d: abs((d.get("recommendation") or {}).get("total_score", 0)), reverse=True)[:15]
        for d in show:
            rec = d.get("recommendation") or {}
            sym = d.get("broker_symbol", "?")
            nm  = d.get("name", "") or sym
            lines.append(f"- **{sym}** ({nm}) | Score: {rec.get('total_score',0):+d} | {rec.get('comment','')[:120]}")
        lines.append("")

    # ----------------------------------------------------------------
    # Broker-only / low-data assets
    # ----------------------------------------------------------------
    if broker_only:
        lines.append("## Broker-only / Low-data Assets")
        lines.append("Tieto symboly sú T212 interné kódy bez Yahoo Finance dát. "
                     "Nemôžu byť hodnotené — slúžia len na evidenciu pozícií.")
        lines.append("")
        lines.append("| Symbol | Qty | Avg cena | T212 cena | P&L | P&L % | DQ Status |")
        lines.append("|--------|-----|----------|-----------|-----|-------|-----------|")
        for d in sorted(broker_only,
                        key=lambda x: abs((x.get("trading212") or {}).get("pnl") or 0),
                        reverse=True):
            t   = d.get("trading212") or {}
            sym = d.get("broker_symbol", "?")
            qty = t.get("quantity"); avg = t.get("average_price")
            cp  = t.get("current_price"); pnl = t.get("pnl"); pct = t.get("pnl_pct")
            dqs = d.get("dq_status", "?")
            sign = "+" if (pnl or 0) >= 0 else ""
            pct_s = f"{sign}{pct:.1f}%" if pct is not None else "?"
            lines.append(f"| {sym} | {fmt(qty,4)} | {fmt(avg)} | {fmt(cp)} | "
                         f"{sign}{fmt(pnl)} | {pct_s} | {dqs} |")
        lines.append("")

    # ----------------------------------------------------------------
    # Market radar / global news
    # ----------------------------------------------------------------
    lines.append("## Dnešné všeobecné novinky / market radar")
    if global_news:
        lines.extend(format_news_items(global_news, limit=8))
    else:
        lines.append("- No broad market news collected.")
    lines.append("")

    # Trump radar
    t_market    = trump_news.get("market_impact", [])
    t_companies = trump_news.get("company_hits", {})
    if t_market or t_companies:
        lines.append("## 🇺🇸 Trump Market Radar")
        if t_market:
            lines.append("### Všeobecný vplyv na trhy")
            for item in t_market[:6]:
                lines.append(f"- {item.get('title','')} ({item.get('source_domain','')}) "
                             f"— sentiment: {item.get('sentiment','NEUTRAL')}")
            lines.append("")
        if t_companies:
            lines.append("### Zmienky o firmách z portfólia")
            for sym, hits in sorted(t_companies.items()):
                for item in hits:
                    lines.append(f"  - **{sym}**: {item.get('title','')} — sentiment: {item.get('sentiment','')}")
            lines.append("")

    # Discovery news
    lines.append("## Nové / externé watchlist nápady")
    lines.append("Tieto položky nie sú automatické odporúčania. Sú to iba katalyzátory na manuálne overenie.")
    if discovery_news:
        lines.extend(format_news_items(discovery_news, limit=8))
    else:
        lines.append("- No external discovery items collected.")
    lines.append("")

    # ----------------------------------------------------------------
    # Per-ticker news sections (iba tradable)
    # ----------------------------------------------------------------
    lines.append("## Novinky podľa sledovaných aktív")
    for d in tradable:
        s    = d.get("summary") or {}
        news = d.get("news") or []
        rec  = d.get("recommendation") or {}
        if not news and not rec.get("recommendation"):
            continue
        sym  = d.get("broker_symbol") or s.get("broker_symbol") or "?"
        nm   = d.get("name") or s.get("name") or sym
        lines.append(f"### {sym} — {nm}")
        ai_rec = rec.get("recommendation", "")
        if ai_rec:
            emoji = {"BUY": "🟢", "SELL": "🔴", "WATCH": "🟡"}.get(ai_rec, "")
            sv = rec.get("score_value", 0); sm = rec.get("score_momentum", 0)
            ss = rec.get("score_sentiment", 0); tot = rec.get("total_score", 0)
            lines.append(f"- **AI Recommendation: {emoji} {ai_rec}** "
                         f"| Score: V{sv} M{sm} S{ss} = {tot:+d} | DQ: {rec.get('data_quality','?')}")
            cmt = rec.get("comment", "")
            if cmt:
                lines.append(f"- AI Comment: {cmt}")
        if news:
            lines.extend(format_news_items(news, limit=3))
        levels = s.get("risk_levels", {})
        if levels.get("invalidation"):
            lines.append(f"- Risk level: {levels.get('invalidation')}")
        lines.append("")

    # ----------------------------------------------------------------
    # Ollama tidy-up (with ticker validation)
    # ----------------------------------------------------------------
    if analysis_text and analysis_text.strip():
        lines.append("## Ollama tidy-up")
        allowed = set(d.get("broker_symbol", "").upper() for d in tradable if d.get("broker_symbol"))
        # Validate: check if model mentioned unknown tickers
        import re
        mentioned = set(re.findall(r"\b([A-Z]{1,6})\b", analysis_text))
        unknown   = mentioned - allowed - {"BUY", "SELL", "WATCH", "EUR", "USD", "MA", "RSI",
                                           "OK", "DQ", "T212", "ETF", "AI", "PE", "EPS",
                                           "N", "A", "B", "C", "V", "M", "S"}
        suspicious_unknown = [t for t in unknown if len(t) >= 2 and t not in {"THE", "AND", "FOR", "NOT"}]
        # Flag obvious hallucination markers
        hallucinated = [t for t in suspicious_unknown
                        if t in {"AAPL", "GOOG", "MSFT", "AMZN", "META", "NFLX", "BIDU", "TSLA"}
                        and t not in allowed]
        if hallucinated:
            lines.append(f"⚠️ Model tidy-up omitted due to validation failure.")
            lines.append(f"Hallucinated tickers detected: {', '.join(sorted(hallucinated))}")
            print(f"[WARNING] Ollama hallucinated tickers: {hallucinated} — output discarded")
        else:
            lines.append(analysis_text)
        lines.append("")
    else:
        lines.append("## Ollama tidy-up")
        lines.append("(AI analysis not available or empty)")
        lines.append("")

    # ----------------------------------------------------------------
    # Data Quality Notes
    # ----------------------------------------------------------------
    lines.append("## Data Quality Notes")
    low_q = [d for d in collected_data if d.get("data_quality", 100) < 60
             and d.get("dq_status") != "BROKER_ONLY"]
    if low_q:
        lines.append("⚠️ Low data quality assets (excluded from BUY/SELL scoring):")
        for d in low_q:
            s   = d.get("summary") or {}
            sym = d.get("broker_symbol") or s.get("broker_symbol") or "UNKNOWN"
            dq  = d.get("data_quality") or 0
            dqs = d.get("dq_status", "PARTIAL")
            err = (d.get("price") or {}).get("error", "No historical price data")
            tried = (d.get("price") or {}).get("symbol_tried", sym)
            lines.append(f"- {sym}: {dq}/100 ({dqs}) — {err}. Tried: {tried}")
    if broker_only:
        lines.append(f"- {len(broker_only)} T212 interné kódy (BROKER_ONLY) — "
                     f"detaily v sekcii 'Broker-only / Low-data Assets' vyššie.")
    if not low_q and not broker_only:
        lines.append("- No low data quality assets.")
    lines.append("")

    return "\n".join(lines)



def save_report(markdown_text: str, json_data: Dict[str, Any], folder: str) -> Tuple[Path, Path]:
    """Save reports to markdown and JSON files."""
    out = Path(folder)
    out.mkdir(exist_ok=True)
    
    # Timestamped files for archive
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    archive = out / "archive"
    archive.mkdir(exist_ok=True)
    md_file = archive / f"{timestamp}_portfolio_analysis.md"
    json_file = archive / f"{timestamp}_portfolio_analysis.json"
    
    # Main files (always latest)
    main_md = out / "portfolio_analysis.md"
    main_json = out / "portfolio_analysis.json"
    
    # Write timestamped versions
    md_file.write_text(markdown_text, encoding="utf-8")
    json_file.write_text(json.dumps(json_data, indent=2, ensure_ascii=False), encoding="utf-8")
    
    # Write main versions
    main_md.write_text(markdown_text, encoding="utf-8")
    main_json.write_text(json.dumps(json_data, indent=2, ensure_ascii=False), encoding="utf-8")
    
    return main_md, main_json


# ============================================================================
# MAIN EXECUTION
# ============================================================================

def main():
    """Main entry point."""
    ap = argparse.ArgumentParser(
        description="Reliable Portfolio AI Assistant with local Ollama models"
    )
    ap.add_argument("--config", default="portfolio_config.json", help="Config file path")
    ap.add_argument("--no-ai", action="store_true", help="Generate report without AI analysis")
    ap.add_argument("--no-interactive", action="store_true", help="Run without prompts")
    ap.add_argument("--validate-only", action="store_true", help="Validate config and exit")
    ap.add_argument("--migrate-config", action="store_true", help="Migrate legacy config format")
    ap.add_argument("--debug", action="store_true", help="Include debug data in output")
    ap.add_argument("--dry-run", action="store_true", help="Don't save files")
    ap.add_argument("--group", help="Analyze specific group only")
    ap.add_argument("--asset", help="Analyze specific asset (broker_symbol)")
    ap.add_argument("--json-only", action="store_true", help="Output JSON only")
    
    args = ap.parse_args()
    
    print("\n" + "=" * 70)
    print("[PORTFOLIO AI ASSISTANT - RELIABLE INVESTMENT MONITORING]")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70 + "\n")
    
    # === STEP 1: LOAD AND VALIDATE CONFIG ===
    print("[STEP 1] Loading configuration...")
    try:
        config = load_config(args.config)
        config = migrate_legacy_config(config)  # Auto-migrate if needed
    except Exception as e:
        print(f"[ERROR] Cannot load config: {e}")
        return
    
    # Validate config
    errors = validate_config(config)
    if errors:
        print("[ERROR] Config validation failed:")
        for error in errors:
            print(f"  - {error}")
        if args.validate_only or args.migrate_config:
            return
        print("\nContinuing anyway...\n")
    else:
        print("[OK] Config valid")
    
    # === STEP 2: MIGRATION ===
    if args.migrate_config:
        print("\n[MIGRATION] Writing portfolio.migrated.json...")
        migrated_path = Path(args.config).parent / "portfolio.migrated.json"
        migrated_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[OK] Migrated config saved: {migrated_path.resolve()}")
        return
    
    # === STEP 3: VALIDATION ONLY ===
    if args.validate_only:
        print("[VALIDATE] Checking Ollama...")
        settings = config.get("settings", {})
        available, msg = check_ollama_available(settings)
        if available:
            print(f"[OK] {msg}")
            models = list_ollama_models(settings)
            if models:
                print(f"[OK] Available models: {', '.join(models[:5])}")
            else:
                print("[WARNING] Could not list models")
        else:
            print(f"[ERROR] {msg}")
        return
    
    settings = config.get("settings", {})
    # Merge ai_backends from config root into settings for AI calls
    if "ai_backends" in config:
        settings["ai_backends"] = config["ai_backends"]
    assets = config.get("assets", [])
    portfolio_rules = config.get("portfolio_rules", {})

    # Load optional broker/environment data
    env = load_environment_variables()

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

    if trading212_enabled and env.get("trading212_api_key"):
        print("[BROKER] Fetching Trading 212 positions...")
        sync = fetch_trading212_positions(
            env["trading212_api_key"],
            api_secret=env.get("trading212_api_secret"),
            api_base=env.get("trading212_api_base", "https://live.trading212.com"),
        )
        broker_positions["trading212"] = sync.get("positions", [])
        broker_sync_status = sync.get("status", broker_sync_status)
        if broker_sync_status.get("ok"):
            print(f"[BROKER] Trading 212 positions loaded: {len(broker_positions['trading212'])}")

            # Fetch authoritative EUR total from cash API
            try:
                import base64
                _key    = env.get("trading212_api_key", "")
                _secret = env.get("trading212_api_secret", "")
                _creds  = base64.b64encode(f"{_key}:{_secret}".encode()).decode()
                _base   = env.get("trading212_api_base", "https://live.trading212.com").rstrip("/")
                _r = requests.get(
                    f"{_base}/api/v0/equity/account/cash",
                    headers={"Authorization": f"Basic {_creds}"},
                    timeout=10
                )
                if _r.status_code == 200:
                    _cash = _r.json()
                    broker_sync_status["account_total"] = float(_cash.get("total", 0))
                    broker_sync_status["account_pnl"]   = float(_cash.get("result", 0))
                    broker_sync_status["cash_free"]      = float(_cash.get("free", 0))
                    print(f"[BROKER] Účet: {_cash.get('total', 0):.2f} EUR | "
                          f"P&L: {_cash.get('result', 0):+.2f} EUR | "
                          f"Free cash: {_cash.get('free', 0):.2f} EUR")
            except Exception as _e:
                print(f"[BROKER] Cash API fetch failed: {_e}")
        else:
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

    assets = merge_broker_data(assets, broker_positions)
    symbol_aliases = config.get("symbol_aliases", {})
    assets = auto_discover_unmatched_positions(assets, broker_positions, settings, symbol_aliases)

    # === STEP 4: CHECK LM STUDIO ===
    print("[STEP 2] Checking LM Studio availability...")
    available, msg = check_lmstudio_available(settings)
    if not available:
        print(f"[WARNING] LM Studio check: {msg}")
        if not args.no_ai:
            print("[WARNING] Forcing --no-ai mode")
            args.no_ai = True
    else:
        print(f"[OK] LM Studio available")
        working_model = get_working_model(settings)
        if working_model:
            print(f"[OK] Working model: {working_model}")
        else:
            print("[ERROR] No working model found. Available:")
            models = list_lmstudio_models(settings)
            if models:
                for m in models[:5]:
                    print(f"  - {m}")
            if not args.no_ai:
                args.no_ai = True
    
    # === STEP 5: FILTER ASSETS ===
    print(f"\n[STEP 3] Loading assets ({len(assets)} total)...")
    if args.group:
        assets = [a for a in assets if a.get("group") == args.group]
        print(f"  Filtered to group '{args.group}': {len(assets)} assets")
    
    if args.asset:
        assets = [a for a in assets if a.get("broker_symbol") == args.asset]
        print(f"  Filtered to asset '{args.asset}': {len(assets)} assets")
    
    if not assets:
        print("[ERROR] No assets to analyze")
        return
    
# === STEP 6: COLLECT DATA ===
    print(f"\n[STEP 4] Collecting data for {len(assets)} assets...")
    
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

            # T212 internal codes bez Yahoo dát — SKIP scoring pipeline
            price_dict = data.get("price", {})
            price_val = price_dict.get("price") if isinstance(price_dict, dict) else price_dict
            if (data.get("auto_discovered")
                    and settings.get("skip_no_data_auto_discovered", True)
                    and data_quality < 20
                    and not price_val
                    and dq_str in ("NO_PRICE_DATA", "BROKER_ONLY")):
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

            summary = build_asset_summary(data, signals, data_quality, settings)
            data["signals"]      = signals
            data["data_quality"] = data_quality
            data["summary"]      = summary
            data["recommendation"] = calculate_recommendation(data, signals, settings)

            collected_data.append(data)
            print(log_suffix)
        except Exception as e:
            print(f"[ERROR: {e}]")

    # === STEP 5: SCORES & RECOMMENDATIONS ===
    buy_soft   = [d for d in collected_data if d.get("recommendation", {}).get("recommendation") == "BUY"]
    sell_soft  = [d for d in collected_data if d.get("recommendation", {}).get("recommendation") == "SELL"]
    watch_soft = [d for d in collected_data if d.get("recommendation", {}).get("recommendation") == "WATCH"]
    low_dq     = [d for d in collected_data if d.get("dq_status") not in ("OK", "PARTIAL")]
    print(f"\n[STEP 5] Scores & recommendations...")
    print(f"  AI recommendations generated for {len(collected_data)} assets "
          f"({len(low_dq)} low data quality skipped from BUY/SELL scoring).")
    print(f"  BUY: {len(buy_soft)} | SELL: {len(sell_soft)} | WATCH: {len(watch_soft)}")

    print(f"\n[STEP 6] Collecting broad market/discovery news...")
    global_news    = [] if settings.get("skip_global_news", False) else collect_global_news(settings)
    discovery_news = [] if settings.get("skip_discovery_news", False) else collect_discovery_news(settings)
    trump_news     = {}   # Trump radar — add collect_trump_news() call here to enable
    print(f"  Global news: {len(global_news)} | Discovery ideas: {len(discovery_news)}")

    print(f"\n[STEP 7] Generating reports...")
    
    # Build JSON output — clean split: analyzed_assets vs broker_only_assets
    GOOD_DQ = ("OK", "PARTIAL", "SYMBOL_MISMATCH")
    tradable_json    = [d for d in collected_data if d.get("dq_status") in GOOD_DQ]
    broker_only_json = [d for d in collected_data if d.get("dq_status") not in GOOD_DQ]

    def _asset_to_json(data: Dict[str, Any]) -> Dict[str, Any]:
        rec  = data.get("recommendation") or {}
        s    = data.get("summary") or {}
        pr   = data.get("price") or {}
        t212 = data.get("trading212") or {}
        return {
            "ticker":             data.get("broker_symbol"),
            "name":               data.get("name"),
            "group":              data.get("group"),
            "current_price":      pr.get("price"),
            "t212_price":         t212.get("current_price"),
            "change_1d_pct":      pr.get("change_1d_pct"),
            "currency":           pr.get("currency"),
            "data_quality":       rec.get("data_quality") or data.get("dq_status", "PARTIAL"),
            "data_quality_num":   data.get("data_quality", 0),
            "ai_recommendation":  rec.get("recommendation", "WATCH"),
            "score_value":        rec.get("score_value", 0),
            "score_momentum":     rec.get("score_momentum", 0),
            "score_sentiment":    rec.get("score_sentiment", 0),
            "total_score":        rec.get("total_score", 0),
            "comment":            rec.get("comment", ""),
            "buy_probability":    s.get("buy_probability", 0),
            "sell_probability":   s.get("sell_probability", 0),
            "technical_score":    s.get("technical_score", 0),
            "avg_sentiment":      rec.get("avg_sentiment"),
            "rsi":                rec.get("rsi"),
            "news_count":         len(data.get("news", [])),
            "news":               data.get("news", []),
            "t212_position": {
                "quantity":       t212.get("quantity"),
                "average_price":  t212.get("average_price"),
                "pnl":            t212.get("pnl"),
                "pnl_pct":        t212.get("pnl_pct"),
            } if t212 else None,
        }

    json_output = {
        "timestamp":  datetime.now().isoformat(),
        "config": {
            "model":         get_working_model(settings) or settings.get("model"),
            "buy_threshold": settings.get("buy_probability_threshold", 60),
            "sell_threshold":settings.get("sell_probability_threshold", 70),
        },
        "summary": {
            "broker_positions_loaded":  broker_sync_status.get("positions_count", 0),
            "assets_analyzed":          len(tradable_json),
            "broker_only_assets":       len(broker_only_json),
            "buy_candidates_soft":      len([d for d in tradable_json if (d.get("recommendation") or {}).get("recommendation") == "BUY"]),
            "sell_candidates_soft":     len([d for d in tradable_json if (d.get("recommendation") or {}).get("recommendation") == "SELL"]),
            "watch_candidates":         len([d for d in tradable_json if (d.get("recommendation") or {}).get("recommendation") == "WATCH"]),
            "buy_candidates_strict":    len([d for d in tradable_json if (d.get("summary") or {}).get("status") == "BUY_CANDIDATE"]),
            "sell_candidates_strict":   len([d for d in tradable_json if (d.get("summary") or {}).get("status") == "SELL_CANDIDATE"]),
        },
        "buy_candidates":   [_asset_to_json(d) for d in tradable_json if (d.get("recommendation") or {}).get("recommendation") == "BUY"],
        "sell_candidates":  [_asset_to_json(d) for d in tradable_json if (d.get("recommendation") or {}).get("recommendation") == "SELL"],
        "watch_candidates": [_asset_to_json(d) for d in tradable_json if (d.get("recommendation") or {}).get("recommendation") == "WATCH"],
        "analyzed_assets":  [_asset_to_json(d) for d in tradable_json],
        "broker_only_assets":[_asset_to_json(d) for d in broker_only_json],
        "data_quality_notes": [],
        "broker_sync_status": broker_sync_status,
        "assets": [_asset_to_json(d) for d in collected_data],  # backwards compat
    }


    # Build markdown report (without AI analysis if not using AI)
    if args.no_ai:
        analysis_text = "[AI analysis disabled]\n\nScores are calculated deterministically."
    else:
        print("  Calling AI for analysis...")
        prompt = build_prompt(collected_data, portfolio_rules, settings)
        analysis_text = run_ai_pipeline(prompt, settings)
    
    markdown_report = generate_report(collected_data, analysis_text, settings,
                                       global_news, discovery_news, broker_sync_status,
                                       trump_news)
    
    # === STEP 8: SAVE FILES ===
    if not args.dry_run:
        print(f"\n[STEP 8] Saving reports...")
        output_folder = settings.get("output_folder", "reports")
        md_path, json_path = save_report(markdown_report, json_output, output_folder)
        
        print(f"[OK] Markdown: {md_path.resolve()}")
        print(f"[OK] JSON: {json_path.resolve()}")
    else:
        print("\n[DRY RUN] Reports not saved")
    
    # === SUMMARY ===
    print("\n" + "=" * 70)
    print("[ANALYSIS COMPLETE]")
    smry = json_output.get("summary", {})
    print(f"Broker positions loaded:        {smry.get('broker_positions_loaded', 0)}")
    print(f"Assets analyzed (market data):  {smry.get('assets_analyzed', 0)}")
    print(f"Broker-only / low-data:         {smry.get('broker_only_assets', 0)}")
    print(f"BUY candidates (soft):          {smry.get('buy_candidates_soft', 0)}")
    print(f"SELL candidates (soft):         {smry.get('sell_candidates_soft', 0)}")
    print(f"WATCH candidates:               {smry.get('watch_candidates', 0)}")
    print(f"Strong BUY (strict Python):     {smry.get('buy_candidates_strict', 0)}")
    print(f"Strong SELL (strict Python):    {smry.get('sell_candidates_strict', 0)}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()


logger.info("=" * 70)
logger.info("Finished successfully")
logger.info("=" * 70)