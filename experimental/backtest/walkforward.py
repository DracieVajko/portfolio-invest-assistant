"""Phase 8 walk-forward out-of-sample harness. Research only, isolated.

Expanding windows: each fold trains (coverage check only — strategy parameters
are fixed) on [train_start, train_end] and is scored on [test_start, test_end].
Indicator warmup/look-ahead guard: the engine runs on (train tail + test) but
only the test segment scores; trades entered within embargo_bdays of the test
start are excluded from OOS metrics.

Single-period runs remain IN-SAMPLE by definition; only concatenated OOS folds
may be reported as out-of-sample evidence.

Outputs (inside experimental/reports/ only, enforced):
  walkforward_<run_id>.json / _metrics.csv / _oos_equity.csv / _summary.md
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, List

import pandas as pd

from experimental.backtest.engine import BacktestEngine
from experimental.backtest.io import load_ohlcv
from experimental.backtest.metrics import compute_metrics, metrics_to_dict
from experimental.backtest.validate import ValidationError


def _resolve_output_dir(output_dir: str | Path) -> Path:
    out = Path(output_dir)
    if not out.is_absolute():
        out = Path.cwd() / out
    root = (Path.cwd() / "experimental" / "reports").resolve()
    try:
        out.resolve().relative_to(root)
    except ValueError:
        raise ValueError(f"Output dir must be inside experimental/reports/, got {output_dir}")
    out.mkdir(parents=True, exist_ok=True)
    return out


def _run_id(*hashes: str) -> str:
    return hashlib.sha256("|".join(hashes).encode("utf-8")).hexdigest()[:12]


def run_walkforward(config_path: str | Path) -> dict:
    """Run walk-forward OOS evaluation. Raises on validation/coverage failure."""
    cfg_path = Path(config_path)
    cfg = json.loads(cfg_path.read_bytes().decode("utf-8"))
    strategy_path = cfg["strategy_config_path"]
    data_path = cfg["data_input_path"]
    fx_path = cfg.get("fx_config_path", "experimental/backtest/config/fx_rates.json")
    output_dir = _resolve_output_dir(cfg.get("output_dir", "experimental/reports"))
    folds = cfg.get("folds")
    if not folds:
        raise ValueError("walkforward config needs explicit 'folds' (train/test date ranges)")
    warmup_bars = int(cfg.get("warmup_bars", 60))
    embargo_bdays = int(cfg.get("embargo_bdays", 5))
    min_train_bars = int(cfg.get("minimum_train_bars", 252))

    df, meta = load_ohlcv(data_path, fx_path)
    df = df.sort_values(["symbol", "date"]).reset_index(drop=True)
    engine = BacktestEngine(strategy_path, fx_path)

    oos_curves: List[pd.Series] = []
    oos_trades: List[dict] = []
    fold_reports: List[dict] = []
    for i, fold in enumerate(folds):
        train = df[(df["date"] >= fold["train_start"]) & (df["date"] <= fold["train_end"])]
        test = df[(df["date"] >= fold["test_start"]) & (df["date"] <= fold["test_end"])]
        if len(train) < min_train_bars:
            raise ValueError(f"fold {i}: train bars {len(train)} < minimum {min_train_bars}")
        if test.empty:
            raise ValueError(f"fold {i}: empty test segment")
        warmup = train.sort_values("date").groupby("symbol").tail(warmup_bars)
        run_df = pd.concat([warmup, test]).sort_values(["symbol", "date"]).reset_index(drop=True)
        try:
            result = engine.run(run_df, meta)
        except ValidationError as exc:
            raise ValueError(f"fold {i}: validation failed: {exc}") from exc
        curve: pd.Series = result["equity_curve"].sort_index()
        seg = curve[curve.index >= pd.Timestamp(fold["test_start"], tz="utc")]
        if seg.empty:
            raise ValueError(f"fold {i}: no scored bars in test segment")
        oos_curves.append(seg)
        cutoff = pd.Timestamp(fold["test_start"], tz="utc") + pd.Timedelta(days=embargo_bdays)
        kept = [t for t in result.get("trades", [])
                if isinstance(t, dict) and pd.Timestamp(t.get("entry_date")) >= cutoff]
        oos_trades.extend(kept)
        fold_reports.append({
            "fold": i, "train_start": fold["train_start"], "train_end": fold["train_end"],
            "test_start": fold["test_start"], "test_end": fold["test_end"],
            "train_bars": len(train), "scored_bars": len(seg), "oos_trades": len(kept),
        })

    oos_curve = pd.concat(oos_curves).sort_index()
    oos_curve = oos_curve[~oos_curve.index.duplicated(keep="last")]
    metrics = metrics_to_dict(compute_metrics(oos_curve, oos_trades))
    run_id = _run_id(meta.get("data_hash", ""), meta.get("fx_hash", ""),
                     hashlib.sha256(cfg_path.read_bytes()).hexdigest())
    (output_dir / f"walkforward_{run_id}.json").write_text(json.dumps({
        "run_id": run_id, "folds": fold_reports, "metrics_oos": metrics,
        "data_hash": meta.get("data_hash"), "fx_hash": meta.get("fx_hash"),
        "disclaimer": "Out-of-sample on walk-forward folds only; not live-trading evidence.",
    }, indent=2), encoding="utf-8")
    oos_curve.to_csv(output_dir / f"walkforward_{run_id}_oos_equity.csv", header=True)
    (output_dir / f"walkforward_{run_id}_summary.md").write_text(
        f"# Walk-forward OOS `{run_id}`\n\nFolds: {len(folds)} | "
        f"OOS bars: {len(oos_curve)} | OOS trades: {len(oos_trades)} | "
        f"CAGR: {metrics.get('cagr', 0):.2%} | MaxDD: {metrics.get('max_drawdown', 0):.2%}\n",
        encoding="utf-8")
    return {"run_id": run_id, "folds": fold_reports, "metrics_oos": metrics,
            "oos_bars": len(oos_curve), "oos_trades": len(oos_trades)}
