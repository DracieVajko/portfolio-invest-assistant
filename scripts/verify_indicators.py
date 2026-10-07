"""Phase 3 cross-PC indicator parity check — offline, deterministic, no secrets.

Builds a seeded synthetic OHLCV fixture, runs the canonical manual engine,
and prints a stable snapshot hash. Run on both PCs: hashes must match
byte-for-byte (values rounded to 10dp, NaN normalized) for cross-PC parity.

Exit codes: 0 parity hash printed, 1 engine failure.
Writes no files.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_fixture(n_bars: int = 300, seed: int = 42):
    import numpy as np
    import pandas as pd

    rng = np.random.RandomState(seed)
    rets = rng.normal(0.0008, 0.012, n_bars)
    close = 100.0 * np.cumprod(1.0 + rets)
    spread = np.abs(rng.normal(0.004, 0.002, n_bars))
    high = close * (1.0 + spread)
    low = close * (1.0 - spread)
    open_ = np.empty(n_bars)
    open_[0] = 100.0
    open_[1:] = close[:-1]
    volume = (rng.randint(500_000, 5_000_000, n_bars)).astype(float)
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume})


def snapshot_hash(df, columns) -> str:
    parts = []
    for col in columns:
        series = df[col]
        for v in series.tolist():
            try:
                f = float(v)
            except (TypeError, ValueError):
                parts.append("NaN")
                continue
            import math

            parts.append("NaN" if math.isnan(f) else f"{round(f, 10):.10f}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cross-PC manual-engine parity hash (offline).")
    parser.add_argument("--bars", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    try:
        from investment_engine.research.indicators import (
            ENGINE_VERSION,
            MANUAL_COLUMNS,
            apply_manual_indicators,
        )
    except Exception as exc:
        print(f"FAIL: cannot import canonical engine: {type(exc).__name__}: {exc}")
        return 1

    df = build_fixture(args.bars, args.seed)
    out = apply_manual_indicators(df.copy())
    missing = [c for c in MANUAL_COLUMNS if c not in out.columns]
    if missing:
        print(f"FAIL: missing columns: {missing}")
        return 1
    digest = snapshot_hash(out, MANUAL_COLUMNS)
    print(f"engine: manual ({ENGINE_VERSION})")
    print(f"bars: {args.bars} seed: {args.seed} columns: {len(MANUAL_COLUMNS)}")
    print(f"snapshot_sha256: {digest}")
    print("RESULT: OK (exit 0) — compare snapshot_sha256 across PCs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
