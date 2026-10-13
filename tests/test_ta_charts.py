"""Chart pack smoke tests (Agg backend, synthetic frames, tmp output)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from investment_engine.research import ta_charts as charts


def _frame(n: int = 600, seed: int = 11) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.0008, 0.012, n))
    idx = pd.date_range("2022-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "open": np.concatenate([[100.0], close[:-1]]),
        "high": close * 1.004, "low": close * 0.996,
        "close": close,
        "volume": rng.randint(500_000, 5_000_000, n).astype(float),
    }, index=idx)


def test_c1_c2_c3_render_png(tmp_path):
    frame = _frame()
    as_of = "2024-06-01"
    paths = charts.render_pack(frame, "TST", as_of, tmp_path,
                               kinds=("C1", "C2", "C3"))
    assert len(paths) == 3
    for p in paths:
        blob = tmp_path.joinpath(__import__("os").path.basename(p)).read_bytes()
        assert blob[:8] == b"\x89PNG\r\n\x1a\n" and len(blob) > 5000


def test_c4_needs_benchmark_and_c5_needs_trades(tmp_path):
    frame = _frame()
    assert charts.render_pack(frame, "TST", "2024-06-01", tmp_path,
                              kinds=("C4",)) == []
    bench = (_frame(seed=12) * 1.0).rename(columns={"close": "close"})
    paths = charts.render_pack(frame, "TST", "2024-06-01", tmp_path,
                               bench=bench, bench_name="BENCH",
                               kinds=("C4",))
    assert len(paths) == 1
    tx = [{"date": "2024-03-01", "side": "BUY", "price": 105.0},
          {"date": "2024-05-01", "side": "SELL", "price": 120.0}]
    paths = charts.render_pack(frame, "TST", "2024-06-01", tmp_path,
                               transactions=tx, avg_cost=105.0, kinds=("C5",))
    assert len(paths) == 1


def test_short_frame_yields_nothing_but_never_raises(tmp_path):
    frame = _frame(n=10)
    assert charts.render_pack(frame, "TST", "2024-06-01", tmp_path) == []
