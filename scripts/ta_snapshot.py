"""TA snapshot CLI (Appendix B7). Read-only market data, writes one JSON.

Usage:
    py scripts/ta_snapshot.py --symbol AAPL --exchange NASDAQ --currency USD
    py scripts/ta_snapshot.py --symbol BTC-USD --crypto --benchmark SPY
    py scripts/ta_snapshot.py --symbol AAPL --out agent_data/scratch

Writes agent_data/scratch/ta_snapshot_{SYM}_{YYYYMMDD}.json (gitignored).
Never sends POST; only yfinance GET. Never touches portfolio files.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from investment_engine.research.ta_snapshot import (  # noqa: E402
    build_snapshot,
    last_complete_bar,
)


def fetch_ohlcv(symbol: str, period: str = "max") -> tuple:
    """yfinance OHLCV with split-adjusted close + Adj Close separated."""
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    hist = ticker.history(period=period, interval="1d", auto_adjust=False)
    if hist is None or hist.empty:
        raise RuntimeError(f"no yfinance history for {symbol}")
    cols = {str(c).lower().replace(" ", "_"): c for c in hist.columns}
    rename = {}
    for want in ("open", "high", "low", "close", "volume"):
        if want in cols:
            rename[cols[want]] = want
    adj_key = cols.get("adj_close", cols.get("adjclose"))
    if adj_key:
        rename[adj_key] = "adj_close"
    frame = hist.rename(columns=rename)
    keep = [c for c in ("open", "high", "low", "close", "adj_close", "volume")
            if c in frame.columns]
    return frame[keep], ticker


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build an Appendix B7 TA snapshot.")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--exchange", default="")
    ap.add_argument("--currency", default="")
    ap.add_argument("--price-unit", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--isin", default="")
    ap.add_argument("--benchmark", default="")
    ap.add_argument("--benchmark-ccy", default="")
    ap.add_argument("--period", default="max")
    ap.add_argument("--crypto", action="store_true")
    ap.add_argument("--out", default="agent_data/scratch")
    ap.add_argument("--charts", default="",
                    help="also render C1-C5 chart pack into this dir "
                         "(default: no charts)")
    ap.add_argument("--transactions", default="",
                    help="@path/to/transactions.json for C5 "
                         '([{"date": ..., "side": "BUY", "price": ...}])')
    ap.add_argument("--avg-cost", type=float, default=None)
    args = ap.parse_args(argv)

    now_utc = datetime.now(timezone.utc)
    try:
        frame, _ = fetch_ohlcv(args.symbol, args.period)
    except Exception as exc:
        print(f"ERROR: data fetch failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1
    try:
        import yfinance as yf

        acts = yf.Ticker(args.symbol).actions
        exdiv: list[str] = []
        splits: dict[str, float] = {}
        if acts is not None and not acts.empty:
            cols = {str(c).lower().replace(" ", "_"): c for c in acts.columns}
            if "dividends" in cols:
                div = acts[cols["dividends"]].dropna()
                exdiv = [d.date().isoformat() for d in div.index if float(div.loc[d]) != 0]
            if "stock_splits" in cols:
                spl = acts[cols["stock_splits"]].dropna()
                for d in spl.index:
                    try:
                        ratio = float(spl.loc[d])
                        if ratio and ratio != 1.0:
                            splits[d.date().isoformat()] = 1.0 / ratio
                    except (TypeError, ValueError, ZeroDivisionError):
                        continue
    except Exception as exc:
        print(f"WARNING: corporate actions unreadable: {type(exc).__name__}",
              file=sys.stderr)
        exdiv, splits = [], {}
    complete = last_complete_bar(now_utc)
    frame = frame[frame.index.date <= complete]
    if frame.empty:
        print("ERROR: no complete bars", file=sys.stderr)
        return 1

    bench = None
    bmeta: dict = {}
    if args.benchmark:
        try:
            bench, _ = fetch_ohlcv(args.benchmark, args.period)
            bmeta = {"ticker": args.benchmark,
                     "currency": args.benchmark_ccy or args.currency}
        except Exception as exc:
            print(f"WARNING: benchmark fetch failed: {type(exc).__name__}",
                  file=sys.stderr)

    snap = build_snapshot(
        frame, symbol=args.symbol, name=args.name, isin=args.isin,
        exchange=args.exchange, currency=args.currency,
        price_unit=args.price_unit,
        asset_class="crypto" if args.crypto else "equity",
        benchmark=bench if bench is not None else None,
        benchmark_meta=bmeta, ex_div_dates=exdiv, splits=splits,
        now=now_utc)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"ta_snapshot_{args.symbol}_{complete.isoformat()}.json"
    dest.write_text(json.dumps(snap, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    d = snap.get("daily", {})
    print(f"{args.symbol} as-of {complete.isoformat()}: close={d.get('close')} "
          f"regime={d.get('regime')} RSI={d.get('rsi14')} "
          f"bars={snap['data']['n_bars']} -> {dest}")
    for note in snap["data"].get("notes", [])[:5]:
        print(f"  note: {note}")
    for flag in snap["data"].get("quality_flags", [])[:5]:
        print(f"  quality: {flag}")
    if args.charts:
        from investment_engine.research import ta_charts as _charts

        transactions = None
        if args.transactions:
            spec = args.transactions[1:] if args.transactions.startswith("@") else args.transactions
            try:
                transactions = json.loads(Path(spec).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                print(f"WARNING: transactions unreadable: {type(exc).__name__}",
                      file=sys.stderr)
        paths = _charts.render_pack(
            frame, args.symbol, complete.isoformat(), args.charts,
            bench=bench if bench is not None else None,
            bench_name=args.benchmark, transactions=transactions,
            avg_cost=args.avg_cost, regime=str(d.get("regime") or ""))
        snap["files"]["charts"] = paths
        dest.write_text(json.dumps(snap, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        print(f"charts: {len(paths)} rendered -> {args.charts}")
        if not paths:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
