"""Fundamentals pack CLI (§5.2, Appendix D). Read-only market data.

Usage:
    py scripts/fundamentals.py --symbol AAPL [--out agent_data/scratch]

Writes agent_data/scratch/fundamentals_{SYM}.json (gitignored).
Only yfinance GETs. Never touches portfolio files.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from investment_engine.research.fundamentals import build_pack  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build a §5.2 fundamentals pack.")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--out", default="agent_data/scratch")
    args = ap.parse_args(argv)

    try:
        import yfinance as yf

        ticker = yf.Ticker(args.symbol)
        info = ticker.info or {}
        pack = build_pack(
            ticker.financials, ticker.balance_sheet, ticker.cashflow,
            ticker.quarterly_financials, symbol=args.symbol,
            currency=str(info.get("currency", "")),
            fiscal_year_end=str(info.get("fiscalYearEnd", "")))
    except Exception as exc:
        print(f"ERROR: fundamentals fetch failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"fundamentals_{args.symbol}.json"
    dest.write_text(json.dumps(pack, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    ttm = pack.get("ttm", {})
    print(f"{args.symbol}: TTM revenue={ttm.get('revenue')} "
          f"FCF={ttm.get('fcf')} ROIC={pack.get('returns', {}).get('roic')} "
          f"annuals={pack['statements_available']['annual']} -> {dest}")
    for note in pack.get("notes", [])[:6]:
        print(f"  note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
