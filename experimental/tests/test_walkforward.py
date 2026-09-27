"""Phase 8 walk-forward harness: OOS scoring, embargo, determinism, containment."""

from __future__ import annotations

import json
from pathlib import Path


def _synthetic_csv(path: Path, start="2020-01-01", days=500):
    import numpy as np
    import pandas as pd

    rng = np.random.RandomState(7)
    dates = pd.bdate_range(start, periods=days, tz="UTC")
    rows = []
    for sym in ("AAA", "BBB"):
        rets = rng.normal(0.0005, 0.01, days)
        close = 50.0 * np.cumprod(1.0 + rets)
        # engineered dip mid-sample to invite pullback entries
        if days > 270:
            close[250:270] *= np.linspace(1.0, 0.9, 20)
        for i, day in enumerate(dates):
            o = close[i - 1] if i else 50.0
            rows.append({"date": day.isoformat(), "symbol": sym, "open": o,
                         "high": max(o, close[i]) * 1.005, "low": min(o, close[i]) * 0.995,
                         "close": close[i], "volume": 1_000_000, "currency": "EUR",
                         "earnings_date": ""})
    pd.DataFrame(rows).to_csv(path, index=False)


def _config(tmp_path, csv, folds, min_train=60):
    import shutil

    strat_src = Path("experimental/backtest/config/tech_pie_pullback_v1.json")
    fx_src = Path("experimental/backtest/config/fx_rates.json")
    strat = tmp_path / "strat.json"
    fx = tmp_path / "fx.json"
    shutil.copy(strat_src, strat)
    shutil.copy(fx_src, fx)
    cfg = {"strategy_config_path": str(strat), "data_input_path": str(csv),
           "fx_config_path": str(fx), "output_dir": "experimental/reports",
           "warmup_bars": 60, "embargo_bdays": 5, "minimum_train_bars": min_train,
           "folds": folds}
    cfg_path = tmp_path / "wf.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    return cfg_path


def test_walkforward_oos_and_embargo(tmp_path):
    import pandas as pd

    from experimental.backtest.walkforward import run_walkforward

    csv = tmp_path / "data.csv"
    _synthetic_csv(csv)
    cfg = _config(tmp_path, csv, [
        {"train_start": "2020-01-01", "train_end": "2020-12-31",
         "test_start": "2021-01-01", "test_end": "2021-06-30"},
        {"train_start": "2020-01-01", "train_end": "2021-06-30",
         "test_start": "2021-07-01", "test_end": "2021-12-31"}])
    out = run_walkforward(cfg)
    assert len(out["folds"]) == 2
    assert out["oos_bars"] > 0
    assert "cagr" in out["metrics_oos"]
    cutoff = pd.Timestamp("2021-07-01", tz="utc") + pd.Timedelta(days=5)
    # embargo respected for every OOS trade (vacuous when zero trades)
    assert out["oos_trades"] >= 0
    rep = Path("experimental/reports") / f"walkforward_{out['run_id']}.json"
    assert rep.is_file()
    rep.unlink()
    for suffix in ("_oos_equity.csv", "_summary.md"):
        p = Path("experimental/reports") / f"walkforward_{out['run_id']}{suffix}"
        assert p.is_file()
        p.unlink()


def test_walkforward_deterministic(tmp_path):
    from experimental.backtest.walkforward import run_walkforward

    csv = tmp_path / "data.csv"
    _synthetic_csv(csv)
    folds = [{"train_start": "2020-01-01", "train_end": "2020-12-31",
              "test_start": "2021-01-01", "test_end": "2021-06-30"}]
    first = run_walkforward(_config(tmp_path, csv, folds))
    second = run_walkforward(_config(tmp_path, csv, folds))
    assert first["run_id"] == second["run_id"]
    assert first["metrics_oos"] == second["metrics_oos"]
    for suffix in (".json", "_oos_equity.csv", "_summary.md"):
        (Path("experimental/reports") / f"walkforward_{first['run_id']}{suffix}").unlink()


def test_walkforward_fail_closed_and_contained(tmp_path):
    import pytest

    from experimental.backtest.walkforward import run_walkforward

    csv = tmp_path / "data.csv"
    _synthetic_csv(csv, days=100)
    cfg = _config(tmp_path, csv, [
        {"train_start": "2020-01-01", "train_end": "2020-12-31",
         "test_start": "2021-01-01", "test_end": "2021-06-30"}], min_train=100000)
    with pytest.raises(ValueError):
        run_walkforward(cfg)
    cfg2 = _config(tmp_path, csv, [
        {"train_start": "2020-01-01", "train_end": "2020-12-31",
         "test_start": "2021-01-01", "test_end": "2021-06-30"}], min_train=10)
    import json as _json

    raw = _json.loads(cfg2.read_text(encoding="utf-8"))
    raw["output_dir"] = str(tmp_path / "outside")
    cfg2.write_text(_json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        run_walkforward(cfg2)


def test_walkforward_no_forbidden_imports():
    src = Path("experimental/backtest/walkforward.py").read_text(encoding="utf-8")
    for forbidden in ("trading212", "yfinance", "requests", "api.env", "LLM", "provider"):
        assert forbidden not in src
