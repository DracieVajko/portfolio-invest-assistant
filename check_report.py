"""Inspect reports/portfolio_analysis.json from the command line.

Consolidates the old check_positions*.py / check_raw*.py / check_missing.py
one-off scripts into a single tool. Usage:

    py check_report.py positions [--ccy GBX,USD] [--file reports/...json]
    py check_report.py summary
    py check_report.py missing
    py check_report.py raw [--pie | --no-pie | --all]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _positions(data: dict) -> list[dict]:
    return (data.get("t212_data", {}) or {}).get("account_summary", {}).get(
        "all_positions", []) or []


def _raw(data: dict) -> list[dict]:
    return (data.get("t212_data", {}) or {}).get("account_summary", {}).get(
        "raw_positions", []) or []


def cmd_positions(args) -> int:
    ap = _positions(load(args.file))
    ccys = {c.strip().upper() for c in args.ccy.split(",") if c.strip()}
    n = 0
    for p in ap:
        if (p.get("quote_currency") or "").upper() not in ccys:
            continue
        n += 1
        print(f"{p.get('symbol', '?'):15s} "
              f"qty={p.get('quantity', 0):>15.4f} "
              f"raw_px={p.get('raw_current_price', 0):>10.4f} "
              f"ccy={p.get('quote_currency', '?')} "
              f"val={p.get('value_eur', 0):>10.2f} "
              f"bmv={p.get('broker_market_value_eur', 'N/A')} "
              f"src={p.get('currency_source', '?')} "
              f"fx={p.get('fx_rate_used', 'N/A')}")
    print(f"({n} positions in {sorted(ccys)})")
    return 0


def cmd_summary(args) -> int:
    d = load(args.file)
    ap = _positions(d)
    total = sum(p.get("value_eur") or 0 for p in ap)
    print(f"Sum of all position values: {total:.2f} EUR")
    eur = [p for p in ap if (p.get("quote_currency") or "").upper() == "EUR"]
    print(f"\nEUR positions ({len(eur)}):")
    for p in eur:
        print(f"  {p.get('symbol', '?'):15s} "
              f"qty={p.get('quantity', 0):>15.4f} "
              f"raw_px={p.get('raw_current_price', 0):>10.4f} "
              f"val={p.get('value_eur', 0):>10.2f} "
              f"bmv={p.get('broker_market_value_eur', 'N/A')}")
    s = (d.get("t212_data", {}) or {}).get("account_summary", {}) or {}
    print(f"\nBroker total equity: {s.get('total_equity')}")
    print(f"Broker free cash: {s.get('cash_free')}")
    print(f"Broker pie cash: {s.get('cash_pie')}")
    print(f"Broker invested: {s.get('invested')}")
    print(f"Sum invested + free + pie: "
          f"{(s.get('invested') or 0) + (s.get('cash_free') or 0) + (s.get('cash_pie') or 0)}")
    return 0


def cmd_missing(args) -> int:
    ap = _positions(load(args.file))
    missing = [p for p in ap
               if (p.get("value_eur") or 0) == 0]
    print(f"Positions with missing/zero value_eur: {len(missing)}")
    for p in missing:
        print(f"  {p.get('symbol', '?')}: qty={p.get('quantity')}, "
              f"raw_px={p.get('raw_current_price')}, ccy={p.get('quote_currency')}, "
              f"bmv={p.get('broker_market_value_eur')}")
    print("\nvalue_eur - broker_market_value_eur differences:")
    for p in ap:
        diff = (p.get("value_eur") or 0) - (p.get("broker_market_value_eur") or 0)
        if abs(diff) > 0.01:
            print(f"  {p.get('symbol', '?')}: {diff:.4f}")
    print(f"\nSum broker_market_value_eur: "
          f"{sum(p.get('broker_market_value_eur') or 0 for p in ap):.2f}")
    for title, key in (("Valuation sources", "valuation_source"),
                       ("Mapping statuses", "mapping_status")):
        print(f"\n{title}:")
        counts: dict[str, int] = {}
        for p in ap:
            counts[str(p.get(key, "N/A"))] = counts.get(str(p.get(key, "N/A")), 0) + 1
        for k in sorted(counts):
            print(f"  {k}: {counts[k]}")
    return 0


def cmd_raw(args) -> int:
    d = load(args.file)
    raw = _raw(d)
    print(f"Raw positions count: {len(raw)}")
    if args.mode in ("pie", "all"):
        pie = [p for p in raw if (p.get("pieQuantity") or 0) > 0]
        print(f"\nPositions with pieQuantity > 0: {len(pie)}")
        for p in pie:
            print(f"  {p.get('ticker', 'N/A')}: qty={p.get('quantity', 0)}, "
                  f"pieQty={p.get('pieQuantity', 0)}, "
                  f"currentPrice={p.get('currentPrice', 0)}")
    if args.mode in ("no-pie", "all"):
        no_pie = [p for p in raw if (p.get("pieQuantity") or 0) == 0]
        print(f"\nPositions with pieQuantity == 0: {len(no_pie)}")
        for p in no_pie:
            print(f"  {p.get('ticker', 'N/A'):20s} "
                  f"qty={p.get('quantity', 0):>15.4f} "
                  f"price={p.get('currentPrice', 0):>10.4f} "
                  f"ppl={p.get('ppl') or 0:>10.4f} "
                  f"fxPpl={p.get('fxPpl') or 0:>10.4f}")
        raw_sum = sum((p.get("quantity") or 0) * (p.get("currentPrice") or 0)
                      for p in raw)
        print(f"\nRaw sum (qty * price, no FX): {raw_sum:.2f}")
    if args.mode == "all":
        ap = _positions(d)
        ap_symbols = {p.get("symbol", "?") for p in ap}
        raw_symbols = {p.get("ticker", p.get("symbol", "UNKNOWN")) for p in raw}
        print(f"\nAll positions symbols: {len(ap_symbols)}")
        print(f"Raw positions symbols: {len(raw_symbols)}")
        print(f"\nIn all_positions but not raw: {ap_symbols - raw_symbols}")
        print(f"In raw but not all_positions: {raw_symbols - ap_symbols}")
        print("\nAll raw position tickers:")
        for p in raw:
            print(f"  {p.get('ticker', 'N/A'):20s} "
                  f"qty={p.get('quantity', 0):>15.4f} "
                  f"price={p.get('currentPrice', 0):>10.4f} "
                  f"ppl={p.get('ppl') or 0:>10.4f} "
                  f"fxPpl={p.get('fxPpl') or 0:>10.4f} "
                  f"pieQty={p.get('pieQuantity', 0)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Inspect portfolio_analysis.json")
    ap.add_argument("--file", default="reports/portfolio_analysis.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("positions", help="non-EUR positions with FX info")
    p.add_argument("--ccy", default="GBX,USD")
    p.set_defaults(func=cmd_positions)
    s = sub.add_parser("summary", help="totals + EUR positions + broker summary")
    s.set_defaults(func=cmd_summary)
    m = sub.add_parser("missing", help="missing values, diffs, sources, statuses")
    m.set_defaults(func=cmd_missing)
    r = sub.add_parser("raw", help="raw T212 positions")
    r.add_argument("--pie", dest="mode", action="store_const", const="pie")
    r.add_argument("--no-pie", dest="mode", action="store_const", const="no-pie")
    r.add_argument("--all", dest="mode", action="store_const", const="all")
    r.set_defaults(mode="all", func=cmd_raw)
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError:
        print(f"ERROR: file not found: {args.file}", file=sys.stderr)
        return 2
    except KeyError as e:
        print(f"ERROR: unexpected report shape, missing key {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
