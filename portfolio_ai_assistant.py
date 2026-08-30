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
import requests_cache
# Ak nepoužívaš requests_cache, stačí povedať yfinance aby globálne používal túto session:
yf.utils.get_http_session = lambda: session
# -------------------------------------------------------------------------

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

try:
    from ddgs import DDGS
except Exception:
    try:
        from duckduckgo_search import DDGS
    except Exception:
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
                positions.append({
                    "broker": "Trading212",
                    "broker_symbol": (
                        item.get("ticker")
                        or item.get("symbol")
                        or item.get("instrumentCode")
                        or item.get("shortName")
                    ),
                    "quantity": item.get("quantity"),
                    "average_price": item.get("averagePrice") or item.get("average_price"),
                    "current_price": item.get("currentPrice") or item.get("current_price"),
                    "market_value": item.get("value") or item.get("marketValue") or item.get("market_value"),
                    "pnl": item.get("ppl") or item.get("pnl") or item.get("profitLoss"),
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


def merge_broker_data(assets, broker_positions):
    """Merge broker position data with portfolio assets."""
    enhanced_assets = []
    
    for asset in assets:
        broker_symbol = asset.get("broker_symbol", "").upper()
        enhanced = asset.copy()
        
        for pos in broker_positions.get("trading212", []):
            if pos.get("broker_symbol", "").upper() == broker_symbol:
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


# ============================================================================
# OLLAMA/MODEL MANAGEMENT
# ============================================================================

def build_ollama_url(settings: Dict[str, Any], endpoint: str = "chat") -> str:
    """Build full Ollama API URL from settings."""
    base_url = (
        settings.get("ollama_base_url")
        or settings.get("ollama_url")
        or "http://127.0.0.1:11434"
    ).rstrip("/")

    if endpoint == "chat":
        ep = settings.get("ollama_chat_endpoint", "/api/chat")
    elif endpoint == "tags":
        ep = settings.get("ollama_tags_endpoint", "/api/tags")
    else:
        ep = endpoint

    if not ep.startswith("/"):
        ep = "/" + ep

    return base_url + ep


def list_ollama_models(settings: Dict[str, Any]) -> Optional[List[str]]:
    """Fetch list of available Ollama models."""
    try:
        url = build_ollama_url(settings, "tags")
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        models = [m.get("name", "") for m in data.get("models", [])]
        return sorted(models)
    except Exception as e:
        return None


def validate_model_available(settings: Dict[str, Any], model_name: str) -> bool:
    """Check if a specific model is available in Ollama."""
    models = list_ollama_models(settings)
    if models is None:
        return False
    return any(model_name in m for m in models)


def check_ollama_available(settings: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Check if Ollama is reachable. Returns (is_available, message).
    """
    try:
        url = build_ollama_url(settings, "tags")
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        return True, "Ollama reachable"
    except requests.ConnectionError:
        return False, f"Cannot connect to Ollama at {settings.get('ollama_url')}"
    except Exception as e:
        return False, f"Ollama check failed: {e}"


def get_working_model(settings: Dict[str, Any]) -> Optional[str]:
    """
    Find a working model (primary or fallback).
    Returns model name if found, None otherwise.
    """
    primary = settings.get("model")
    fallback = settings.get("fallback_model")
    
    if primary and validate_model_available(settings, primary):
        return primary
    
    if fallback and validate_model_available(settings, fallback):
        return fallback
    
    # List all available and pick first
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
        return [{"title": "Missing duckduckgo-search", "snippet": "Install: pip install duckduckgo-search"}]
    
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


def collect_asset_data(asset: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    """Collect all data for an asset."""
    broker_symbol = asset.get("broker_symbol", "")
    yahoo_symbol = asset.get("yahoo_symbol", "")
    name = asset.get("name", "")
    search_query = asset.get("search_query", name or broker_symbol)

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

    news_raw = ddg_search(f"{search_query} stock news earnings", max_results=max_news * 2, news=True)
    web_raw = ddg_search(f"{search_query} financial analysis", max_results=max_web * 2, news=False)

    news, news_excluded = filter_and_score_news(news_raw, asset, max_news)
    web, web_excluded = filter_and_score_news(web_raw, asset, max_web)

    return {
        "broker_symbol": broker_symbol,
        "yahoo_symbol": used_yahoo_symbol or yahoo_symbol,
        "configured_yahoo_symbol": yahoo_symbol,
        "name": name,
        "group": asset.get("group", "PORTFOLIO"),
        "price": price_data,
        "news": news,
        "news_excluded": news_excluded,
        "web": web,
        "web_excluded": web_excluded,
        "symbol_candidates": symbol_candidates,
        "tradingview": fetch_tradingview_technical_data(asset.get("tradingview_symbol", "")) if settings.get("use_tradingview_public_data", True) else None,
        "collected_at": datetime.now().isoformat()
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
    """
    score = 0
    price = asset_data.get("price", {})
    
    # Price available: +25
    if price.get("price"):
        score += 25
    
    # Sufficient historical data (100+ candles): +15
    if price.get("candle_count", 0) >= 100:
        score += 15
    elif price.get("candle_count", 0) >= 50:
        score += 8
    
    # Company identity confirmed: +10
    if price.get("currency") or asset_data.get("name"):
        score += 10
    
    # Market cap or volume: +10
    if price.get("market_cap") or price.get("volume"):
        score += 10
    
    # All moving averages present: +10
    if all(price.get(ma) for ma in ["ma20", "ma50", "ma100"]):
        score += 10
    
    # Relevant news found: +15
    news_count = len(asset_data.get("news", []))
    if news_count >= 2:
        score += 15
    elif news_count >= 1:
        score += 8
    
    # No obvious data issues: +10
    if not price.get("error"):
        score += 10
    
    return min(100, max(0, score))


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

def ask_ollama(prompt: str, settings: Dict[str, Any]) -> str:
    """Call Ollama with prompt."""
    model = get_working_model(settings)
    if not model:
        return "ERROR: No Ollama model available. Please check installation."
    
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a strict, factual investment analyst. "
                    "You NEVER invent probabilities, prices, company names, or earnings. "
                    "You NEVER change calculated scores. "
                    "You NEVER override Python-computed buy_probability or sell_probability. "
                    "If data quality is low, you say 'neoverené' (unverified). "
                    "You work only with structured data provided. "
                    "Speak in Slovak."
                )
            },
            {"role": "user", "content": prompt}
        ],
        "options": {
            "temperature": float(settings.get("temperature", 0.15)),
            "num_ctx": int(settings.get("num_ctx", 16384))
        }
    }
    
    try:
        url = build_ollama_url(settings, "chat")
        r = requests.post(url, json=payload, timeout=int(settings.get("ollama_timeout_seconds", 600)))
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "").strip()
    except Exception as e:
        return f"ERROR calling Ollama: {e}"


def build_asset_summary(asset_data: Dict[str, Any], signals: Dict[str, Any], 
                       data_quality: int, settings: Dict[str, Any]) -> Dict[str, Any]:
    """Build structured summary for one asset."""
    price = asset_data.get("price", {})
    min_dq = settings.get("min_data_quality_for_signal", 60)
    buy_threshold = settings.get("buy_probability_threshold", 60)
    sell_threshold = settings.get("sell_probability_threshold", 70)
    
    summary = {
        "broker_symbol": asset_data.get("broker_symbol"),
        "yahoo_symbol": asset_data.get("yahoo_symbol"),
        "name": asset_data.get("name"),
        "group": asset_data.get("group"),
        "current_price": price.get("price"),
        "currency": price.get("currency"),
        "change_1d_pct": price.get("change_1d_pct"),
        "change_5d_pct": price.get("change_5d_pct"),
        "trend": price.get("trend_hint"),
        "risk": price.get("risk_hint"),
        "technical_score": signals.get("technical_score", 0),
        "fundamental_score": signals.get("fundamental_score", 0),
        "sentiment_score": signals.get("sentiment_score", 0),
        "risk_score": signals.get("risk_score", 0),
        "buy_probability": signals.get("buy_probability", 0),
        "sell_probability": signals.get("sell_probability", 0),
        "data_quality_score": data_quality,
        "confidence_level": signals.get("confidence_level", "LOW"),
        "status": "NO_SIGNAL"
    }
    
    # Determine status
    if data_quality < min_dq:
        summary["status"] = "NO_SIGNAL_LOW_DATA_QUALITY"
    elif price.get("error"):
        summary["status"] = "DATA_ERROR"
    elif summary["buy_probability"] >= buy_threshold:
        summary["status"] = "BUY_CANDIDATE"
    elif summary["sell_probability"] >= sell_threshold:
        summary["status"] = "SELL_CANDIDATE"
    elif summary["buy_probability"] >= 40:
        summary["status"] = "WATCH_BUY"
    elif summary["sell_probability"] >= 50:
        summary["status"] = "WATCH_SELL"
    else:
        summary["status"] = "HOLD"
    
    return summary


def build_prompt(collected_data: List[Dict[str, Any]], portfolio_rules: Dict[str, str],
                 settings: Dict[str, Any]) -> str:
    """Build analysis prompt for Ollama."""
    lines = [
        "You are a careful investment analyst. Use ONLY the structured data provided.",
        "CRITICAL: Never override the calculated buy_probability or sell_probability.",
        "CRITICAL: Never invent probabilities, prices, or earnings data.",
        "If data quality is low, mark it as 'neoverené' (unverified).",
        "Speak in Slovak.",
        ""
    ]
    
    lines.append("# Portfolio Rules")
    for k, v in portfolio_rules.items():
        lines.append(f"- {v}")
    
    lines.append("\n# Assets Analysis\n")

    # Keep the local model prompt small. Raw data stays in JSON.
    ai_max_assets = int(settings.get("ai_max_assets", 12))
    ranked_data = sorted(
        collected_data,
        key=lambda d: (
            d.get("data_quality", 0),
            abs(d.get("summary", {}).get("change_1d_pct") or 0),
            d.get("summary", {}).get("sentiment_score", 0),
            len(d.get("news", [])),
        ),
        reverse=True,
    )[:ai_max_assets]

    for data in ranked_data:
        broker_sym = data.get("broker_symbol", "?")
        yahoo_sym = data.get("yahoo_symbol", "?")
        name = data.get("name", "?")
        
        lines.append(f"## {broker_sym} ({name})")
        lines.append(f"**Yahoo Symbol: {yahoo_sym}**\n")
        
        price = data.get("price", {})
        if price.get("error"):
            lines.append(f"⚠️ DATA ERROR: {price.get('error')}\n")
            continue
        
        lines.append(f"- Price: {fmt(price.get('price'))} {price.get('currency', '')}")
        lines.append(f"- 1D: {fmt(price.get('change_1d_pct'), 2, '%')} | 5D: {fmt(price.get('change_5d_pct'), 2, '%')}")
        lines.append(f"- Trend: {price.get('trend_hint', 'N/A')}")
        lines.append(f"- Risk: {price.get('risk_hint', 'N/A')}\n")
        
        signals = data.get("signals", {})
        dq = data.get("data_quality", 0)
        
        lines.append(f"**Scores:**")
        lines.append(f"- Technical: {signals.get('technical_score', 0)}/100")
        lines.append(f"- Sentiment: {signals.get('sentiment_score', 0)}/100")
        lines.append(f"- Risk: {signals.get('risk_score', 0)}/100")
        lines.append(f"- Data Quality: {dq}/100")
        lines.append(f"- **BUY Probability: {signals.get('buy_probability', 0)}%**")
        lines.append(f"- **SELL Probability: {signals.get('sell_probability', 0)}%**\n")
        
        if signals.get("technical_signals"):
            lines.append("Technical Signals:")
            for sig in signals.get("technical_signals", []):
                lines.append(f"  • {sig}")
            lines.append("")
        
        # --- T212 actual holdings ---
        t212 = data.get("trading212")
        if t212:
            qty     = t212.get("quantity")
            avg_p   = t212.get("average_price")
            curr_p  = t212.get("current_price")
            pnl     = t212.get("pnl")
            pnl_pct = t212.get("pnl_pct")
            val     = t212.get("market_value") or ((curr_p * qty) if curr_p and qty else None)
            lines.append("**Moja pozícia (Trading212):**")
            if qty    is not None: lines.append(f"  \u2022 Qty      : {qty}")
            if avg_p  is not None: lines.append(f"  \u2022 Avg cena : {avg_p:.4f}")
            if curr_p is not None: lines.append(f"  \u2022 Aktuálna : {curr_p:.4f}")
            if val    is not None: lines.append(f"  \u2022 Hodnota  : {val:.2f}")
            if pnl    is not None:
                sign    = "+" if pnl >= 0 else ""
                pct_str = f" ({sign}{pnl_pct:.1f}%)" if pnl_pct is not None else ""
                lines.append(f"  \u2022 P&L      : {sign}{pnl:.2f}{pct_str}")
            lines.append("")

        if data.get("news"):
            lines.append("Top News:")
            for item in data.get("news", [])[:2]:
                lines.append(f"  \u2022 {item.get('title', '')}")
            lines.append("")
    
    lines.append("\n# Your Task")
    lines.append("1. Analyze each asset based on the scores above.")
    lines.append("2. DO NOT change any calculated probability.")
    lines.append("3. Give a compact Slovak tidy-up: today\'s catalysts, long-run watch, short-run watch, risk review.")
    lines.append("4. Do not create fake buy/sell candidates if Python score is low.")
    lines.append("5. Mention concrete news/catalysts and invalidation levels when available.")
    lines.append("6. Pre každú pozíciu kde mám reálne holdings (T212 sekcia): okomentuj P&L a či má zmysel držať, doložiť, alebo zvážiť výstup.")
    lines.append("7. Ak je pozícia vo výraznej strate (pnl_pct < -15%), explicitne to zmieň a navrhni postup.")
    
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
    analysis: str,
    settings: Dict[str, Any],
    global_news: Optional[List[Dict[str, Any]]] = None,
    discovery_news: Optional[List[Dict[str, Any]]] = None,
    broker_status: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate final markdown report focused on actions, news, and risk."""
    global_news = global_news or []
    discovery_news = discovery_news or []
    broker_status = broker_status or {}

    lines = [
        "# Portfolio Analysis Report",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "Data Sources: yfinance, DuckDuckGo news search, optional TradingView public checks, optional Trading 212 read-only API",
        "",
        "## Broker Sync Status",
    ]

    if broker_status:
        ok = "OK" if broker_status.get("ok") else "FAILED/DISABLED"
        lines.append(f"- Trading 212: **{ok}**")
        if broker_status.get("endpoint_used"):
            lines.append(f"- Endpoint used: `{broker_status.get('endpoint_used')}`")
        lines.append(f"- Positions loaded: {broker_status.get('positions_count', 0)}")
        if broker_status.get("error"):
            lines.append(f"- Warning: {broker_status.get('error')}")
    else:
        lines.append("- Trading 212: not attempted / disabled")
    lines.append("")

    # Summary statistics
    total = len(collected_data)
    buy_count = sum(1 for d in collected_data if d.get("summary", {}).get("status") == "BUY_CANDIDATE")
    sell_count = sum(1 for d in collected_data if d.get("summary", {}).get("status") == "SELL_CANDIDATE")
    low_dq = sum(1 for d in collected_data if d.get("data_quality", 100) < 60)

    lines.append("## Summary")
    lines.append(f"- Total assets monitored: {total}")
    lines.append(f"- Strong BUY candidates by strict Python score: {buy_count}")
    lines.append(f"- Strong SELL candidates by strict Python score: {sell_count}")
    lines.append(f"- Low data quality assets: {low_dq}")
    lines.append("- Note: low BUY count does not mean no opportunities; this score is intentionally conservative.")
    lines.append("")

    # General market/news section
    lines.append("## Dnešné všeobecné novinky / market radar")
    if global_news:
        lines.extend(format_news_items(global_news, limit=8))
    else:
        lines.append("- No broad market news collected.")
    lines.append("")

    # Discovery ideas
    lines.append("## Nové / externé watchlist nápady")
    lines.append("Tieto položky nie sú automatické odporúčania. Sú to iba katalyzátory na manuálne overenie.")
    if discovery_news:
        lines.extend(format_news_items(discovery_news, limit=8))
    else:
        lines.append("- No external discovery items collected.")
    lines.append("")

    # Enrich each asset with action labels/levels
    for d in collected_data:
        d.setdefault("summary", {})
        d["summary"]["action_label"] = action_label_for_asset(d)
        d["summary"]["risk_levels"] = compute_stop_levels(d)

    # Long-run/DCA watchlist
    long_run = [
        d for d in collected_data
        if d.get("group") in {"LONG_RUN_DCA", "TECH_PIE"}
        and d.get("data_quality", 0) >= 60
    ]
    long_run.sort(
        key=lambda d: (
            d.get("summary", {}).get("buy_probability", 0),
            d.get("summary", {}).get("sentiment_score", 0),
            d.get("data_quality", 0)
        ),
        reverse=True
    )

    lines.append("## LONG-RUN / DCA watchlist")
    for d in long_run[:8]:
        s = d.get("summary", {})
        levels = s.get("risk_levels", {})
        lines.append(
            f"- **{s.get('broker_symbol')} / {s.get('name')}** — label: `{s.get('action_label')}`, "
            f"BUY {s.get('buy_probability')}%, SELL {s.get('sell_probability')}%, "
            f"Data QA {s.get('data_quality_score')}/100, "
            f"invalidation: {levels.get('invalidation')}"
        )
    lines.append("")

    # Short-term/intraday watchlist
    short_term = [
        d for d in collected_data
        if d.get("data_quality", 0) >= 60 and (
            d.get("group") == "SHORT_TERM_TRADING"
            or abs(d.get("summary", {}).get("change_1d_pct") or 0) >= 4
            or d.get("summary", {}).get("buy_probability", 0) >= 20
        )
    ]
    short_term.sort(
        key=lambda d: (
            abs(d.get("summary", {}).get("change_1d_pct") or 0),
            d.get("summary", {}).get("sentiment_score", 0),
            d.get("summary", {}).get("buy_probability", 0),
        ),
        reverse=True
    )

    lines.append("## SHORT-RUN / intraday watchlist")
    for d in short_term[:8]:
        s = d.get("summary", {})
        levels = s.get("risk_levels", {})
        lines.append(
            f"- **{s.get('broker_symbol')}** — 1D {fmt(s.get('change_1d_pct'), 1, '%')}, "
            f"label: `{s.get('action_label')}`, stop/invalidation: {levels.get('stop_loss')}, "
            f"take-profit watch: {levels.get('take_profit')}"
        )
    lines.append("")

    # Asset scores table
    lines.append("## Asset Scores Table")
    lines.append("| Symbol | Name | Group | Price | 1D | Technical | Sentiment | Buy % | Sell % | Data QA | Label |")
    lines.append("|--------|------|-------|-------|----|-----------|-----------|-------|--------|---------|-------|")

    for d in collected_data:
        s = d.get("summary", {})
        symbol = s.get("broker_symbol", "?")
        name = (s.get("name") or "?").replace("|", "/")
        group = (s.get("group") or "?").replace("|", "/")
        price = fmt(s.get("current_price"), 2)
        change_1d = fmt(s.get("change_1d_pct"), 1, "%")
        tech = s.get("technical_score", 0)
        sentiment = s.get("sentiment_score", 0)
        buy_prob = s.get("buy_probability", 0)
        sell_prob = s.get("sell_probability", 0)
        dq = s.get("data_quality_score", 0)
        label = s.get("action_label", "")
        lines.append(f"| {symbol} | {name} | {group} | {price} | {change_1d} | {tech} | {sentiment} | {buy_prob:.0f} | {sell_prob:.0f} | {dq} | {label} |")

    lines.append("")

    # Per-asset news
    lines.append("## Novinky podľa sledovaných aktív")
    for d in collected_data:
        s = d.get("summary", {})
        news = d.get("news", [])
        if not news:
            continue
        lines.append(f"### {s.get('broker_symbol')} — {s.get('name')}")
        lines.extend(format_news_items(news, limit=3))
        levels = s.get("risk_levels", {})
        lines.append(f"- Risk level: {levels.get('invalidation')}")
        lines.append("")

    # AI Analysis
    lines.append("## Qwen/Ollama tidy-up")
    lines.append(analysis)
    lines.append("")

    # Data quality notes
    lines.append("## Data Quality Notes")
    low_quality_assets = [d for d in collected_data if d.get("data_quality", 100) < 60]
    if low_quality_assets:
        lines.append("⚠️ Low data quality assets (excluded from top candidates):")
        for d in low_quality_assets:
            s = d.get("summary", {})
            tried = ", ".join(d.get("price", {}).get("symbols_tried", []) or d.get("symbol_candidates", []))
            err = d.get("price", {}).get("error", "Low news coverage")
            lines.append(f"- {s.get('broker_symbol')}: {s.get('data_quality_score')}/100 — {err}. Tried: {tried}")
    else:
        lines.append("- No low data quality assets.")

    return "\n".join(lines)


# ============================================================================
# OUTPUT & STORAGE
# ============================================================================

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
            signals = calculate_signals(data.get("price", {}), data.get("news", []), settings)
            data_quality = calculate_data_quality(data)
            
            summary = build_asset_summary(data, signals, data_quality, settings)
            data["signals"] = signals
            data["data_quality"] = data_quality
            data["summary"] = summary
            
            collected_data.append(data)
            print("[OK]")
        except Exception as e:
            print(f"[ERROR: {e}]")
    
    # === STEP 7: GENERATE REPORTS ===
    print(f"\n[STEP 5] Collecting broad market/discovery news...")
    global_news = collect_global_news(settings)
    discovery_news = collect_discovery_news(settings)
    print(f"  Global news: {len(global_news)} | Discovery ideas: {len(discovery_news)}")

    print(f"\n[STEP 6] Generating reports...")
    
    # Build JSON output
    json_output = {
        "timestamp": datetime.now().isoformat(),
        "config": {
            "model": get_working_model(settings) or settings.get("model"),
            "min_data_quality": settings.get("min_data_quality_for_signal", 60),
            "buy_threshold": settings.get("buy_probability_threshold", 60),
            "sell_threshold": settings.get("sell_probability_threshold", 70)
        },
        "summary": {
            "total_assets": len(collected_data),
            "buy_candidates": len([d for d in collected_data if d.get("summary", {}).get("status") == "BUY_CANDIDATE"]),
            "sell_candidates": len([d for d in collected_data if d.get("summary", {}).get("status") == "SELL_CANDIDATE"]),
            "low_data_quality": len([d for d in collected_data if d.get("data_quality", 100) < 60])
        },
        "broker_sync_status": broker_sync_status,
        "global_news": global_news,
        "discovery_news": discovery_news,
        "assets": []
    }
    
    for data in collected_data:
        asset_json = {
            "broker_symbol": data.get("broker_symbol"),
            "yahoo_symbol": data.get("yahoo_symbol"),
            "name": data.get("name"),
            "group": data.get("group"),
            "summary": data.get("summary", {}),
            "price_data": {k: v for k, v in data.get("price", {}).items() if k != "error"},
            "news_count": len(data.get("news", [])),
            "news": data.get("news", []),
            "web": data.get("web", []),
            "news_excluded": data.get("news_excluded", []),
            "web_excluded": data.get("web_excluded", []),
            "tradingview": data.get("tradingview"),
            "symbol_candidates": data.get("symbol_candidates", []),
            "signals": data.get("signals", {})
        }
        
        if args.debug:
            asset_json["price_error"] = data.get("price", {}).get("error")
            asset_json["top_news"] = [{"title": n.get("title"), "domain": n.get("source_domain")} 
                                      for n in data.get("news", [])[:2]]
        
        json_output["assets"].append(asset_json)
    
    # Build markdown report (without AI analysis if not using AI)
    if args.no_ai:
        analysis_text = "[AI analysis disabled]\n\nScores are calculated deterministically."
    else:
        print("  Calling AI for analysis...")
        prompt = build_prompt(collected_data, portfolio_rules, settings)
        analysis_text = ask_ollama(prompt, settings)
    
    markdown_report = generate_report(collected_data, analysis_text, settings, global_news, discovery_news, broker_sync_status)
    
    # === STEP 8: SAVE FILES ===
    if not args.dry_run:
        print(f"\n[STEP 6] Saving reports...")
        output_folder = settings.get("output_folder", "reports")
        md_path, json_path = save_report(markdown_report, json_output, output_folder)
        
        print(f"[OK] Markdown: {md_path.resolve()}")
        print(f"[OK] JSON: {json_path.resolve()}")
    else:
        print("\n[DRY RUN] Reports not saved")
    
    # === SUMMARY ===
    print("\n" + "=" * 70)
    print("[ANALYSIS COMPLETE]")
    print(f"Buy candidates: {json_output['summary']['buy_candidates']}")
    print(f"Sell candidates: {json_output['summary']['sell_candidates']}")
    print(f"Low data quality: {json_output['summary']['low_data_quality']}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
