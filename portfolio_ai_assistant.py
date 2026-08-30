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

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
import os
import yfinance as yf

# --- POISTKA PRE SUI A TAO (Napodobnenie prehliadača pre Yahoo Finance) ---
session = requests.Session()
session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'})
yf.set_tz_cache_location("data/cache") # voliteľné, pre stabilizáciu cache
# Vynútenie session pre všetky požiadavky yfinance
try:
    import requests_cache
except ImportError:
    requests_cache = None
# Ak nepoužívaš requests_cache, stačí povedať yfinance aby globálne používal túto session:
yf.utils.get_http_session = lambda: session
# -------------------------------------------------------------------------

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

try:
    from ddgs import DDGS
except ImportError:
    try:
        # fallback for older environments still using the renamed package
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None


# ============================================================================
# CONFIGURATION & VALIDATION
# ============================================================================

def load_config(path: str = "portfolio_config.json") -> Dict[str, Any]:
    """Load and validate portfolio configuration."""
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    return config




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
    elif load_dotenv:
        load_dotenv(override=True)

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


def auto_discover_unmatched_positions(assets, broker_positions, settings):
    """
    Pre T212 pozície ktoré sa NEPODARILO spárovať so žiadnym assetom v configu
    (napr. nový nákup, ktorý si ešte nepridal do portfolio_config.json),
    vygeneruj dočasný minimálny asset záznam aby sa pre ne stiahli ceny aj novinky.

    Tieto auto-discovered assety sa nezapisujú späť do portfolio_config.json —
    sú iba pre tento jeden beh reportu.
    """
    if not settings.get("auto_discover_t212_positions", True):
        return assets

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
            "yahoo_symbol":       settings.get("symbol_aliases", {}).get(clean, clean),
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
    
    for asset in assets:
        broker_symbol = asset.get("broker_symbol", "").upper()
        enhanced = asset.copy()
        
        for pos in broker_positions.get("trading212", []):
            clean = (pos.get("broker_symbol_clean") or pos.get("broker_symbol", "")).upper()
            raw   = (pos.get("broker_symbol") or "").upper()
            # 1) exact match on normalized ticker (WDC == WDC, from WDC_US_EQ)
            # 2) exact match on raw ticker (legacy / crypto pairs without suffix)
            # 3) prefix match as last resort (handles edge-case suffix variants)
            if clean == broker_symbol or raw == broker_symbol or raw.startswith(broker_symbol + "_"):
                enhanced["trading212"] = pos
                break
        
        for pos in broker_positions.get("revolut", []):
            if pos.get("broker_symbol", "").upper() == broker_symbol:
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

def _detect_backend(settings: Dict[str, Any]) -> str:
    """
    Zistí aktívny AI backend.
    Priorita: lm_studio_base_url (ak je nastavená a dostupná) → Ollama
    Vracia: 'lmstudio' alebo 'ollama'
    """
    lms_url = (settings.get("lm_studio_base_url") or "").strip()
    if lms_url:
        try:
            r = requests.get(f"{lms_url.rstrip('/')}/models", timeout=4)
            if r.status_code == 200:
                return "lmstudio"
        except Exception:
            pass
    return "ollama"


def build_ollama_url(settings: Dict[str, Any], endpoint: str = "chat") -> str:
    """
    Build full API URL — podporuje Ollama aj LM Studio.
    LM Studio: /v1/chat/completions, /v1/models
    Ollama:    /api/chat,            /api/tags
    """
    backend = _detect_backend(settings)

    if backend == "lmstudio":
        base = settings.get("lm_studio_base_url", "http://localhost:1234/v1").rstrip("/")
        if endpoint == "chat":   return base + "/chat/completions"
        if endpoint == "tags":   return base + "/models"
        return base + "/" + endpoint.lstrip("/")
    else:
        base = (settings.get("ollama_base_url") or settings.get("ollama_url")
                or "http://127.0.0.1:11434").rstrip("/")
        ep = ("/api/chat" if endpoint == "chat" else
              "/api/tags" if endpoint == "tags" else endpoint)
        if not ep.startswith("/"): ep = "/" + ep
        return base + ep


def list_ollama_models(settings: Dict[str, Any]) -> Optional[List[str]]:
    """Vráti dostupné modely (Ollama alebo LM Studio)."""
    try:
        backend = _detect_backend(settings)
        url = build_ollama_url(settings, "tags")
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        if backend == "lmstudio":
            # LM Studio OpenAI format: {"data": [{"id": "model-name"}, ...]}
            return sorted(m.get("id", "") for m in data.get("data", []))
        else:
            return sorted(m.get("name", "") for m in data.get("models", []))
    except Exception:
        return None


def validate_model_available(settings: Dict[str, Any], model_name: str) -> bool:
    models = list_ollama_models(settings)
    if models is None:
        return False
    return any(model_name in m for m in models)


def check_ollama_available(settings: Dict[str, Any]) -> Tuple[bool, str]:
    """Skontroluje dostupnosť AI backendu (Ollama alebo LM Studio)."""
    try:
        backend = _detect_backend(settings)
        url = build_ollama_url(settings, "tags")
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        return True, f"{backend.upper()} reachable at {url}"
    except requests.ConnectionError as e:
        return False, f"Cannot connect: {e}"
    except Exception as e:
        return False, f"Check failed: {e}"


def get_working_model(settings: Dict[str, Any]) -> Optional[str]:
    """Nájde funkčný model (primárny → fallback → prvý dostupný)."""
    primary  = settings.get("model")
    fallback = settings.get("fallback_model")

    if primary and validate_model_available(settings, primary):
        return primary
    if fallback and validate_model_available(settings, fallback):
        return fallback
    models = list_ollama_models(settings)
    if models:
        return models[0]
    return None





# ============================================================================
# YFINANCE DATA COLLECTION
# ============================================================================

def get_price_snapshot(symbol: str, period: str = "6mo") -> Dict[str, Any]:
    """Fetch price data from yfinance with comprehensive metrics."""
    out = {
        "symbol": symbol,
        "price": None,
        "currency": None,
        "previous_close": None,
        "change_1d_pct": None,
        "change_5d_pct": None,
        "change_20d_pct": None,
        "change_3mo_pct": None,
        "change_from_6mo_high_pct": None,
        "market_cap": None,
        "pe_trailing": None,
        "pe_forward": None,
        "beta": None,
        "volume": None,
        "avg_volume_20d": None,
        "volume_vs_avg_pct": None,
        "ma20": None,
        "ma50": None,
        "ma100": None,
        "ma200": None,
        "week_52_high": None,
        "week_52_low": None,
        "trend_hint": "N/A",
        "risk_hint": "N/A",
        "candle_count": 0,
        "timestamp": datetime.now().isoformat(),
        "error": None
    }
    
    if not symbol:
        out["error"] = "Empty symbol"
        return out
    
    try:
        ticker = yf.Ticker(symbol)
        
        # Fetch historical prices
        hist = ticker.history(period=period, interval="1d", auto_adjust=False)
        if hist.empty or "Close" not in hist:
            out["error"] = "No historical price data"
            return out
        
        close = hist["Close"].dropna()
        volume = hist["Volume"].dropna() if "Volume" in hist else None
        
        if len(close) < 2:
            out["error"] = f"Insufficient data: only {len(close)} candles"
            return out
        
        out["candle_count"] = len(close)
        
        # Current and previous close
        last = safe_float(close.iloc[-1])
        out["price"] = last
        out["previous_close"] = safe_float(close.iloc[-2]) if len(close) >= 2 else None
        out["change_1d_pct"] = pct(last, out["previous_close"])
        
        # Multi-period changes
        if len(close) >= 6:
            out["change_5d_pct"] = pct(last, safe_float(close.iloc[-6]))
        if len(close) >= 21:
            out["change_20d_pct"] = pct(last, safe_float(close.iloc[-21]))
        if len(close) >= 63:
            out["change_3mo_pct"] = pct(last, safe_float(close.iloc[-63]))
        
        # 52-week metrics
        high = safe_float(close.max())
        low = safe_float(close.min())
        out["week_52_high"] = high
        out["week_52_low"] = low
        if last and high:
            out["change_from_6mo_high_pct"] = (last / high - 1) * 100
        
        # Moving averages
        if len(close) >= 20:
            out["ma20"] = safe_float(close.tail(20).mean())
        if len(close) >= 50:
            out["ma50"] = safe_float(close.tail(50).mean())
        if len(close) >= 100:
            out["ma100"] = safe_float(close.tail(100).mean())
        if len(close) >= 200:
            out["ma200"] = safe_float(close.tail(200).mean())
        
        # Volume metrics
        if volume is not None and len(volume) > 0:
            out["volume"] = safe_float(volume.iloc[-1])
            if len(volume) >= 20:
                out["avg_volume_20d"] = safe_float(volume.tail(20).mean())
                out["volume_vs_avg_pct"] = pct(out["volume"], out["avg_volume_20d"])
        
        # Company info
        try:
            info = ticker.fast_info or {}
            out["currency"] = info.get("currency") if isinstance(info, dict) else None
            out["market_cap"] = safe_float(info.get("marketCap"))
            out["beta"] = safe_float(info.get("beta"))
            
            # Try info dict for PE ratios
            if hasattr(ticker, 'info'):
                out["pe_trailing"] = safe_float(ticker.info.get("trailingPE"))
                out["pe_forward"] = safe_float(ticker.info.get("forwardPE"))
        except Exception:
            pass
        
        # Trend hint
        last, ma20, ma50, ma100 = out["price"], out["ma20"], out["ma50"], out["ma100"]
        if last and ma20 and ma50:
            if ma100 and last > ma20 > ma50 > ma100:
                out["trend_hint"] = "Strong uptrend: price > MA20 > MA50 > MA100"
            elif last > ma20 > ma50:
                out["trend_hint"] = "Uptrend: price > MA20 > MA50"
            elif last < ma20 < ma50:
                out["trend_hint"] = "Downtrend: price < MA20 < MA50"
            elif last > ma20:
                out["trend_hint"] = "Short-term positive, trend unclear"
            else:
                out["trend_hint"] = "Weakness or below MA20"
        
        # Risk hint
        d1, d5, highdrop = out["change_1d_pct"], out["change_5d_pct"], out["change_from_6mo_high_pct"]
        if d1 is not None and d1 < -7:
            out["risk_hint"] = "Sharp 1-day decline"
        elif d5 is not None and d5 < -12:
            out["risk_hint"] = "Sharp 5-day decline"
        elif highdrop is not None and highdrop < -25:
            out["risk_hint"] = "Deep below 6M high"
        elif d1 is not None and d1 > 7:
            out["risk_hint"] = "Sharp 1-day rally, FOMO risk"
        else:
            out["risk_hint"] = "No extreme price signal"
    
    except Exception as e:
        out["error"] = str(e)
    
    return out


def get_price_snapshot_with_candidates(symbols: List[str], period: str = "6mo") -> Dict[str, Any]:
    """
    Try multiple Yahoo Finance symbols and return the first valid price snapshot.
    Useful for broker symbols like CATL/C7A0 where exchange suffixes differ.
    """
    tried = []
    for sym in [s for s in symbols if s]:
        if sym in tried:
            continue
        tried.append(sym)
        snap = get_price_snapshot(sym, period)
        snap["symbol_tried"] = sym
        snap["symbols_tried"] = tried.copy()
        if snap.get("price") is not None and not snap.get("error"):
            snap["resolved_symbol"] = sym
            return snap

    # Return last failure but keep diagnostics
    if tried:
        snap = get_price_snapshot(tried[-1], period)
        snap["symbol_tried"] = tried[-1]
        snap["symbols_tried"] = tried
        snap["resolved_symbol"] = None
        if not snap.get("error"):
            snap["error"] = "No valid price found from symbol candidates"
        return snap

    return get_price_snapshot("", period)


# ============================================================================
# NEWS & SENTIMENT ANALYSIS
# ============================================================================

TRUSTED_DOMAINS = {
    "reuters.com", "cnbc.com", "marketwatch.com", "barrons.com",
    "morningstar.com", "nasdaq.com", "finance.yahoo.com", "stockanalysis.com",
    "seekingalpha.com", "fool.com", "investor.gov", "sec.gov",
    "benzinga.com", "thestreet.com", "investor.com"
}

BULLISH_KEYWORDS = {
    "beat", "raised", "guidance", "growth", "expand", "strong", "surge",
    "upgrade", "record", "rally", "bull", "earn", "revenue", "margin",
    "profit", "backlog", "buyback", "acquisition", "partnership", "demand",
    "accelerat", "outperform"
}

BEARISH_KEYWORDS = {
    "miss", "lowered", "decline", "fall", "weak", "warning", "miss",
    "downgrade", "loss", "lawsuit", "debt", "burn", "dilution", "bankruptcy",
    "delisting", "recall", "scandal", "risk", "bear", "short", "concern",
    "compress", "deteriorat", "headwind"
}


def extract_domain(url: str) -> str:
    """Extract domain from URL."""
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def classify_source(url: str, domain: str = "") -> str:
    """Classify news source: official/news/finance/forum/low_quality."""
    if not domain:
        domain = extract_domain(url)
    
    domain_lower = domain.lower()
    
    if any(x in domain_lower for x in ["sec.gov", "investor.com", "ir."]):
        return "official"
    elif any(x in domain_lower for x in TRUSTED_DOMAINS):
        return "news"
    elif any(x in domain_lower for x in ["bloomberg", "reuters", "cnbc", "ft.com"]):
        return "news"
    elif any(x in domain_lower for x in ["reddit", "stocktwits", "seeking", "forum"]):
        return "forum"
    elif any(x in domain_lower for x in ["finance", "stock", "market"]):
        return "finance_data"
    else:
        return "low_quality"


def classify_sentiment(text: str) -> Tuple[str, float]:
    """
    Classify sentiment using keyword scoring.
    Returns (sentiment, confidence) where sentiment in [POSITIVE, NEGATIVE, NEUTRAL, MIXED, UNKNOWN]
    """
    if not text:
        return "UNKNOWN", 0.0
    
    text_lower = text.lower()
    
    # Count keywords
    bullish_count = sum(1 for kw in BULLISH_KEYWORDS if kw in text_lower)
    bearish_count = sum(1 for kw in BEARISH_KEYWORDS if kw in text_lower)
    
    total = bullish_count + bearish_count
    if total == 0:
        return "NEUTRAL", 0.5
    
    bullish_ratio = bullish_count / total
    
    if bullish_ratio >= 0.75:
        return "POSITIVE", min(0.95, bullish_count / 5)
    elif bullish_ratio <= 0.25:
        return "NEGATIVE", min(0.95, bearish_count / 5)
    elif bullish_ratio >= 0.4 and bullish_ratio <= 0.6:
        return "MIXED", 0.6
    else:
        return "NEUTRAL", 0.7


def is_relevant_to_asset(title: str, snippet: str, asset: Dict[str, Any]) -> Tuple[bool, float]:
    """
    Check if search result is relevant to the asset.
    Returns (is_relevant, relevance_score 0-100)
    """
    text = (title + " " + snippet).lower()
    
    broker_sym = asset.get("broker_symbol", "").lower()
    yahoo_sym = asset.get("yahoo_symbol", "").lower().split(".")[0]  # Without .DE suffix
    company_name = asset.get("name", "").lower()
    search_query = asset.get("search_query", "").lower()
    
    # Keywords that should be present
    required_keywords = [broker_sym, yahoo_sym]
    if company_name and len(company_name) > 3:
        required_keywords.append(company_name.split()[0])  # First word of company name
    
    # Check presence
    match_count = sum(1 for kw in required_keywords if kw and kw in text)
    
    # Negative keywords that disqualify
    irrelevant_phrases = [
        "ringcentral", "national debt", "rtx", "lockheed", "netflix",
        "general electric", "general motors", "random forum", "unrelated",
        "cryptocurrency", "bitcoin", "ethereum"
    ]
    
    for phrase in irrelevant_phrases:
        if phrase in text and broker_sym not in phrase:
            return False, 10
    
    if match_count >= 1:
        # Has at least one required keyword
        return True, min(95, match_count * 40)
    else:
        return False, 20


def filter_and_score_news(results: List[Dict[str, Any]], asset: Dict[str, Any], 
                          max_results: int = 6) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Filter news results by relevance. Returns (relevant_results, excluded_results)
    """
    relevant = []
    excluded = []
    
    for result in results:
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        url = result.get("url", "")
        
        is_relevant, relevance_score = is_relevant_to_asset(title, snippet, asset)
        source_type = classify_source(url)
        
        enriched = {
            **result,
            "relevance_score": relevance_score,
            "source_type": source_type,
            "is_relevant": is_relevant,
            "source_domain": extract_domain(url)
        }
        
        if is_relevant and relevance_score >= 50:
            relevant.append(enriched)
        else:
            excluded.append(enriched)
    
    # Sort by relevance
    relevant.sort(key=lambda x: x["relevance_score"], reverse=True)
    
    return relevant[:max_results], excluded


def ddg_search(query: str, max_results: int = 10, news: bool = False):
    """Search using DuckDuckGo."""
    if DDGS is None:
        return [{"title": "Missing ddgs", "snippet": "Install: pip install ddgs"}]
    
    results = []
    try:
        with DDGS() as ddgs:
            raw = ddgs.news(query, max_results=max_results) if news else ddgs.text(query, max_results=max_results)
            for r in raw:
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", r.get("href", "")),
                    "snippet": r.get("body", ""),
                    "date": r.get("date", ""),
                    "source": r.get("source", "")
                })
    except Exception as e:
        results.append({"title": "Search error", "snippet": str(e)})
    
    return results


def resolve_company_name(ticker: str, price_data: Dict[str, Any]) -> str:
    """
    Pokúsi sa zistiť skutočný názov firmy z yfinance info.
    Používa sa pre T212_AUTO_DISCOVERED tickery kde máme len kód,
    nie ľudský názov.  Vráti ticker ak info nie je dostupné.
    """
    try:
        used_sym = price_data.get("resolved_symbol") or ticker
        if not used_sym or used_sym == ticker:
            return ticker
        import yfinance as yf
        info = yf.Ticker(used_sym).fast_info
        # fast_info is lightweight, doesn't hit the heavy info endpoint
        # Try to get longName via regular info if fast_info has nothing useful
        name = getattr(info, "display_name", None) or getattr(info, "name", None)
        if not name:
            full_info = yf.Ticker(used_sym).info
            name = (full_info.get("longName") or full_info.get("shortName") or "").strip()
        return name if name else ticker
    except Exception:
        return ticker


def build_search_query(broker_symbol: str, name: str, is_auto_discovered: bool,
                        resolved_company: str = "") -> str:
    """
    Zostrojí optimálny vyhľadávací dotaz.
    
    Priorita:
    1. Ak je manuálne nastavený search_query v configu → použij ho (bez zmeny).
    2. Ak je auto-discovered a resolved_company je dostupná → "{company} {ticker} stock news"
    3. Ak ticker je príliš krátky (1-2 znaky) alebo generický → pridaj plné meno
    4. Inak → "{name or broker_symbol} {ticker} stock"
    
    Cieľ: zabrániť garbage novinkám pre jednoznakové tickery ako O, C, ES, BE.
    """
    sym = broker_symbol.strip()
    
    # Ak existuje resolved company name (z yfinance), použi ho ako základ
    company = resolved_company if resolved_company and resolved_company != sym else name
    
    # Krátky ticker (1-3 znaky) je VŽDY nejednoznačný → vyžaduje názov firmy
    if len(sym) <= 3 and company and company != sym:
        return f"{company} {sym} stock news"
    
    # Auto-discovered bez názvu → použi resolvedcompany
    if is_auto_discovered and company and company != sym:
        return f"{company} {sym} stock"
    
    # Štandardný prípad
    base = company if company and company != sym else sym
    return f"{base} stock news"


def collect_asset_data(asset: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    """Collect all data for an asset."""
    broker_symbol = asset.get("broker_symbol", "")
    yahoo_symbol = asset.get("yahoo_symbol", "")
    name = asset.get("name", "")
    is_auto = asset.get("auto_discovered", False)
    # Použijeme manuálny search_query ak existuje a nie je len symbolom
    manual_sq = asset.get("search_query", "")
    has_manual_sq = manual_sq and manual_sq != f"{broker_symbol} stock news" and manual_sq != broker_symbol

    symbol_candidates = []

    if yahoo_symbol:
        symbol_candidates.append(yahoo_symbol)

    # Support all naming variants used in configs.
    for key in ("yahoo_symbol_candidates", "yahoo_symbol_fallbacks", "symbol_candidates"):
        for alt in asset.get(key, []):
            if alt and alt not in symbol_candidates:
                symbol_candidates.append(alt)

    if not symbol_candidates and broker_symbol:
        symbol_candidates.append(broker_symbol)

    max_news = int(settings.get("max_news_per_asset", 6))
    max_web = int(settings.get("max_web_per_asset", 4))

    price_data = get_price_snapshot_with_candidates(
        symbol_candidates,
        settings.get("days_price_history", "6mo")
    )

    used_yahoo_symbol = (
        price_data.get("resolved_symbol")
        or price_data.get("used_yahoo_symbol")
        or price_data.get("symbol_tried")
        or yahoo_symbol
    )
    price_data["used_yahoo_symbol"] = used_yahoo_symbol

    # Rozlíšenie skutočného názvu firmy pre auto-discovered tickery
    # (T212 nám dá len ticker, nie názov → yfinance info)
    resolved_company = ""
    if is_auto and (not name or name == broker_symbol):
        resolved_company = resolve_company_name(broker_symbol, price_data)
        if resolved_company and resolved_company != broker_symbol:
            name = resolved_company  # update name for prompt/report

    # Zostrojenie optimálneho search query
    if has_manual_sq:
        search_query = manual_sq
    else:
        search_query = build_search_query(broker_symbol, name, is_auto, resolved_company)

    news_raw = ddg_search(f"{search_query} earnings", max_results=max_news * 2, news=True)
    web_raw = ddg_search(f"{search_query} analysis price target", max_results=max_web * 2, news=False)

    news, news_excluded = filter_and_score_news(news_raw, asset, max_news)
    web, web_excluded = filter_and_score_news(web_raw, asset, max_web)

    return {
        "broker_symbol": broker_symbol,
        "yahoo_symbol": used_yahoo_symbol or yahoo_symbol,
        "configured_yahoo_symbol": yahoo_symbol,
        "name": name or resolved_company or broker_symbol,
        "group": asset.get("group", "PORTFOLIO"),
        "price": price_data,
        "news": news,
        "news_excluded": news_excluded,
        "web": web,
        "web_excluded": web_excluded,
        "symbol_candidates": symbol_candidates,
        "tradingview": fetch_tradingview_technical_data(asset.get("tradingview_symbol", "")) if settings.get("use_tradingview_public_data", True) else None,
        "collected_at": datetime.now().isoformat(),
        # Pass-through broker position data so generate_report() can build the holdings table
        "trading212": asset.get("trading212"),
        "revolut": asset.get("revolut"),
        "auto_discovered": asset.get("auto_discovered", False),
    }


def compact(items: List[Dict[str, Any]], limit: int = 3) -> str:
    """Format news items for display."""
    lines = []
    for i, r in enumerate(items[:limit], 1):
        title = (r.get("title") or "").strip()
        relevance = r.get("relevance_score", 0)
        domain = r.get("source_domain", "")
        
        lines.append(f"{i}. {title}")
        if relevance:
            lines.append(f"   Relevance: {relevance:.0f}%")
        if domain:
            lines.append(f"   Source: {domain}")
        
        snippet = (r.get("snippet") or "").strip().replace("\n", " ")
        if snippet:
            lines.append(f"   {snippet[:300]}")
    
    return "\n".join(lines) if lines else "No relevant data found"


# ============================================================================
# DATA QUALITY & SCORING
# ============================================================================

def calculate_data_quality(asset_data: Dict[str, Any]) -> int:
    """
    Calculate data quality score (0-100) based on availability and completeness.
    Also sets asset_data["dq_status"] string flag:
      "OK"          — full data, safe for scoring
      "PARTIAL"     — price exists but incomplete (no MAs, low candle count)
      "NO_PRICE_DATA" — no usable price feed
      "BROKER_ONLY" — T212 position exists but no external data at all
    """
    score = 0
    price = asset_data.get("price", {}) or {}

    has_price      = bool(price.get("price"))
    candle_count   = price.get("candle_count", 0)
    has_identity   = bool(price.get("currency") or asset_data.get("name"))
    has_volume     = bool(price.get("market_cap") or price.get("volume"))
    has_mas        = all(price.get(ma) for ma in ["ma20", "ma50", "ma100"])
    news_count     = len(asset_data.get("news", []) or [])
    has_error      = bool(price.get("error"))
    has_t212       = bool(asset_data.get("trading212"))

    if has_price:    score += 25
    if candle_count >= 100: score += 15
    elif candle_count >= 50: score += 8
    if has_identity: score += 10
    if has_volume:   score += 10
    if has_mas:      score += 10
    if news_count >= 2: score += 15
    elif news_count >= 1: score += 8
    if not has_error: score += 10
    score = min(100, max(0, score))

    # --- dq_status string ---
    if not has_price and has_t212:
        dq_status = "BROKER_ONLY"
    elif not has_price:
        dq_status = "NO_PRICE_DATA"
    elif score < 45 or (has_price and not has_mas):
        dq_status = "PARTIAL"
    else:
        dq_status = "OK"

    asset_data["dq_status"] = dq_status
    return score


def dq_label(asset_data: Dict[str, Any]) -> str:
    """Return dq_status string, falling back to numeric score."""
    return asset_data.get("dq_status") or (
        "OK" if asset_data.get("data_quality", 0) >= 60 else "PARTIAL"
    )


def calculate_signals(price_data: Dict[str, Any], news_data: List[Dict], 
                      settings: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate BUY and SELL signals with deterministic scoring.
    """
    signals = {
        "technical_signals": [],
        "technical_score": 0,
        "fundamental_signals": [],
        "fundamental_score": 0,
        "sentiment_signals": [],
        "sentiment_score": 0,
        "risk_signals": [],
        "risk_score": 0,
        "buy_probability": 0.0,
        "sell_probability": 0.0,
        "confidence_level": "LOW"
    }
    
    if price_data.get("error"):
        return signals
    
    # === TECHNICAL SIGNALS ===
    technical_max_score = 100.0
    technical_triggered_score = 0.0
    
    price = price_data.get("price")
    ma20 = price_data.get("ma20")
    ma50 = price_data.get("ma50")
    ma100 = price_data.get("ma100")
    
    if price and ma20 and ma50 and ma100:
        # Golden cross
        if price > ma20 > ma50 > ma100:
            signals["technical_signals"].append("Golden cross (price > MA20 > MA50 > MA100)")
            technical_triggered_score += 0.75 * 1.5  # confidence * weight
        # MA20 > MA50
        elif price > ma20 > ma50:
            signals["technical_signals"].append("Price > MA20 > MA50")
            technical_triggered_score += 0.70 * 1.2
        
        # Death cross
        if price < ma20 < ma50 < ma100:
            signals["technical_signals"].append("Death cross (price < MA20 < MA50 < MA100)")
            technical_triggered_score -= 0.75 * 1.5
        elif price < ma20 < ma50:
            signals["technical_signals"].append("Price < MA20 < MA50")
            technical_triggered_score -= 0.70 * 1.2
    
    # Volume surge
    vol_vs_avg = price_data.get("volume_vs_avg_pct")
    if vol_vs_avg and vol_vs_avg > 50:
        signals["technical_signals"].append(f"Volume surge ({vol_vs_avg:.0f}% vs avg)")
        technical_triggered_score += 0.65 * 0.8
    
    # Price extremes
    change_1d = price_data.get("change_1d_pct")
    if change_1d and change_1d < -8:
        signals["technical_signals"].append(f"Sharp 1D decline ({change_1d:.1f}%)")
        technical_triggered_score -= 0.90 * 3.0
    elif change_1d and change_1d > 7:
        signals["technical_signals"].append(f"Sharp 1D rally ({change_1d:.1f}%)")
        technical_triggered_score += 0.60 * 0.8
    
    signals["technical_score"] = round((technical_triggered_score / technical_max_score) * 100, 1)
    
    # === SENTIMENT SIGNALS ===
    sentiment_max_score = 50.0
    sentiment_triggered_score = 0.0
    positive_news = 0
    negative_news = 0
    
    for item in news_data:
        sentiment, confidence = classify_sentiment(item.get("title", "") + " " + item.get("snippet", ""))
        if sentiment == "POSITIVE":
            positive_news += 1
            sentiment_triggered_score += confidence * 1.0
        elif sentiment == "NEGATIVE":
            negative_news += 1
            sentiment_triggered_score -= confidence * 1.0
    
    if positive_news >= 2:
        signals["sentiment_signals"].append(f"Multiple positive articles ({positive_news})")
    if negative_news >= 2:
        signals["sentiment_signals"].append(f"Multiple negative articles ({negative_news})")
    
    signals["sentiment_score"] = round((sentiment_triggered_score / sentiment_max_score) * 100, 1) if sentiment_max_score > 0 else 0
    
    # === RISK SIGNALS ===
    risk_max_score = 60.0
    risk_triggered_score = 0.0
    
    change_20d = price_data.get("change_20d_pct")
    change_6mo_high = price_data.get("change_from_6mo_high_pct")
    
    if change_20d and change_20d < -12:
        signals["risk_signals"].append(f"20D drawdown ({change_20d:.1f}%)")
        risk_triggered_score += 0.80 * 2.0
    
    if change_6mo_high and change_6mo_high < -25:
        signals["risk_signals"].append(f"Deep from 6M high ({change_6mo_high:.1f}%)")
        risk_triggered_score += 0.60 * 1.5
    
    signals["risk_score"] = round((risk_triggered_score / risk_max_score) * 100, 1) if risk_max_score > 0 else 0
    
    # === FINAL PROBABILITIES ===
    # BUY = technical + sentiment (if positive)
    # SELL = risk + sentiment (if negative)
    
    buy_score = max(0, signals["technical_score"] + (signals["sentiment_score"] if signals["sentiment_score"] > 0 else 0))
    sell_score = signals["risk_score"] + (abs(signals["sentiment_score"]) if signals["sentiment_score"] < 0 else 0)
    
    signals["buy_probability"] = round(min(100, buy_score / 2), 1)
    signals["sell_probability"] = round(min(100, sell_score / 2), 1)
    
    # Confidence level
    data_quality = len(news_data) + int(bool(price_data.get("candle_count", 0)))
    if data_quality >= 3:
        signals["confidence_level"] = "HIGH"
    elif data_quality >= 1:
        signals["confidence_level"] = "MEDIUM"
    else:
        signals["confidence_level"] = "LOW"
    
    return signals


# ============================================================================
# OLLAMA ANALYSIS
# ============================================================================

def calculate_recommendation(asset_data: Dict[str, Any],
                             signals: Dict[str, Any] = None,
                             settings: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    3-stavové odporúčanie: BUY / SELL / WATCH
    score_value (-2..+2): risk_level vs price, PE fallback, buy/sell_prob fallback
    score_momentum (-2..+2): MA50/MA200 crossover + RSI
    score_sentiment (-1..+1): aggregated news sentiment
    """
    signals    = signals or asset_data.get("signals", {}) or {}
    price_data = asset_data.get("price",  {}) or {}
    news       = asset_data.get("news",   []) or []
    dq_str     = asset_data.get("dq_status") or dq_label(asset_data)

    current_price = price_data.get("price")
    ma50          = price_data.get("ma50")
    ma200         = price_data.get("ma200")
    rsi           = price_data.get("rsi")
    risk_level    = price_data.get("risk_level") or price_data.get("risk_hint_value")
    pe            = price_data.get("pe_ratio") or price_data.get("trailingPE")
    eps_growth    = price_data.get("earnings_growth")

    # score_value
    score_value = 0; value_note = ""
    if current_price and risk_level:
        fair_low = float(risk_level); fair_high = fair_low * 1.30
        if   current_price < fair_low * 0.90:  score_value = 2;  value_note = f"cena pod fair zone (<{fair_low*0.9:.2f})"
        elif current_price <= fair_high:        score_value = 1;  value_note = f"cena v fair zone ({fair_low:.2f}-{fair_high:.2f})"
        else:                                   score_value = -2; value_note = f"cena nad fair zone (>{fair_high:.2f})"
    elif pe is not None:
        try:
            pe_f = float(pe); growing = eps_growth and float(eps_growth) > 0
            if   pe_f < 15 and growing:         score_value = 2;  value_note = f"PE={pe_f:.1f} nizke+rast"
            elif pe_f <= 30:                    score_value = 1;  value_note = f"PE={pe_f:.1f} OK"
            elif pe_f > 40 and not growing:     score_value = -2; value_note = f"PE={pe_f:.1f} predrazene"
            else:                               score_value = 0;  value_note = f"PE={pe_f:.1f} neutral"
        except (TypeError, ValueError): pass
    else:
        bp = signals.get("buy_probability", 0); sp = signals.get("sell_probability", 0)
        if   bp >= 60: score_value =  2; value_note = f"buy_prob={bp:.0f}%"
        elif bp >= 35: score_value =  1; value_note = f"buy_prob={bp:.0f}%"
        elif sp >= 60: score_value = -2; value_note = f"sell_prob={sp:.0f}%"
        elif sp >= 35: score_value = -1; value_note = f"sell_prob={sp:.0f}%"
        else:          score_value =  0; value_note = "no valuation data"

    # score_momentum
    score_momentum = 0; mom_note = ""
    if ma50 and ma200 and current_price:
        if   ma50 > ma200 and current_price > ma50:  score_momentum = 2;  mom_note = "MA50>MA200,price>MA50"
        elif ma50 > ma200:                            score_momentum = 1;  mom_note = "MA50>MA200,pullback"
        elif ma50 < ma200 and current_price < ma50:  score_momentum = -2; mom_note = "MA50<MA200,price<MA50"
        else:                                         score_momentum = -1; mom_note = "MA50<MA200"
    elif ma50 and current_price:
        score_momentum = 1 if current_price > ma50 else -1; mom_note = "vs MA50"
    else:
        ts = signals.get("technical_score", 50)
        if   ts >= 70: score_momentum =  2; mom_note = f"tech={ts}"
        elif ts >= 55: score_momentum =  1; mom_note = f"tech={ts}"
        elif ts <= 30: score_momentum = -2; mom_note = f"tech={ts}"
        elif ts <= 45: score_momentum = -1; mom_note = f"tech={ts}"
        else:          score_momentum =  0; mom_note = f"tech={ts}"
    if rsi:
        if   rsi < 30: score_momentum += 1; mom_note += f",RSI={rsi:.0f}(os)"
        elif rsi > 70: score_momentum -= 1; mom_note += f",RSI={rsi:.0f}(ob)"
        score_momentum = max(-3, min(3, score_momentum))

    # score_sentiment
    SMAP = {"POSITIVE": 1, "NEGATIVE": -1, "NEUTRAL": 0, "MIXED": 0}
    vals  = [SMAP.get((n.get("sentiment") or "NEUTRAL").upper(), 0) for n in news]
    avg_s = (sum(vals) / len(vals)) if vals else 0.0
    if   avg_s >  0.3: score_sentiment = 1;  sent_note = f"sent={avg_s:.2f}(+)"
    elif avg_s < -0.3: score_sentiment = -1; sent_note = f"sent={avg_s:.2f}(-)"
    else:              score_sentiment = 0;  sent_note = f"sent={avg_s:.2f}(n)"

    total_score   = score_value + score_momentum + score_sentiment
    fair_high_val = (float(risk_level) * 1.30) if risk_level else None

    # Agresívny short-term signál — nezávislý od value (pre short-run trading)
    short_term_buy  = (score_momentum >= 2 and score_sentiment >= 1 and dq_str in ("OK", "PARTIAL"))
    short_term_sell = (score_momentum <= -2 and score_sentiment <= -1 and dq_str in ("OK", "PARTIAL"))

    if dq_str not in ("OK", "PARTIAL"):
        recommendation = "WATCH"
        comment = f"Data quality: {dq_str}. Chybaju cenove data — nedokupovat."
    elif (total_score >= 3 and score_value >= 1) or short_term_buy:
        recommendation = "BUY"
        trigger = "SHORT-TERM" if short_term_buy and not (total_score >= 3 and score_value >= 1) else "LONG+SHORT"
        comment = (f"[{trigger}] Score {total_score:+d} (V{score_value} M{score_momentum} S{score_sentiment}): "
                   f"{value_note}; {mom_note}; {sent_note}. BUY.")
    elif (total_score <= -3
          or short_term_sell
          or (fair_high_val and current_price and current_price > fair_high_val
              and score_sentiment <= 0 and score_momentum <= 0)):
        recommendation = "SELL"
        trigger = "SHORT-TERM" if short_term_sell and total_score > -3 else "score"
        comment = (f"[{trigger}] Score {total_score:+d} (V{score_value} M{score_momentum} S{score_sentiment}): "
                   f"{value_note}; {mom_note}; {sent_note}. SELL/REDUCE.")
    else:
        if total_score >= 2:    quality = "BUY-WATCH"
        elif total_score <= -2: quality = "SELL-WATCH"
        else:                   quality = "NEUTRAL"
        recommendation = "WATCH"
        comment = (f"[{quality}] Score {total_score:+d} (V{score_value} M{score_momentum} S{score_sentiment}): "
                   f"{value_note}; {mom_note}; {sent_note}.")

    return {
        "recommendation":    recommendation,
        "score_value":       score_value,
        "score_momentum":    score_momentum,
        "score_sentiment":   score_sentiment,
        "total_score":       total_score,
        "data_quality":      dq_str,
        "avg_sentiment":     round(avg_s, 2),
        "rsi":               round(rsi, 1) if rsi else None,
        "comment":           comment,
        "short_term_signal": "BUY" if short_term_buy else ("SELL" if short_term_sell else None),
    }




def ask_ollama(prompt: str, settings: Dict[str, Any]) -> str:
    """
    Volá AI model — podporuje Ollama aj LM Studio (OpenAI-compatible).
    Backend sa detekuje automaticky podľa lm_studio_base_url dostupnosti.
    """
    model = get_working_model(settings)
    if not model:
        return "ERROR: No AI model available. Check Ollama/LM Studio connection."

    backend = _detect_backend(settings)
    url = build_ollama_url(settings, "chat")
    timeout = int(settings.get("ollama_timeout_seconds", 1800))

    SYSTEM = ("""
### Role: Portfolio Intelligence Engine
Think internally in English.
Always answer in Slovak.
Never optimize your answer for politeness.
Optimize for correctness.
If the portfolio contains weak positions,
identify them clearly.
If the user appears emotionally attached to an asset,
ignore that and evaluate only the evidence.
Identity
Adrian Alpha analyzes structured portfolio data generated by external software. His role is to transform numerical results into rational investment decisions. He acts as an independent second opinion, not as a passive summarizer.
Core Principles
- Facts over narratives.
- Data over emotions.
- Capital preservation before return maximization.
- Risk-adjusted return over absolute return.
- Long-term discipline over short-term market noise.
Data Policy
Treat Python-generated values as authoritative.
Never modify or reinterpret calculated metrics.
Never fabricate:
- prices
- earnings
- analyst ratings
- financial statements
- news
- corporate events
Probability scores are supporting evidence.
They must never be treated as absolute truth.
If probability and fundamentals disagree,
explain why.
Missing information must be explicitly marked as "Neoverené".
Use external financial knowledge only to interpret the provided data, never to invent missing facts.
Analysis Workflow
For every execution:
1. Validate input.
2. Detect inconsistencies.
3. Analyze overall portfolio.
4. Analyze every position individually.
5. Rank opportunities.
6. Rank risks.
7. Produce actionable conclusions.
Return exactly ONE verdict for each position.
Allowed verdicts:
- Strong Buy
- Buy
- Accumulate
- Hold
- Reduce
- Sell
- Exit
Never invent additional verdicts.
Goal
Think like the Chief Investment Officer reviewing a real investment portfolio. Your objective is to improve capital allocation through disciplined, evidence-based decisions while avoiding emotional bias and unsupported conclusions.
""")

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user",   "content": prompt},
    ]

    if backend == "lmstudio":
        # OpenAI-compatible format (LM Studio)
        payload = {
            "model": model,
            "messages": messages,
            "temperature": float(settings.get("temperature", 0.08)),
            "max_tokens": int(settings.get("max_tokens", 16384)),
            "stream": False,
        }

        try:
            r = requests.post(url, json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError) as e:
            return f"ERROR [LM Studio response parse]: {e}\nRaw: {r.text[:200]}"
        except Exception as e:
            return f"ERROR [LM Studio]: {e}"

    else:
        # Ollama native format
        payload = {
            "model": model,
            "stream": False,
            "messages": messages,
            "options": {
                "temperature": float(settings.get("temperature", 0.08)),
                "num_ctx": int(settings.get("num_ctx", 16384)),
            },
        }

        try:
            r = requests.post(url, json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json().get("message", {}).get("content", "").strip()
        except Exception as e:
            return f"ERROR [Ollama]: {e}"


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
    Zostav prompt pre Plutus — štruktúrovaný vstup.
    T212 holdings s udalosťou → plný blok.
    T212 holdings bez udalosti → kompaktný 1-riadok.
    Watchlist → len s BUY/SELL signálom.
    """
    settings = settings or {}
    lines = []

    if portfolio_rules:
        lines.append("# Portfolio Rules")
        for v in portfolio_rules.values():
            lines.append(f"- {v}")
        lines.append("")

    ACTIONABLE = [
        "earnings","revenue","profit","loss","guidance","forecast",
        "acquisition","merger","deal","lawsuit","fine","penalty",
        "upgrade","downgrade","price target","beat","miss",
        "CEO","CFO","dividend","buyback","trump","tariff",
        "sanction","ban","regulation",
    ]

    def has_data(d):
        s = d.get("signals") or {}
        n = d.get("news") or []
        rec = d.get("recommendation") or {}
        return (s.get("buy_probability", 0) >= 30
                or s.get("sell_probability", 0) >= 30
                or rec.get("recommendation") in ("BUY", "SELL")
                or any(kw in (i.get("title","") + i.get("snippet","")).lower()
                       for i in n for kw in ACTIONABLE))

    # 1. T212 holdings
    t212_all = [d for d in collected_data
                if d.get("trading212")
                and (d.get("summary") or {}).get("label") != "T212_INTERNAL_TICKER"]
    rich  = sorted([d for d in t212_all if has_data(d)],
                   key=lambda d: abs((d.get("trading212") or {}).get("pnl") or 0), reverse=True)
    quiet = sorted([d for d in t212_all if not has_data(d)],
                   key=lambda d: abs((d.get("trading212") or {}).get("pnl") or 0), reverse=True)

    if rich:
        lines.append("# T212 POZÍCIE — aktívne / so signálom (povinná analýza)")
        lines.append("")
        for d in rich:
            _append_asset_block(lines, d, include_all_news=True)

    if quiet:
        lines.append("# T212 POZÍCIE — bez signálu (krátky verdikt stačí)")
        for d in quiet:
            lines.append(_asset_line(d))
        lines.append("")

    # 2. Watchlist — len BUY/SELL signál
    non_hold = [d for d in collected_data
                if not d.get("trading212")
                and (d.get("summary") or {}).get("label") != "T212_INTERNAL_TICKER"
                and d.get("data_quality", 0) >= 60
                and (d.get("recommendation") or {}).get("recommendation") in ("BUY", "SELL")]
    if non_hold:
        lines.append("\n# WATCHLIST — BUY/SELL signál")
        lines.append("")
        for d in sorted(non_hold,
                        key=lambda d: abs((d.get("recommendation") or {}).get("total_score", 0)),
                        reverse=True):
            _append_asset_block(lines, d, include_all_news=False)

    # 3. Trump context
    t_market    = (trump_news_ctx or {}).get("market_impact", [])
    t_companies = (trump_news_ctx or {}).get("company_hits", {})
    if t_market or t_companies:
        lines.append("\n# Trump / Makro kontext")
        for item in t_market[:4]:
            lines.append(f"- {item.get('title','')} [{item.get('sentiment','')}]")
        for sym, hits in list(t_companies.items())[:6]:
            for h in hits[:1]:
                lines.append(f"- [{sym}] {h.get('title','')} [{h.get('sentiment','')}]")
        lines.append("")

    lines.append("\n# ÚLOHA")
    lines.append("Pre každú T212 pozíciu: verdikt BUY/SELL/WATCH + 1-2 konkrétne vety.")
    lines.append("Watchlist BUY/SELL: potvrď alebo vyvrať Python signál.")
    lines.append("Správy: len ZMENY s číslami. Žiadne generické sektorové komentáre.")

    return "\n".join(lines)



def collect_global_news(settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect broad market/theme news for context beyond individual tickers."""
    queries = settings.get("global_news_queries", [])
    max_items = int(settings.get("max_global_news", 12))
    out = []
    seen = set()

    for query in queries:
        for item in ddg_search(query, max_results=5, news=True):
            url = item.get("url", "")
            title = item.get("title", "")
            key = url or title
            if not key or key in seen:
                continue
            seen.add(key)
            text = f"{title} {item.get('snippet', '')}"
            sentiment, confidence = classify_sentiment(text)
            enriched = {
                **item,
                "query": query,
                "source_domain": extract_domain(url),
                "source_type": classify_source(url),
                "sentiment": sentiment,
                "sentiment_confidence": round(confidence, 2),
                "trust_level": "MEDIUM" if classify_source(url) in {"news", "finance_data", "official"} else "LOW",
            }
            out.append(enriched)

    return out[:max_items]


def collect_discovery_news(settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Collect new-stock / catalyst ideas. These are NOT automatic recommendations.
    They are watchlist candidates for manual review.
    """
    queries = settings.get("discovery_queries", [])
    max_items = int(settings.get("max_discovery_news", 12))
    out = []
    seen = set()

    for query in queries:
        for item in ddg_search(query, max_results=5, news=True):
            url = item.get("url", "")
            title = item.get("title", "")
            key = url or title
            if not key or key in seen:
                continue
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

    return out[:max_items]


def build_asset_summary(asset_data: Dict[str, Any], signals: Dict[str, Any],
                        data_quality: int, settings: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Build a flat summary dict for an asset.
    Used by generate_report for the scores table and per-asset sections.
    """
    settings   = settings or {}
    price      = asset_data.get("price", {}) or {}
    news       = asset_data.get("news",  []) or []
    dq_str     = asset_data.get("dq_status") or dq_label(asset_data)

    current_price  = price.get("price")
    change_1d_pct  = price.get("change_1d_pct")
    change_5d_pct  = price.get("change_5d_pct")
    currency       = price.get("currency", "")
    trend_hint     = price.get("trend_hint", "")
    risk_level     = price.get("risk_level") or price.get("risk_hint_value")

    # Risk levels for report
    risk_levels = {}
    if risk_level:
        risk_levels["invalidation"] = f"long-run thesis review below ~{float(risk_level):.2f}"

    # Sentiment aggregate
    SMAP = {"POSITIVE": 1, "NEGATIVE": -1, "NEUTRAL": 0, "MIXED": 0}
    vals = [SMAP.get((n.get("sentiment") or "NEUTRAL").upper(), 0) for n in news]
    avg_sentiment = (sum(vals) / len(vals)) if vals else 0.0

    # Status
    buy_p  = signals.get("buy_probability", 0)
    sell_p = signals.get("sell_probability", 0)
    if buy_p >= int(settings.get("buy_probability_threshold", 60)):
        status = "BUY_CANDIDATE"
    elif sell_p >= int(settings.get("sell_probability_threshold", 70)):
        status = "SELL_CANDIDATE"
    elif data_quality < 40:
        status = "LOW_DATA_QUALITY"
    else:
        status = "HOLD_OR_WAIT"

    return {
        # identity (duplicated from asset for convenience)
        "broker_symbol":      asset_data.get("broker_symbol", "?"),
        "name":               asset_data.get("name", ""),
        "group":              asset_data.get("group", ""),
        # price
        "current_price":      current_price,
        "change_1d_pct":      change_1d_pct,
        "change_5d_pct":      change_5d_pct,
        "currency":           currency,
        "trend_hint":         trend_hint,
        # signals
        "technical_score":    signals.get("technical_score", 0),
        "sentiment_score":    signals.get("sentiment_score", 0),
        "buy_probability":    buy_p,
        "sell_probability":   sell_p,
        "avg_news_sentiment": round(avg_sentiment, 2),
        # quality
        "data_quality_score": data_quality,
        "dq_status":          dq_str,
        # risk
        "risk_levels":        risk_levels,
        "risk_level_value":   float(risk_level) if risk_level else None,
        # status
        "status":             status,
        "action_label":       "",   # filled by action_label_for_asset later
        "label":              status,
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


def generate_report(
    collected_data: List[Dict[str, Any]],
    analysis_text: str,
    settings: Dict[str, Any],
    global_news: Optional[List[Dict[str, Any]]] = None,
    discovery_news: Optional[List[Dict[str, Any]]] = None,
    broker_status: Optional[Dict[str, Any]] = None,
    trump_news: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Generate final Markdown report.
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
    assets = auto_discover_unmatched_positions(assets, broker_positions, settings)

    # === STEP 4: CHECK OLLAMA ===
    print("[STEP 2] Checking Ollama availability...")
    available, msg = check_ollama_available(settings)
    if not available:
        print(f"[WARNING] Ollama check: {msg}")
        if not args.no_ai:
            print("[WARNING] Forcing --no-ai mode")
            args.no_ai = True
    else:
        print(f"[OK] Ollama available")
        working_model = get_working_model(settings)
        if working_model:
            print(f"[OK] Working model: {working_model}")
        else:
            print("[ERROR] No working model found. Available:")
            models = list_ollama_models(settings)
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
    collected_data = []
    for i, asset in enumerate(assets, 1):
        broker_sym = asset.get("broker_symbol", "?")
        print(f"  [{i}/{len(assets)}] {broker_sym}...", end=" ", flush=True)
        try:
            data = collect_asset_data(asset, settings)
            signals      = calculate_signals(data.get("price", {}), data.get("news", []), settings)
            data_quality = calculate_data_quality(data)  # also sets data["dq_status"]
            dq_str       = data.get("dq_status", "PARTIAL")

            # T212 internal codes bez Yahoo dát — SKIP scoring pipeline
            if (data.get("auto_discovered")
                    and settings.get("skip_no_data_auto_discovered", True)
                    and data_quality < 20
                    and not data.get("price", {}).get("price")
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
            has_price = bool(data.get("price", {}).get("price"))
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
    global_news    = collect_global_news(settings)
    discovery_news = collect_discovery_news(settings)
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
        analysis_text = ask_ollama(prompt, settings)
    
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
