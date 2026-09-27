"""Operator tool: inspect / refresh the ISIN instrument universe cache.

Offline-safe: --check and --dry-run make no network calls. --refresh contacts
OpenFIGI (free, keyless) for cache-missing ISINs only, then probes Yahoo for
the selected candidates. Writes only data/cache/instrument_universe.json
(gitignored). Never prints secrets (optional OPENFIGI_APIKEY is never echoed).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _catalog_isins() -> dict[str, str]:
    """ISIN -> holding currency from the local instrument catalog (offline)."""
    from investment_engine.portfolio.broker_first import get_cached_catalog

    out: dict[str, str] = {}
    try:
        catalog = get_cached_catalog() or {}
        for _tick, entry in catalog.items():
            if isinstance(entry, dict) and entry.get("isin"):
                out[str(entry["isin"]).strip().upper()] = str(
                    entry.get("currencyCode", "") or "").strip().upper()
    except Exception:
        pass
    return out


def main(argv: list[str] | None = None) -> int:
    from investment_engine.research import universe as uni

    parser = argparse.ArgumentParser(description="ISIN universe cache tool (offline-safe).")
    parser.add_argument("--check", action="store_true", help="Report cache coverage (no network).")
    parser.add_argument("--dry-run", action="store_true", help="List ISINs that WOULD be fetched (no network).")
    parser.add_argument("--refresh", action="store_true", help="Fetch missing ISINs (OpenFIGI) + verify (Yahoo).")
    parser.add_argument("--isns", default="", help="Comma-separated ISINs (default: catalog ISINs).")
    args = parser.parse_args(argv)

    if args.isns:
        isins = [i.strip().upper() for i in args.isns.split(",") if i.strip()]
        ccy: dict[str, str] = {}
    else:
        catalog = _catalog_isins()
        isins, ccy = sorted(catalog), catalog
    print(f"ISINs in scope: {len(isins)}")

    cache = uni.load_cache()
    store = cache.get("isins", {})
    missing = [i for i in isins if i not in store]
    print(f"cached: {len(isins) - len(missing)} / missing: {len(missing)}")
    if missing:
        print("missing (first 20): " + ", ".join(missing[:20]))

    if args.check:
        verified = uni.verified_yahoo_symbols()
        print(f"verified Yahoo symbols in cache: {len(verified)}")
        return 0
    if args.dry_run:
        print(f"dry-run: would fetch {len(missing)} ISINs in "
              f"{(len(missing) + 9) // 10} OpenFIGI request(s), then probe candidates.")
        return 0
    if args.refresh:
        if not uni.enabled():
            print("UNIVERSE_ENABLED=0: refresh disabled.")
            return 2
        stats = uni.ensure_for_isins(isins, ccy)
        print(f"refresh: {stats}")
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
