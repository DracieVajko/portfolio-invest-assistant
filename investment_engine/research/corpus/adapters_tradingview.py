"""Phase: TradingView research adapter for the automatic Playwright sweep.

COMPLIANCE: public TradingView symbol pages only (no login), low frequency
(serial page loads, capped symbols per run), data-facts + quoted ratings only.
On block markers (403/429/CAPTCHA/denied) the adapter disables itself for the
rest of the run. Nothing here generates predictions: page forecasts are quoted
as page content with estimate labelling, never asserted as fact.

Covers what pure Python cannot: per-symbol key facts, gauge technicals, and
analyst ratings for listings Yahoo misses or serves unreliably.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Yahoo suffix -> TradingView exchange slug. Only venues verified against
# TradingView URL conventions; unknown venues return None (skipped, never
# guessed). US bare symbols fall back NASDAQ -> NYSE in that order.
YAHOO_SUFFIX_TO_TV_EXCHANGE: dict[str, str] = {
    ".L": "LSE",
    ".DE": "XETRA",
    ".PA": "EURONEXT",
    ".MC": "BME",
}

_TV_DISABLED = False
_BLOCK_MARKERS = ("403", "429", "captcha", "denied", "blocked", "net::ERR",
                  "761", "challenge")


def reset() -> None:
    """Re-enable after a run (tests + run start)."""
    global _TV_DISABLED
    _TV_DISABLED = False


def disabled() -> bool:
    return _TV_DISABLED


def disable(reason: str = "") -> None:
    global _TV_DISABLED
    if not _TV_DISABLED:
        _TV_DISABLED = True
        logger.warning("TradingView adapter disabled for the rest of the run%s",
                       f": {reason}" if reason else "")


def _is_block_error(exc: BaseException | str) -> bool:
    low = str(exc or "").lower()
    return any(m in low for m in _BLOCK_MARKERS)


def tv_exchange_for(yahoo: str | None, config_tv: str | None = None) -> str | None:
    """Resolve the TradingView exchange. Config wins; suffix map next."""
    if config_tv and ":" in config_tv:
        exch, _, sym = config_tv.partition(":")
        if exch.strip() and sym.strip():
            return exch.strip().upper()
    y = str(yahoo or "").strip()
    if not y:
        return None
    for suffix, exchange in YAHOO_SUFFIX_TO_TV_EXCHANGE.items():
        if y.upper().endswith(suffix):
            return exchange
    if "." not in y and "-" not in y:
        return "NASDAQ"  # US bare: first attempt (NYSE fallback by caller)
    return None


def resolve_tv_symbol(display: str, yahoo: str | None, t212_id: str | None = None,
                      config_assets: list[dict] | None = None) -> tuple[str, str] | None:
    """Return (tv_symbol, exchange) or None when underivable (skip, never guess).

    Crypto (no exchange concept here) and unknown venues return None.
    """
    disp = str(display or "").strip().upper()
    if not disp:
        return None
    for asset in config_assets or []:
        if not isinstance(asset, dict):
            continue
        if t212_id and str(asset.get("broker_symbol", "")) == t212_id:
            tv = str(asset.get("tradingview_symbol", "") or "")
            if ":" in tv:
                exch, _, sym = tv.partition(":")
                if exch.strip() and sym.strip():
                    return sym.strip().upper(), exch.strip().upper()
            break
    y = str(yahoo or "").strip()
    if not y:
        return None
    if y.upper().endswith(("-USD",)):
        return None  # crypto uses venue-specific formats; out of scope
    exchange = tv_exchange_for(y)
    if not exchange:
        return None
    stem = y.upper()
    for suffix in YAHOO_SUFFIX_TO_TV_EXCHANGE:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    if not stem or exchange == "NASDAQ":
        return stem or disp, exchange
    return stem, exchange


def bundle_to_technicals(bundle) -> dict[str, Any]:
    """Map TradingViewSymbolData to distinctly-keyed technicals.

    Keys are TV_-prefixed so nothing mistakes them for Yahoo numerics.
    """
    try:
        data = bundle.to_dict() if hasattr(bundle, "to_dict") else dict(bundle or {})
    except Exception:
        return {}
    out: dict[str, Any] = {}
    if data.get("price") is not None:
        out["TV_PRICE"] = data["price"]
    if data.get("change_pct") is not None:
        out["TV_CHANGE_PCT"] = data["change_pct"]
    if data.get("technicals_rating"):
        out["TV_TECHNICALS_RATING"] = str(data["technicals_rating"])
    if data.get("analyst_rating"):
        out["TV_ANALYST_RATING"] = str(data["analyst_rating"])
    return out


def bundle_to_items(bundle, display: str, fetched_at: str) -> list[dict[str, Any]]:
    """TradingViewSymbolData -> corpus ResearchItem dicts (snapshot-timestamped).

    Gauge readings are observations timestamped at fetch (not fabricated
    publication dates): published_at_utc = fetched_at, tier 2, with an
    explicit snapshot note. Forecasts quoted from the page stay quoted text.
    """
    try:
        data = bundle.to_dict() if hasattr(bundle, "to_dict") else dict(bundle or {})
    except Exception:
        return []
    disp = str(display or "").strip().upper()
    base_url = str(data.get("source", "") or "")
    url = f"https://www.{base_url}" if base_url and not base_url.startswith("http") else (base_url or "")
    items: list[dict[str, Any]] = []
    facts = str(data.get("key_facts_today", "") or "").strip()
    if facts:
        items.append({
            "title": f"{disp}: key facts snapshot",
            "url": url, "source": "TradingView", "published_dt": fetched_at,
            "category": "company",
            "tier": 2, "relevance_score": 70,
            "preview": f"Snapshot as of {fetched_at}: {facts[:400]}",
        })
    analyst = str(data.get("analyst_rating", "") or "").strip()
    if analyst:
        items.append({
            "title": f"{disp}: analyst rating {analyst} (page quote, estimate, not a fact)",
            "url": url, "source": "TradingView", "published_dt": fetched_at,
            "category": "analyst",
            "tier": 2, "relevance_score": 70,
            "preview": f"Snapshot as of {fetched_at}: analyst gauge reads '{analyst}'. "
                       f"Quoted page content, not generated analysis.",
        })
    for item in items:
        item.setdefault("ticker_tags", [disp])
    return items


def run_sweep(candidates: list[tuple[str, str | None, str | None]],
              config_assets: list[dict] | None = None,
              max_symbols: int = 8,
              timeout_ms: int = 30000) -> dict[str, Any]:
    """Automatic sweep: one TradingView page per candidate. Never raises.

    candidates: [(display, yahoo_or_None, t212_id_or_None)]. Returns
    {technicals: {display: dict}, items: [...], stats: {...}}.
    Respects the run kill-switch; stops early on block markers.
    """
    from investment_engine.research import web_researcher as _wr

    result: dict[str, Any] = {"technicals": {}, "items": [],
                              "stats": {"attempted": 0, "hits": 0, "skipped": 0, "blocked": 0}}
    if _TV_DISABLED:
        result["stats"]["blocked"] = len(candidates or [])
        return result
    targets: list[tuple[str, str, str]] = []
    for display, yahoo, t212_id in candidates or []:
        if len(targets) >= max_symbols:
            break
        resolved = resolve_tv_symbol(display, yahoo, t212_id, config_assets)
        if resolved is None:
            result["stats"]["skipped"] += 1
            continue
        sym, exch = resolved
        targets.append((str(display).strip().upper(), sym, exch))
    if not targets:
        return result
    try:
        fetched = _wr.fetch_tradingview_symbols_sync(
            [(sym, exch) for _, sym, exch in targets],
            include_financials=True, headless=True,
            timeout_ms=timeout_ms, max_concurrent=1)
    except Exception as exc:
        if _is_block_error(exc):
            disable(f"{type(exc).__name__}")
            result["stats"]["blocked"] = len(targets)
        else:
            logger.debug("TV sweep failed: %s", type(exc).__name__)
            result["stats"]["skipped"] += len(targets)
        return result
    by_key = {(str(b.symbol).upper(), str(b.exchange).upper()): b
              for b in (fetched or []) if b is not None}
    fetched_at = datetime.now(timezone.utc).isoformat()
    for display, sym, exch in targets:
        result["stats"]["attempted"] += 1
        bundle = by_key.get((sym.upper(), exch.upper()))
        if bundle is None:
            result["stats"]["skipped"] += 1
            continue
        tech = bundle_to_technicals(bundle)
        if tech:
            result["technicals"][display] = tech
        items = bundle_to_items(bundle, display, fetched_at)
        if items:
            result["items"].extend(items)
        if tech or items:
            result["stats"]["hits"] += 1
        else:
            result["stats"]["skipped"] += 1
    return result
