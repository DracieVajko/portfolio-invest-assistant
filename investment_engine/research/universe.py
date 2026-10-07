"""Universal instrument resolver: ISIN-keyed cross-listing discovery.

Problem: static Yahoo tables cover one portfolio; any new holding (EU/UK/CN
venue of a known company) needs manual mapping or stays UNRESOLVED.

Design (default ON, graceful degradation — any failure behaves exactly like
the pre-universe code):
- ISIN (from the broker snapshot catalog layer) is the universal key.
- OpenFIGI v3 mapping (free, keyless: 25 req/min, 10 jobs/request) resolves
  one ISIN to all venue listings (ticker + exchCode). Optional OPENFIGI_APIKEY
  env raises limits; values are never logged or printed.
- exchCode -> Yahoo suffix allowlist translates venues; unknown codes are
  SKIPPED, never guessed.
- Research-listing selection prefers the holding-currency home venue, then US,
  then first Equity row. Quantity/valuation NEVER move (broker ID keeps them);
  the research listing feeds technicals/news/earnings only.
- Results persist in data/cache/instrument_universe.json (gitignored) with
  per-candidate Yahoo existence probes. UNIVERSE_ENABLED=0 disables everything.
- UNIVERSE_CACHE_PATH overrides the cache location (tests use tmp).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
CACHE_VERSION = 1
DEFAULT_CACHE = Path("data") / "cache" / "instrument_universe.json"

# Bloomberg exchCode -> Yahoo suffix. Allowlist: unknown codes are skipped.
EXCH_TO_YAHOO_SUFFIX: dict[str, str] = {
    "US": "", "UQ": "", "UW": "", "UR": "", "UP": "", "UX": "", "UN": "",
    "LN": ".L", "GR": ".DE", "FP": ".PA", "PA": ".PA", "MC": ".MC",
    "IM": ".MI", "AS": ".AS", "NA": ".AS", "SW": ".SW", "VX": ".SW",
    "ST": ".ST", "DC": ".CO", "AV": ".VI", "BB": ".BR", "LS": ".LS",
    "TO": ".TO", "HK": ".HK", "SS": ".SS", "SZ": ".SZ", "KS": ".KS",
    "T": ".T", "SQ": ".AT",
}

# Preferred exchCodes per holding currency (home-venue-first rule).
CURRENCY_VENUES: dict[str, list[str]] = {
    "EUR": ["GR", "PA", "MC", "AS", "NA", "IM", "AV", "LS", "DC", "LN"],
    "USD": ["US", "UQ", "UW", "UR", "UP", "UX", "UN"],
    "GBP": ["LN"],
    "CHF": ["SW", "VX"],
    "DKK": ["DC"],
    "SEK": ["ST"],
    "CAD": ["TO"],
    "HKD": ["HK"],
    "CNY": ["SS", "SZ"],
    "KRW": ["KS"],
    "JPY": ["T"],
}

_JOBS_PER_REQUEST = 10
_MIN_GAP_S = 2.5
_FETCH_TIMEOUT_S = 15
_MAX_NEW_ISINS_PER_RUN = 60
_MAX_PROBES_PER_ISIN = 2


def enabled() -> bool:
    """Kill-switch: UNIVERSE_ENABLED=0 disables discovery (tables still work)."""
    return os.getenv("UNIVERSE_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def cache_path() -> Path:
    override = os.getenv("UNIVERSE_CACHE_PATH", "").strip()
    return Path(override) if override else DEFAULT_CACHE


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_cache() -> dict[str, Any]:
    """Load the universe cache ({} when missing/corrupt — never raises)."""
    try:
        data = json.loads(cache_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("version") == CACHE_VERSION:
            return data
    except Exception:
        pass
    return {"version": CACHE_VERSION, "isins": {}}


def save_cache(data: dict[str, Any]) -> None:
    """Persist the universe cache (best-effort, never raises)."""
    try:
        data["version"] = CACHE_VERSION
        path = cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / f".tmp.universe.{os.getpid()}.json"
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        logger.debug("Universe cache save skipped: %s", type(exc).__name__)


def translate_listing(ticker: str, exch_code: str) -> str | None:
    """Bloomberg venue listing -> Yahoo symbol. None for unknown venues."""
    clean = "".join(str(ticker or "").strip().upper().split())
    suffix = EXCH_TO_YAHOO_SUFFIX.get(str(exch_code or "").strip().upper(), None)
    if not clean or suffix is None:
        return None
    return clean + suffix


def select_research_listing(listings: list[dict[str, Any]],
                            holding_currency: str | None = None) -> tuple[dict[str, Any] | None, str]:
    """Pick the research listing. Returns (listing|None, reason)."""
    equities = [l for l in (listings or []) if isinstance(l, dict)]
    if not equities:
        return None, "no-listings"
    ccy = str(holding_currency or "").strip().upper()
    for venue in CURRENCY_VENUES.get(ccy, []):
        for listing in equities:
            if str(listing.get("exchCode", "")).strip().upper() == venue and listing.get("yahoo"):
                return listing, f"home-currency-venue:{venue}"
    for listing in equities:
        if str(listing.get("exchCode", "")).strip().upper() in ("US", "UQ", "UW"):
            return listing, "us-home-listing"
    if equities[0].get("yahoo"):
        return equities[0], "first-equity-listing"
    return None, "no-yahoo-translation"


def _openfigi_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json", "User-Agent": "PortfolioAI/2.0"}
    key = os.getenv("OPENFIGI_APIKEY", "").strip() or os.getenv("OPENFIGI_API_KEY", "").strip()
    if key:
        headers["X-OPENFIGI-APIKEY"] = key  # never logged (see below)
    return headers


def fetch_listings(isins: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Batch ISIN -> venue listings via OpenFIGI (keyless). Never raises.

    Returns {ISIN: [{ticker, exchCode, mic, currency, name, yahoo}]}.
    Respects 25 req/min via a 2.5 s gap; 10 jobs per request.
    """
    import requests

    targets = [str(i).strip().upper() for i in (isins or []) if str(i or "").strip()]
    out: dict[str, list[dict[str, Any]]] = {}
    if not targets:
        return out
    try:
        capped = targets[: _MAX_NEW_ISINS_PER_RUN * 2]
        for chunk_start in range(0, len(capped), _JOBS_PER_REQUEST):
            chunk = capped[chunk_start:chunk_start + _JOBS_PER_REQUEST]
            if not chunk:
                break
            try:
                resp = requests.post(
                    OPENFIGI_URL,
                    json=[{"idType": "ID_ISIN", "idValue": isin} for isin in chunk],
                    headers=_openfigi_headers(),
                    timeout=_FETCH_TIMEOUT_S,
                )
                payload = resp.json() if resp.status_code == 200 else []
            except Exception as exc:
                logger.debug("OpenFIGI request failed: %s", type(exc).__name__)
                payload = []
            for isin, result in zip(chunk, payload if isinstance(payload, list) else []):
                rows = []
                try:
                    for entry in (result or {}).get("data", []) or []:
                        if not isinstance(entry, dict):
                            continue
                        yahoo = translate_listing(entry.get("ticker", ""), entry.get("exchCode", ""))
                        if not yahoo:
                            continue
                        rows.append({
                            "ticker": str(entry.get("ticker", "")),
                            "exchCode": str(entry.get("exchCode", "")),
                            "mic": str(entry.get("micCode", "") or ""),
                            "currency": str(entry.get("currency", "") or ""),
                            "name": str(entry.get("name", "") or "")[:120],
                            "yahoo": yahoo,
                        })
                except Exception:
                    rows = []
                out[isin] = rows
            time.sleep(_MIN_GAP_S)
    except Exception as exc:
        logger.debug("OpenFIGI batch aborted: %s", type(exc).__name__)
    return out


def verify_candidate_yahoo(yahoo: str, timeout: int = 10) -> bool:
    """Existence probe: one short Yahoo history call. False on any failure."""
    try:
        import yfinance as _yf

        hist = _yf.Ticker(str(yahoo)).history(period="5d", interval="1d", timeout=timeout)
        return hist is not None and not hist.empty and len(hist.dropna()) > 0
    except Exception:
        return False


def ensure_for_isins(isins: list[str], holding_ccy: dict[str, str] | None = None) -> dict[str, Any]:
    """Ensure universe records exist for ISINs (fetch missing only). Never raises.

    Returns stats {cached, fetched, failed, verified}. Probed candidates are
    recorded so later runs stay offline.
    """
    holding_ccy = holding_ccy or {}
    stats = {"cached": 0, "fetched": 0, "failed": 0, "verified": 0}
    if not enabled():
        return stats
    try:
        cache = load_cache()
        store = cache.setdefault("isins", {})
        wanted = [str(i).strip().upper() for i in (isins or []) if str(i or "").strip()]
        missing = [i for i in wanted if i not in store][: _MAX_NEW_ISINS_PER_RUN]
        if not missing:
            stats["cached"] = len(wanted)
            return stats
        fetched = fetch_listings(missing)
        for isin in missing:
            try:
                listings = fetched.get(isin, [])
                ccy = holding_ccy.get(isin)
                selected, reason = select_research_listing(listings, ccy)
                verified: dict[str, Any] = {}
                if selected and selected.get("yahoo"):
                    probed = 0
                    for cand in [selected] + [l for l in listings if l is not selected]:
                        if probed >= _MAX_PROBES_PER_ISIN:
                            break
                        yahoo = cand.get("yahoo")
                        if not yahoo or yahoo in verified:
                            continue
                        probed += 1
                        if verify_candidate_yahoo(yahoo):
                            verified[yahoo] = {"ok": True, "checked_at": _utcnow()}
                            stats["verified"] += 1
                store[isin] = {
                    "listings": listings,
                    "selected": {"yahoo": (selected or {}).get("yahoo"),
                                 "reason": reason,
                                 "holding_currency": ccy},
                    "verified": verified,
                    "fetched_at": _utcnow(),
                }
                stats["fetched"] += 1
            except Exception:
                stats["failed"] += 1
        save_cache(cache)
    except Exception as exc:
        logger.debug("Universe ensure skipped: %s", type(exc).__name__)
    return stats


def lookup_universe_yahoo(isin: str) -> tuple[str | None, str]:
    """Cached research Yahoo symbol for an ISIN. (None, reason) when absent."""
    try:
        record = load_cache().get("isins", {}).get(str(isin or "").strip().upper(), {})
        selected = (record or {}).get("selected", {}) or {}
        yahoo = selected.get("yahoo")
        verified = (record or {}).get("verified", {}) or {}
        if yahoo and verified.get(yahoo, {}).get("ok"):
            return yahoo, f"universe:{selected.get('reason', '')}"
        if yahoo:
            return None, "universe-unverified"
        return None, "universe-miss"
    except Exception:
        return None, "universe-error"


def verified_yahoo_symbols() -> set[str]:
    """All cache-verified Yahoo symbols (for support gating). Never raises."""
    try:
        out: set[str] = set()
        for record in load_cache().get("isins", {}).values():
            for yahoo, verdict in ((record or {}).get("verified", {}) or {}).items():
                if isinstance(verdict, dict) and verdict.get("ok"):
                    out.add(str(yahoo))
        return out
    except Exception:
        return set()


def is_universe_verified(yahoo: str | None) -> bool:
    """True when the universe cache verified this Yahoo symbol (existence probe)."""
    if not yahoo:
        return False
    return str(yahoo).strip() in verified_yahoo_symbols()


def universe_support_state(yahoo: str | None, purpose: str = "market_data") -> str:
    """support_state with universe-verified admission. Never raises."""
    try:
        if yahoo and is_universe_verified(yahoo):
            return "SUPPORTED"
        from investment_engine.portfolio.symbols import support_state as _support

        return _support(yahoo, purpose)
    except Exception:
        return "UNRESOLVED"


def enrich_yahoo_map(yahoo_by_display: dict[str, str | None],
                     positions: list[dict[str, Any]] | None,
                     is_working: Callable[[str | None], bool] | None = None) -> dict[str, Any]:
    """Fill ONLY missing/unresolvable entries from the universe. Never overrides.

    positions: broker snapshot rows carrying instrument ISIN + currency
    (dicts with instrument.isin / instrument.currency, or flat isin/currency).
    Returns stats {resolved, skipped_working, missed}.
    """
    stats = {"resolved": 0, "skipped_working": 0, "missed": 0}
    if not enabled():
        return stats
    try:
        rows = []
        for pos in positions or []:
            if not isinstance(pos, dict):
                continue
            ins = pos.get("instrument", {}) if isinstance(pos.get("instrument"), dict) else {}
            isin = str(ins.get("isin") or pos.get("isin") or "").strip().upper()
            ccy = str(ins.get("currency") or pos.get("currency") or "").strip().upper()
            disp = str(pos.get("display_symbol") or pos.get("display") or "").strip().upper()
            if isin and disp:
                rows.append((disp, isin, ccy or None))
        if rows:
            ensure_for_isins([isin for _, isin, _ in rows],
                             {isin: ccy for _, isin, ccy in rows if ccy})
        for disp, isin, _ccy in rows:
            current = (yahoo_by_display or {}).get(disp)
            if current and (is_working(current) if is_working else True):
                stats["skipped_working"] += 1
                continue
            yahoo, _reason = lookup_universe_yahoo(isin)
            if yahoo:
                yahoo_by_display[disp] = yahoo
                stats["resolved"] += 1
            else:
                stats["missed"] += 1
    except Exception as exc:
        logger.debug("Universe enrich skipped: %s", type(exc).__name__)
    return stats


def fingerprint_inputs(isins: list[str]) -> str:
    """Stable hash of an ISIN set (for manifests/logs, no secrets)."""
    return hashlib.sha256("|".join(sorted({str(i).strip().upper() for i in isins if i})).encode()).hexdigest()[:12]
