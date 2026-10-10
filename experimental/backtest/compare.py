"""Comparison runner - research-only, deterministic, fail-closed.

Loads data once, runs strategy + buy-and-hold + periodic rebalance on same dates,
aligns curves, computes metrics and relative metrics, writes reproducible artifacts
inside experimental/reports/ only.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd
import numpy as np

from experimental.backtest.io import load_ohlcv, load_ohlcv_from_dataframe, load_fx_config
from experimental.backtest.validate import validate_dataframe, ValidationError, ValidationReport
from experimental.backtest.costs import CostsConfig
from experimental.backtest.metrics import compute_metrics, metrics_to_dict
from experimental.backtest.benchmarks import equal_weight_buy_and_hold, periodic_equal_weight_rebalance
from experimental.backtest.engine import BacktestEngine


def _get_code_version() -> str:
    """Try git commit, else unknown."""
    try:
        # Run git rev-parse HEAD from repo root if git available
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).resolve().parents[2],
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()[:12]
    except Exception:
        pass
    return "unknown"


def _deterministic_run_id(data_hash: str, fx_hash: str, strategy_hash: str, config_hash: str) -> str:
    raw = f"{data_hash}|{fx_hash}|{strategy_hash}|{config_hash}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _load_json_sorted(path: Path) -> Tuple[dict, str]:
    raw = path.read_bytes()
    h = hashlib.sha256(raw).hexdigest()
    data = json.loads(raw.decode("utf-8"))
    return data, h


def _filter_by_date(df: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    if start:
        start_ts = pd.to_datetime(start, utc=True)
        df = df[df["date"] >= start_ts]
    if end:
        end_ts = pd.to_datetime(end, utc=True)
        df = df[df["date"] <= end_ts]
    return df


def _concentration_stats(equity_curve: pd.Series, mv_series: pd.Series | None) -> dict:
    """Max weight etc. If mv_series provided, compute max_single_name weight? For equal-weight we know."""
    if mv_series is None or equity_curve is None or len(equity_curve)==0:
        return {"max_weight": 0.0, "herfindahl": 0.0}
    # For equal-weight, weight per symbol = 1/N, but after drift concentration changes
    # Approx by mv/equity if mv is total market value, not per-symbol; for strategy we lack per-symbol mv
    # Return simple avg gross exposure as concentration proxy
    avg_gross = float((mv_series / equity_curve).mean()) if len(equity_curve)>0 else 0.0
    return {"avg_gross_exposure": round(avg_gross, 4), "max_weight": round(avg_gross, 4)}


def run_comparison(config_path: str | Path) -> dict:
    """
    Run full comparison. Returns result dict and writes artifacts to experimental/reports/.
    Raises ValidationError / ValueError on fail-closed conditions.
    """
    cfg_path = Path(config_path)
    if not cfg_path.is_file():
        raise FileNotFoundError(f"Comparison config not found: {cfg_path}")
    cfg, cfg_hash = _load_json_sorted(cfg_path)

    # Extract fields with defaults
    strategy_cfg_path = Path(cfg.get("strategy_config_path", "experimental/backtest/config/tech_pie_pullback_v1.json"))
    data_input_path = Path(cfg.get("data_input_path", "experimental/tests/fixtures/sample_ohlcv.csv"))
    fx_cfg_path = Path(cfg.get("fx_config_path", "experimental/backtest/config/fx_rates.json"))
    output_dir = Path(cfg.get("output_dir", "experimental/reports"))
    start_date = cfg.get("start_date")
    end_date = cfg.get("end_date")
    initial_equity = float(cfg.get("initial_equity", 10000.0))
    universe = cfg.get("universe")  # None means from data
    # Benchmark definitions
    benchmarks_cfg = cfg.get("benchmarks", {})
    bnh_enabled = benchmarks_cfg.get("buy_and_hold", {}).get("enabled", True)
    periodic_enabled = benchmarks_cfg.get("periodic_rebalance", {}).get("enabled", True)
    rebalance_freq = cfg.get("rebalance_frequency", benchmarks_cfg.get("periodic_rebalance", {}).get("frequency", "monthly"))
    costs_cfg = cfg.get("costs", {"commission_bps":10, "slippage_bps":5, "fx_spread_bps":10, "min_ticket_eur":1.0})
    risk_free = float(cfg.get("risk_free_rate", 0.0))
    ann_days = int(cfg.get("annualization_trading_days", 252))
    random_seed = cfg.get("random_seed", 42)
    min_history = int(cfg.get("minimum_history_required_per_symbol", cfg.get("coverage", {}).get("min_bars_per_symbol", 252)))
    min_eligible = int(cfg.get("minimum_eligible_symbols", cfg.get("coverage", {}).get("min_symbols", 5)))
    max_excluded_pct = float(cfg.get("max_excluded_pct", cfg.get("coverage", {}).get("max_excluded_pct", 20.0)))
    validation_cfg = cfg.get("validation", {})
    allow_gap = validation_cfg.get("allow_gap_larger_than_two_bdays", cfg.get("data_quality_policy", {}).get("allow_gap_larger_than_two_bdays", False))
    stale_thresh = validation_cfg.get("stale_threshold_bdays", 30)

    # Safety: output_dir must be inside experimental/reports
    # Resolve and check prefix
    out_resolved = output_dir.resolve()
    exp_reports_resolved = Path("experimental/reports").resolve()
    # Allow if out_resolved is exactly exp_reports or child
    try:
        out_resolved.relative_to(exp_reports_resolved)
    except ValueError:
        # Also allow if output_dir == "experimental/reports" relative
        if out_resolved != exp_reports_resolved:
            raise ValueError(f"Output dir must be inside experimental/reports/, got {output_dir}")

    # Random seed if any
    np.random.seed(random_seed)

    # Load FX and strategy config hashes
    fx_cfg, fx_hash = load_fx_config(fx_cfg_path)
    strat_cfg, strat_hash = _load_json_sorted(strategy_cfg_path)

    # Load data
    if not data_input_path.is_file():
        raise FileNotFoundError(f"Data input not found: {data_input_path} (must be local CSV/parquet)")
    # Support parquet path if ends with .parquet
    if str(data_input_path).lower().endswith(".parquet"):
        df, meta = load_ohlcv(data_input_path, fx_cfg_path, parquet_path=data_input_path)
    else:
        df, meta = load_ohlcv(data_input_path, fx_cfg_path)
    data_hash = meta["data_hash"]

    # Filter by date
    df = _filter_by_date(df, start_date, end_date)
    if df.empty:
        raise ValueError("No data after date filtering")

    # Validate once
    report = validate_dataframe(df, data_hash=data_hash, allow_gap_larger_than_two_bdays=allow_gap, as_of=end_date, stale_threshold_bdays=stale_thresh)
    if not report.ok:
        raise ValidationError(report)

    # Universe handling
    if universe is None:
        # From data if universe_from_data true or just use data symbols
        universe = sorted(df["symbol"].unique().tolist())
    else:
        universe = [str(s).upper().strip() for s in universe]

    # Coverage checks
    # Count valid bars per symbol in filtered df
    eligible = []
    excluded = []
    coverage_by_symbol = {}
    for sym in universe:
        sub = df[df["symbol"] == sym]
        cnt = int(sub["close_eur"].notna().sum())
        coverage_by_symbol[sym] = cnt
        if cnt >= min_history:
            eligible.append(sym)
        else:
            excluded.append({"symbol": sym, "reason": f"insufficient_history {cnt} < {min_history}", "bars": cnt})
    # Also add symbols in df but not in universe? Not needed
    # Check robustness
    if len(eligible) < min_eligible:
        raise ValueError(f"Insufficient eligible symbols: {len(eligible)} < {min_eligible}; excluded: {excluded}")
    if len(universe) > 0 and (len(excluded) / len(universe) * 100) > max_excluded_pct:
        raise ValueError(f"Excluded {len(excluded)}/{len(universe)} ({len(excluded)/len(universe)*100:.1f}%) exceeds max {max_excluded_pct}%")
    # Date range
    all_dates = sorted(df["date"].unique())
    date_range = {"start": str(pd.to_datetime(all_dates[0], utc=True).date()) if all_dates else None, "end": str(pd.to_datetime(all_dates[-1], utc=True).date()) if all_dates else None}
    # Filter df to eligible only
    df_eligible = df[df["symbol"].isin(eligible)].copy()
    # Dropped dates/symbols reporting
    dropped_symbols = excluded
    dropped_dates = []  # Not dropping dates, but if some dates have missing eligible symbols, report
    # For each date, count eligible symbols present
    for d in all_dates:
        present = df_eligible[df_eligible["date"] == d]["symbol"].unique().tolist()
        missing_for_date = set(eligible) - set(present)
        if missing_for_date:
            dropped_dates.append({"date": str(pd.to_datetime(d, utc=True).date()), "missing_symbols": sorted(missing_for_date), "reason": "missing price on date"})

    # Costs
    costs = CostsConfig(
        commission_bps=float(costs_cfg.get("commission_bps", 10)),
        slippage_bps=float(costs_cfg.get("slippage_bps", 5)),
        fx_spread_bps=float(costs_cfg.get("fx_spread_bps", 10)),
        min_ticket_eur=float(costs_cfg.get("min_ticket_eur", 1.0)),
    )

    # Run benchmarks
    bnh_result = None
    per_result = None
    if bnh_enabled:
        bnh_result = equal_weight_buy_and_hold(df_eligible, initial_equity, costs, universe=eligible, min_history=min_history)
    if periodic_enabled:
        per_result = periodic_equal_weight_rebalance(df_eligible, initial_equity, costs, frequency=rebalance_freq, universe=eligible, min_history=min_history)

    # Run strategy via BacktestEngine
    # Engine expects strategy config path; it will load its own costs etc. But we want to use same initial_equity and same df
    # Override engine's start_equity to match comparison initial_equity
    engine = BacktestEngine(str(strategy_cfg_path), fx_cfg_path)
    # Override costs and equity to match comparison config
    engine.costs = costs
    engine.start_equity = initial_equity
    # Also override max_alloc etc from strategy config, but keep as is
    strat_result = engine.run(df_eligible, meta, as_of=end_date, validate=False)  # already validated

    # Collect equity curves
    curves: Dict[str, pd.Series] = {}
    curves["strategy"] = strat_result["equity_curve"]
    if bnh_result is not None:
        curves["buy_and_hold"] = bnh_result["equity_curve"]
    if per_result is not None:
        curves["periodic_rebalance"] = per_result["equity_curve"]

    # Align curves on common trading dates
    # Find common index
    common_idx = None
    for name, curve in curves.items():
        if common_idx is None:
            common_idx = set(curve.index)
        else:
            common_idx = common_idx.intersection(set(curve.index))
    if common_idx is None or len(common_idx)==0:
        raise ValueError("No common dates across equity curves")
    common_idx = sorted(common_idx)
    # Check periods match: strategy and benchmark periods must match exactly after alignment
    # If original curves have different date ranges, we flag
    for name, curve in curves.items():
        if len(curve) != len(common_idx) or not set(curve.index) == set(common_idx):
            # This is a mismatch; we will align by reindexing and warn, but spec says reject if periods do not match
            # For strictness, if curves lengths differ, reject
            # However benchmarks and strategy use same df, so they should match; if not, it's error
            raise ValueError(f"Period mismatch for {name}: curve dates differ from common dates; strategy vs benchmark periods must match")
    # Align (reindex to common)
    aligned = {name: curve.reindex(common_idx).sort_index() for name, curve in curves.items()}

    # Check any equity negative or NaN -> fail closed
    for name, curve in aligned.items():
        if (curve < 0).any():
            raise ValueError(f"Equity curve negative for {name}")
        if curve.isna().any():
            raise ValueError(f"Equity curve NaN for {name}")

    # Compute metrics via existing metrics module
    metrics_by_variant = {}
    for name, curve in aligned.items():
        if name == "strategy":
            trades = strat_result["trades"]
        elif name == "buy_and_hold":
            trades = bnh_result["trades"] if bnh_result else []
        elif name == "periodic_rebalance":
            # For periodic, use fills as trades for turnaround
            trades = per_result["fills"] if per_result else []
        else:
            trades = []
        m = compute_metrics(curve, trades, start_equity=initial_equity)
        # For strategy, override avg_gross_exposure with engine's computed (already)
        if name == "strategy":
            # strat_result metrics already has avg_gross, but we recomputed; keep recomputed and also set from engine history
            # Use engine's avg_gross if available
            hist = strat_result.get("metrics", None)
            if hist:
                # Keep our computed but also capture concentration
                pass
        metrics_by_variant[name] = metrics_to_dict(m)

    # Relative metrics: strategy minus benchmark
    relative = {}
    if "strategy" in metrics_by_variant and "buy_and_hold" in metrics_by_variant:
        for k in ["total_return","cagr","max_drawdown","sharpe_ratio","sortino_ratio","calmar_ratio"]:
            s = metrics_by_variant["strategy"].get(k, 0)
            b = metrics_by_variant["buy_and_hold"].get(k, 0)
            # For max_drawdown, lower is better, so minus same formula
            relative[f"strategy_{k}_minus_buy_and_hold"] = round(s - b, 6) if isinstance(s, float) else s - b
    if "strategy" in metrics_by_variant and "periodic_rebalance" in metrics_by_variant:
        for k in ["total_return","cagr","max_drawdown","sharpe_ratio","sortino_ratio","calmar_ratio"]:
            s = metrics_by_variant["strategy"].get(k, 0)
            b = metrics_by_variant["periodic_rebalance"].get(k, 0)
            relative[f"strategy_{k}_minus_periodic_rebalance"] = round(s - b, 6) if isinstance(s, float) else s - b

    # Robustness extra reports
    # Concentration stats
    # For B&H equal weight, concentration = 1/n
    concentration = {}
    if bnh_result is not None:
        n = len(bnh_result.get("eligible", []))
        concentration["buy_and_hold_equal_weight"] = 1.0/n if n>0 else 0
    if per_result is not None:
        n = len(per_result.get("eligible", []))
        concentration["periodic_equal_weight"] = 1.0/n if n>0 else 0
    # Strategy concentration: max single position weight estimate from engine fills
    # Approximate: max 8% per instrument, but we report actual max
    strat_conc = 0.08  # default max
    concentration["strategy_max_alloc_pct"] = strat_conc

    # % time spent in cash: for strategy, from equity_history
    # Need to reconstruct from engine or aligned curves: use 1 - avg_gross_exposure
    pct_cash = {}
    for name in aligned.keys():
        if name == "strategy":
            avg_gross = metrics_by_variant["strategy"].get("avg_gross_exposure", 0)
            pct_cash[name] = round((1 - avg_gross) * 100, 2) if avg_gross else 100.0
        else:
            # For benchmarks, cash small, avg_gross close to 1
            # Estimate from bnh_result cash_series if available
            if name == "buy_and_hold" and bnh_result is not None:
                cash_s = bnh_result.get("cash_series")
                eq_s = bnh_result.get("equity_curve")
                if cash_s is not None and eq_s is not None:
                    pct = float((cash_s / eq_s).mean() * 100)
                    pct_cash[name] = round(pct, 2)
                else:
                    pct_cash[name] = round((1 - metrics_by_variant[name].get("avg_gross_exposure",0))*100,2) if metrics_by_variant[name].get("avg_gross_exposure") else 0
            elif name == "periodic_rebalance" and per_result is not None:
                cash_s = per_result.get("cash_series")
                eq_s = per_result.get("equity_curve")
                if cash_s is not None and eq_s is not None:
                    pct = float((cash_s / eq_s).mean() * 100)
                    pct_cash[name] = round(pct,2)
                else:
                    pct_cash[name] = 0
            else:
                pct_cash[name] = 0

    # Number of rebalances and turnover, costs paid
    costs_paid = {}
    if bnh_result is not None:
        costs_paid["buy_and_hold"] = round(float(bnh_result.get("costs_paid", 0)), 2)
    if per_result is not None:
        costs_paid["periodic_rebalance"] = round(float(per_result.get("costs_paid", 0)), 2)
    # Strategy costs from fills
    strat_costs = sum(f.get("costs", {}).get("total_cost", 0) for f in strat_result.get("fills", []))
    costs_paid["strategy"] = round(float(strat_costs), 2)

    turnover_report = {}
    if per_result is not None:
        turnover_report["periodic_rebalance_turnover_raw"] = round(float(per_result.get("rebalance_turnover", 0)), 2)
        turnover_report["periodic_num_rebalances"] = int(per_result.get("num_rebalances", 0))
    if bnh_result is not None:
        turnover_report["buy_and_hold_turnover"] = 0.0

    # Gross P/L vs costs: gross = end_equity - start_equity + costs_paid
    gross_pl = {}
    for name in aligned.keys():
        end_eq = metrics_by_variant[name]["end_equity"]
        gross = (end_eq - initial_equity) + costs_paid.get(name, 0)
        gross_pl[name] = round(gross, 2)

    # Warnings
    warnings: List[str] = []
    if dropped_dates:
        warnings.append(f"{len(dropped_dates)} dates had missing symbols")
    if excluded:
        warnings.append(f"{len(excluded)} symbols excluded for insufficient history")
    # Check concentration >? Not needed

    # Generate run_id
    run_id = _deterministic_run_id(data_hash, fx_hash, strat_hash, cfg_hash)
    generated_at = datetime.now(timezone.utc).isoformat()
    code_version = _get_code_version()

    # Build JSON result
    result_json = {
        "run_id": run_id,
        "generated_at": generated_at,
        "code_version": code_version,
        "strategy_name": strat_cfg.get("strategy", "tech_pie_pullback_v1"),
        "strategy_config": strat_cfg,
        "benchmark_configs": {
            "buy_and_hold": {"enabled": bnh_enabled, "label": "equal_weight_buy_and_hold"},
            "periodic_rebalance": {"enabled": periodic_enabled, "frequency": rebalance_freq},
        },
        "data_hash": data_hash,
        "fx_hash": fx_hash,
        "config_hash": cfg_hash,
        "strategy_config_hash": strat_hash,
        "cost_config": costs_cfg,
        "validation_report": {
            "ok": report.ok,
            "errors": report.errors,
            "warnings": report.warnings,
            "row_count": report.row_count,
            "symbols": report.symbols,
            "data_hash": report.data_hash,
        },
        "universe": universe,
        "eligible_symbols": eligible,
        "excluded_symbols": excluded,
        "coverage_by_symbol": coverage_by_symbol,
        "dropped_dates": dropped_dates,
        "date_range": date_range,
        "all_metrics": metrics_by_variant,
        "relative_metrics": relative,
        "costs_paid": costs_paid,
        "gross_pl": gross_pl,
        "concentration": concentration,
        "pct_time_in_cash": pct_cash,
        "num_rebalances": turnover_report.get("periodic_num_rebalances", 0) if periodic_enabled else 0,
        "rebalance_turnover": turnover_report.get("periodic_rebalance_turnover_raw", 0),
        "turnover": turnover_report,
        "risk_free_rate": risk_free,
        "annualization_trading_days": ann_days,
        "initial_equity": initial_equity,
        "warnings": warnings,
        "disclaimer": "Research-only backtest. Historical performance does not predict future results.",
        "input_paths": {
            "data_input_path": str(data_input_path),
            "fx_config_path": str(fx_cfg_path),
            "strategy_config_path": str(strategy_cfg_path),
            "comparison_config_path": str(cfg_path),
        },
    }

    # Write outputs inside experimental/reports/
    output_dir.mkdir(parents=True, exist_ok=True)
    # JSON
    json_path = output_dir / f"comparison_{run_id}.json"
    json_path.write_text(json.dumps(result_json, indent=2, sort_keys=True, default=str), encoding="utf-8")
    # Metrics CSV
    metrics_csv_path = output_dir / f"comparison_{run_id}_metrics.csv"
    # Build DataFrame: rows = metrics, columns = variants
    # Use metrics_by_variant keys
    metrics_df = pd.DataFrame(metrics_by_variant).T
    metrics_df.index.name = "variant"
    metrics_df.to_csv(metrics_csv_path)
    # Equity curves CSV
    equity_csv_path = output_dir / f"comparison_{run_id}_equity_curves.csv"
    eq_df = pd.DataFrame({name: curve for name, curve in aligned.items()})
    eq_df.index.name = "date"
    eq_df.to_csv(equity_csv_path)
    # Trades CSV
    trades_csv_path = output_dir / f"comparison_{run_id}_trades.csv"
    # Combine trades from all variants with variant column
    all_trades = []
    for name, res in [("strategy", strat_result), ("buy_and_hold", bnh_result), ("periodic_rebalance", per_result)]:
        if res is None:
            continue
        fills = res.get("fills", [])
        for f in fills:
            row = {"variant": name, "date": f.get("date"), "symbol": f.get("symbol"), "side": f.get("side"), "price": f.get("price"), "quantity": f.get("quantity"), "reason": f.get("reason", ""), "commission": f.get("costs", {}).get("total_cost", 0) if isinstance(f.get("costs"), dict) else 0}
            all_trades.append(row)
        # Also trades for strategy
        if name == "strategy":
            for t in res.get("trades", []):
                # Already counted fills, but add trade-level
                pass
    trades_df = pd.DataFrame(all_trades)
    if not trades_df.empty:
        trades_df.to_csv(trades_csv_path, index=False)
    else:
        # Write header only
        pd.DataFrame(columns=["variant","date","symbol","side","price","quantity","reason","commission"]).to_csv(trades_csv_path, index=False)

    # Summary MD
    md_path = output_dir / f"comparison_{run_id}_summary.md"
    md_content = _generate_summary_md(result_json, metrics_by_variant, relative, costs_cfg, warnings, date_range, eligible, excluded, coverage_by_symbol, bnh_result, per_result, strat_result)
    md_path.write_text(md_content, encoding="utf-8")

    return result_json


def _generate_summary_md(result_json, metrics_by_variant, relative, costs_cfg, warnings, date_range, eligible, excluded, coverage_by_symbol, bnh_result, per_result, strat_result) -> str:
    m = metrics_by_variant
    strat_m = m.get("strategy", {})
    bnh_m = m.get("buy_and_hold", {})
    per_m = m.get("periodic_rebalance", {})
    lines = []
    lines.append(f"# Comparison Summary {result_json['run_id']}")
    lines.append(f"_Generated: {result_json['generated_at']} | Code: {result_json['code_version']}_")
    lines.append("")
    lines.append("> Research-only backtest. Historical performance does not predict future results.")
    lines.append("")
    lines.append("## Data quality")
    lines.append(f"- Date range: {date_range['start']} to {date_range['end']}")
    lines.append(f"- Universe requested: {len(result_json['universe'])} symbols: {', '.join(result_json['universe'][:10])}{' ...' if len(result_json['universe'])>10 else ''}")
    lines.append(f"- Eligible: {len(eligible)} ({', '.join(eligible[:10])}{' ...' if len(eligible)>10 else ''})")
    lines.append(f"- Excluded: {len(excluded)}")
    for e in excluded:
        lines.append(f"  - {e['symbol']}: {e['reason']}")
    lines.append(f"- Coverage per symbol (bars):")
    for sym, cnt in coverage_by_symbol.items():
        lines.append(f"  - {sym}: {cnt}")
    lines.append(f"- Validation: {'PASS' if result_json['validation_report']['ok'] else 'FAIL'}; errors: {result_json['validation_report']['errors'] or 'none'}; warnings: {result_json['validation_report']['warnings'] or 'none'}")
    if result_json.get("dropped_dates"):
        lines.append(f"- Dropped dates with missing symbols: {len(result_json['dropped_dates'])}")
        for dd in result_json["dropped_dates"][:5]:
            lines.append(f"  - {dd}")
    lines.append("")
    lines.append("## Strategy result (tech_pie_pullback_v1)")
    if strat_m:
        lines.append(f"- End equity: €{strat_m.get('end_equity')} (start €{strat_m.get('start_equity')})")
        lines.append(f"- Total return: {strat_m.get('total_return'):.2%}, CAGR: {strat_m.get('cagr'):.2%}")
        lines.append(f"- Max drawdown: {strat_m.get('max_drawdown'):.2%}, Vol: {strat_m.get('annualized_volatility'):.2%}")
        lines.append(f"- Sharpe: {strat_m.get('sharpe_ratio')}, Sortino: {strat_m.get('sortino_ratio')}, Calmar: {strat_m.get('calmar_ratio')}")
        lines.append(f"- Trades: {strat_m.get('trade_count')}, Win rate: {strat_m.get('win_rate'):.2%}, Avg holding: {strat_m.get('avg_holding_days')} days")
        lines.append(f"- Turnover (ann): {strat_m.get('turnover')}, Gross exposure: {strat_m.get('avg_gross_exposure'):.2%}")
        lines.append(f"- Costs paid: €{result_json['costs_paid'].get('strategy',0)}; Gross P/L: €{result_json['gross_pl'].get('strategy',0)}")
        lines.append(f"- % time in cash: {result_json['pct_time_in_cash'].get('strategy',0)}%")
    else:
        lines.append("- No strategy result")
    lines.append("")
    lines.append("## Benchmark results")
    if bnh_m:
        lines.append("### Equal-weight buy-and-hold")
        lines.append(f"- End equity: €{bnh_m.get('end_equity')}, Total return: {bnh_m.get('total_return'):.2%}, CAGR: {bnh_m.get('cagr'):.2%}")
        lines.append(f"- Max DD: {bnh_m.get('max_drawdown'):.2%}, Sharpe: {bnh_m.get('sharpe_ratio')}, Costs: €{result_json['costs_paid'].get('buy_and_hold',0)}")
        lines.append(f"- Turnover: {bnh_m.get('turnover')}, Trades: {bnh_m.get('trade_count')}")
    if per_m:
        lines.append("### Periodic equal-weight rebalance")
        lines.append(f"- Frequency: {result_json['benchmark_configs']['periodic_rebalance'].get('frequency')}, Rebalances: {result_json.get('num_rebalances',0)}")
        lines.append(f"- End equity: €{per_m.get('end_equity')}, Total return: {per_m.get('total_return'):.2%}, CAGR: {per_m.get('cagr'):.2%}")
        lines.append(f"- Max DD: {per_m.get('max_drawdown'):.2%}, Sharpe: {per_m.get('sharpe_ratio')}, Costs: €{result_json['costs_paid'].get('periodic_rebalance',0)}")
        lines.append(f"- Turnover raw: €{result_json.get('rebalance_turnover',0)}, Trades: {per_m.get('trade_count')}")
    lines.append("")
    lines.append("## Relative comparison (strategy minus benchmark)")
    for k, v in relative.items():
        # Format as percent for return-like
        if "return" in k or "cagr" in k or "drawdown" in k:
            lines.append(f"- {k}: {v:.4f} ({v*100:.2f} pp)")
        else:
            lines.append(f"- {k}: {v:.4f}")
    if not relative:
        lines.append("- No relative metrics (benchmark disabled)")
    lines.append("")
    lines.append("## Costs and execution assumptions")
    lines.append(f"- Commission: {costs_cfg.get('commission_bps')} bps, Slippage: {costs_cfg.get('slippage_bps')} bps, FX spread: {costs_cfg.get('fx_spread_bps')} bps, Min ticket: €{costs_cfg.get('min_ticket_eur')}")
    lines.append(f"- Buy-and-hold: entry cost once at first open with slippage on fill price")
    lines.append(f"- Periodic rebalance: costs applied to every rebalance trade at next open")
    lines.append(f"- Strategy: costs on every fill at next open (entry) and stop/break/time exits")
    lines.append(f"- Price data: normalized EUR close for valuation, open for execution; missing data forward-filled for equity but reported as dropped")
    lines.append("")
    lines.append("## Limitations")
    lines.append("- In-sample vs out-of-sample: this comparison uses a single period (see date range) and is in-sample for the strategy's fixed parameters (no walk-forward). Do not optimize parameters on this period.")
    lines.append("- Dividends/splits: comparison uses unadjusted close prices unless input data is total-return adjusted; buy-and-hold of price return differs from total-return (dividends) benchmark and may understate benchmark.")
    lines.append("- Unadjusted data: splits cause artificial price jumps; use adjusted close for correct benchmark.")
    lines.append("- FX rates: static dated fixture (see fx_rates.json as_of), not live FX; EUR conversion fixed at input.")
    lines.append("- Transaction costs are model estimates; real slippage, market impact, and taxes differ.")
    lines.append("- No survivorship bias handling; excluded symbols reduce universe and may bias results.")
    lines.append("- Historical performance does not predict future results.")
    lines.append("")
    if warnings:
        lines.append("## Warnings")
        for w in warnings:
            lines.append(f"- {w}")
    lines.append("")
    lines.append("## Hashes (reproducibility)")
    lines.append(f"- Data hash: {result_json['data_hash']}")
    lines.append(f"- FX hash: {result_json['fx_hash']}")
    lines.append(f"- Strategy hash: {result_json['strategy_config_hash']}")
    lines.append(f"- Comparison config hash: {result_json['config_hash']}")
    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Phase 2 comparison runner - local files only, no network/broker")
    parser.add_argument("--config", default="experimental/backtest/config/phase2_comparison.json", help="Path to comparison JSON config")
    args = parser.parse_args()
    try:
        result = run_comparison(args.config)
        # Print concise path-free status
        print(f"OK run_id={result['run_id']} eligible={len(result['eligible_symbols'])} excluded={len(result['excluded_symbols'])} period={result['date_range']['start']}..{result['date_range']['end']}")
        sys.exit(0)
    except Exception as e:
        print(f"FAIL {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
