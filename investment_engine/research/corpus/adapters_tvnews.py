"""TradingView per-symbol news adapter (plain HTTPS JSON, no browser).

Endpoint (verified live 2026-09-26: HTTP 200 + JSON items):
  GET https://news-headlines.tradingview.com/v2/view/headlines/symbol
      ?symbol=EXCH:SYM&client=web&streaming=false&lang=en&limit=N

COMPLIANCE: public endpoint, no login/key, plain Mozilla User-Agent,
~8 symbols/run, serial requests with a 1 req/s cap, retry only on 429/5xx
with backoff. Block markers (403/429/CAPTCHA/denied) disable the adapter
for the rest of the run. Items carry real ``published`` unix timestamps,
so unlike undated scrapes they can enter decision-relevant corpus sets.

Items are corpus-shaped dicts (title/url/source/published_dt/published_str/
preview/category/tier/ticker_match + ticker_tags for bucket merge), the same
shape the Playwright sweep emits.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

TV_NEWS_URL = (
    "https://news-headlines.tradingview.com/v2/view/headlines/symbol"
)
TV_NEWS_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

_TVNEWS_DISABLED = False
_BLOCK_MARKERS = ("403", "429", "captcha", "denied", "blocked", "challenge",
                  "forbidden", "too many")


def reset() -> None:
    """Re-enable after a run (tests + run start)."""
    global _TVNEWS_DISABLED
    _TVNEWS_DISABLED = False


def disabled() -> bool:
    return _TVNEWS_DISABLED


def disable(reason: str = "") -> None:
    global _TVNEWS_DISABLED
    if not _TVNEWS_DISABLED:
        _TVNEWS_DISABLED = True
        logger.warning("TV symbol-news adapter disabled for the rest of the run%s",
                       f": {reason}" if reason else "")


def _is_block_error(exc: BaseException | str) -> bool:
    low = str(exc or "").lower()
    return any(m in low for m in _BLOCK_MARKERS)


def _item_to_record(display: str, item: dict, cutoff) -> dict[str, Any] | None:
    """Map one TV headline JSON object to a corpus-shaped record (or None)."""
    try:
        if not isinstance(item, dict):
            return None
        title = str(item.get("title", "") or "").strip()
        if not title:
            return None
        link = str(item.get("link", "") or "").strip()
        story = str(item.get("storyPath", "") or "").strip()
        url = link or (f"https://www.tradingview.com{story}" if story.startswith("/") else story)
        if not url:
            return None
        try:
            pub_dt = datetime.fromtimestamp(float(item.get("published", 0)), tz=timezone.utc)
        except (TypeError, ValueError):
            return None
        if pub_dt < cutoff:
            return None
        provider = str(item.get("provider", "") or "").strip() or "TradingView"
        return {
            "title": title,
            "url": url,
            "source": f"TradingView/{provider}",
            "published_dt": pub_dt,
            "published_str": pub_dt.strftime("%Y-%m-%d %H:%M"),
            "published": pub_dt.strftime("%Y-%m-%d %H:%M"),
            "preview": "",
            "category": "tv_symbol",
            "tier": 2,
            "ticker_match": [display],
            "ticker_tags": [display],
        }
    except Exception:
        return None


def fetch_symbol_news(
    candidates: list[tuple[str, str | None, str | None]],
    config_assets: list[dict] | None = None,
    limit_per_symbol: int = 10,
    max_symbols: int = 8,
    max_age_hours: int = 48,
    timeout: int = 15,
    session=None,
) -> dict[str, Any]:
    """Fetch dated per-symbol headlines for up to ``max_symbols`` candidates.

    ``candidates``: (display, yahoo, t212_id) triples. Returns
    ``{"items": [...], "stats": {"attempted", "symbols", "items"}}``.
    Never raises: per-symbol failures degrade to an empty contribution.
    """
    from datetime import timedelta

    from investment_engine.research.corpus.adapters_tradingview import resolve_tv_symbol

    out: dict[str, Any] = {"items": [], "stats": {"attempted": 0, "symbols": 0, "items": 0}}
    if _TVNEWS_DISABLED:
        return out
    try:
        import requests as _rq
        sess = session or _rq.Session()
        if session is None:
            sess.headers.update({"User-Agent": TV_NEWS_UA})
    except Exception as exc:  # noqa: BLE001
        logger.debug("TV symbol news skipped (no HTTP session): %s", type(exc).__name__)
        return out

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    done = 0
    for display, yahoo, t212_id in candidates or []:
        if done >= max_symbols or _TVNEWS_DISABLED:
            break
        disp = str(display or "").strip().upper()
        if not disp:
            continue
        try:
            resolved = resolve_tv_symbol(disp, yahoo, t212_id, config_assets)
        except Exception:
            continue
        if not resolved:
            continue
        tv_sym, exchange = resolved
        params = {"symbol": f"{exchange}:{tv_sym}", "client": "web",
                  "streaming": "false", "lang": "en", "limit": limit_per_symbol}
        out["stats"]["attempted"] += 1
        try:
            resp = sess.get(TV_NEWS_URL, params=params, timeout=timeout)
            status = getattr(resp, "status_code", 200)
            if status in (429,) or (status is not None and status >= 500):
                time.sleep(2.0)
                try:
                    resp = sess.get(TV_NEWS_URL, params=params, timeout=timeout)
                    status = getattr(resp, "status_code", 200)
                except Exception:
                    continue
            if status in (403, 429) or (status is not None and status >= 500):
                if _is_block_error(status):
                    disable(f"HTTP {status}")
                    break
                continue
            data = resp.json() if hasattr(resp, "json") else {}
        except Exception as exc:
            if _is_block_error(exc):
                disable(str(exc)[:120])
                break
            logger.debug("TV symbol news failed for %s: %s", disp, type(exc).__name__)
            continue
        finally:
            # serial 1 req/s cap regardless of outcome
            try:
                time.sleep(1.0)
            except Exception:
                pass
        kept = 0
        try:
            items = data.get("items", []) if isinstance(data, dict) else []
        except Exception:
            items = []
        for raw in items if isinstance(items, list) else []:
            rec = _item_to_record(disp, raw, cutoff)
            if rec:
                out["items"].append(rec)
                kept += 1
        if kept:
            out["stats"]["symbols"] += 1
        done += 1
    out["stats"]["items"] = len(out["items"])
    return out
