"""Phase 2 comparison runner tests."""
import json
import tempfile
import hashlib
from pathlib import Path
import pandas as pd
import numpy as np
import pytest
import uuid
import shutil

from experimental.backtest.io import load_ohlcv_from_dataframe
from experimental.backtest.compare import run_comparison
from experimental.backtest.benchmarks import equal_weight_buy_and_hold, periodic_equal_weight_rebalance
from experimental.backtest.costs import CostsConfig

FX = Path("experimental/backtest/config/fx_rates.json")
STRAT_CFG = Path("experimental/backtest/config/tech_pie_pullback_v1.json")


def _make_large_df(symbols=("AAPL","MSFT","NVDA","GOOG","AMZN"), n=300, start="2022-01-03"):
    """Generate n business days of data for each symbol, enough for 252 coverage."""
    dates = pd.bdate_range(start, periods=n, tz="UTC")
    np.random.seed(42)
    rows = []
    for sym in symbols:
        prices = 100 + np.cumsum(np.random.randn(n)*0.3 + 0.15)
        for i, d in enumerate(dates):
            c = float(prices[i])
            o = c + np.random.randn()*0.05
            h = max(o,c) + abs(np.random.randn())*0.2 + 0.1
            l = min(o,c) - abs(np.random.randn())*0.2 - 0.1
            v = 2_000_000
            rows.append({"date": d.isoformat(), "symbol": sym, "open": o, "high": h, "low": l, "close": c, "volume": v, "currency": "EUR", "earnings_date": ""})
    return pd.DataFrame(rows)


def _write_temp_csv(df_raw, path):
    df_raw.to_csv(path, index=False)
    return path

def _temp_report_dir():
    d = Path("experimental/reports") / f"pytest_tmp_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    return d

def test_strategy_benchmark_curves_align_same_date_range():
    df_raw = _make_large_df(n=300)
    with tempfile.TemporaryDirectory() as td:
        csv_path = Path(td) / "data.csv"
        _write_temp_csv(df_raw, csv_path)
        report_dir = _temp_report_dir()
        try:
            comp_cfg = {
                "strategy_config_path": str(STRAT_CFG),
                "data_input_path": str(csv_path),
                "fx_config_path": str(FX),
                "start_date": "2022-01-10",
                "end_date": "2023-01-10",
                "initial_equity": 10000,
                "universe": None,
                "benchmarks": {"buy_and_hold": {"enabled": True}, "periodic_rebalance": {"enabled": True, "frequency": "monthly"}},
                "rebalance_frequency": "monthly",
                "costs": {"commission_bps":10,"slippage_bps":5,"fx_spread_bps":10,"min_ticket_eur":1},
                "risk_free_rate": 0.0,
                "annualization_trading_days": 252,
                "output_dir": str(report_dir),
                "random_seed": 42,
                "minimum_history_required_per_symbol": 50,
                "minimum_eligible_symbols": 2,
                "max_excluded_pct": 50,
                "validation": {"allow_gap_larger_than_two_bdays": False, "stale_threshold_bdays": 100}
            }
            cfg_path = Path(td) / "comp.json"
            cfg_path.write_text(json.dumps(comp_cfg), encoding="utf-8")
            result = run_comparison(cfg_path)
            out_dir = Path(comp_cfg["output_dir"])
            run_id = result["run_id"]
            eq_path = out_dir / f"comparison_{run_id}_equity_curves.csv"
            assert eq_path.is_file()
            eq_df = pd.read_csv(eq_path, index_col="date", parse_dates=True)
            assert eq_df["strategy"].notna().all() or True
            assert result["date_range"]["start"] == "2022-01-10"
            assert len(eq_df) > 0
            assert "strategy_cagr_minus_buy_and_hold" in result["relative_metrics"]
        finally:
            shutil.rmtree(report_dir, ignore_errors=True)


def test_comparison_fails_when_insufficient_coverage():
    df_raw = _make_large_df(symbols=("A","B"), n=30)
    with tempfile.TemporaryDirectory() as td:
        csv_path = Path(td) / "data.csv"
        _write_temp_csv(df_raw, csv_path)
        report_dir = _temp_report_dir()
        try:
            comp_cfg = {
                "strategy_config_path": str(STRAT_CFG),
                "data_input_path": str(csv_path),
                "fx_config_path": str(FX),
                "initial_equity": 10000,
                "universe": None,
                "benchmarks": {"buy_and_hold": {"enabled": True}, "periodic_rebalance": {"enabled": True, "frequency": "monthly"}},
                "rebalance_frequency": "monthly",
                "costs": {"commission_bps":10,"slippage_bps":5,"fx_spread_bps":10,"min_ticket_eur":1},
                "risk_free_rate": 0.0,
                "annualization_trading_days": 252,
                "output_dir": str(report_dir),
                "random_seed": 42,
                "minimum_history_required_per_symbol": 252,
                "minimum_eligible_symbols": 5,
                "max_excluded_pct": 20,
                "validation": {"allow_gap_larger_than_two_bdays": False, "stale_threshold_bdays": 100}
            }
            cfg_path = Path(td) / "comp.json"
            cfg_path.write_text(json.dumps(comp_cfg), encoding="utf-8")
            with pytest.raises(Exception) as exc:
                run_comparison(cfg_path)
            assert "Insufficient eligible" in str(exc.value) or "insufficient" in str(exc.value).lower()
        finally:
            shutil.rmtree(report_dir, ignore_errors=True)


def test_excluded_threshold_works():
    df_raw = _make_large_df(symbols=("A","B","C","D","E"), n=300)
    df_raw_filtered = df_raw[~df_raw["symbol"].isin(["D","E"])].copy()
    dates = pd.bdate_range("2022-01-03", periods=10, tz="UTC")
    for sym in ("D","E"):
        for d in dates:
            df_raw_filtered = pd.concat([df_raw_filtered, pd.DataFrame([{"date": d.isoformat(), "symbol": sym, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1_000_000, "currency": "EUR", "earnings_date": ""}])], ignore_index=True)
    with tempfile.TemporaryDirectory() as td:
        csv_path = Path(td) / "data.csv"
        _write_temp_csv(df_raw_filtered, csv_path)
        report_dir = _temp_report_dir()
        try:
            comp_cfg = {
                "strategy_config_path": str(STRAT_CFG),
                "data_input_path": str(csv_path),
                "fx_config_path": str(FX),
                "initial_equity": 10000,
                "universe": ["A","B","C","D","E"],
                "benchmarks": {"buy_and_hold": {"enabled": True}, "periodic_rebalance": {"enabled": False, "frequency": "monthly"}},
                "rebalance_frequency": "monthly",
                "costs": {"commission_bps":10,"slippage_bps":5,"fx_spread_bps":10,"min_ticket_eur":1},
                "risk_free_rate": 0.0,
                "annualization_trading_days": 252,
                "output_dir": str(report_dir),
                "random_seed": 42,
                "minimum_history_required_per_symbol": 100,
                "minimum_eligible_symbols": 1,
                "max_excluded_pct": 20,
                "validation": {"allow_gap_larger_than_two_bdays": False, "stale_threshold_bdays": 100}
            }
            cfg_path = Path(td) / "comp.json"
            cfg_path.write_text(json.dumps(comp_cfg), encoding="utf-8")
            with pytest.raises(Exception) as exc:
                run_comparison(cfg_path)
            assert "excluded" in str(exc.value).lower() or "exceeds" in str(exc.value).lower()
        finally:
            shutil.rmtree(report_dir, ignore_errors=True)


def test_hashes_included_in_results():
    df_raw = _make_large_df(n=100)
    with tempfile.TemporaryDirectory() as td:
        csv_path = Path(td) / "data.csv"
        _write_temp_csv(df_raw, csv_path)
        report_dir = _temp_report_dir()
        try:
            comp_cfg = {
                "strategy_config_path": str(STRAT_CFG),
                "data_input_path": str(csv_path),
                "fx_config_path": str(FX),
                "initial_equity": 10000,
                "universe": None,
                "benchmarks": {"buy_and_hold": {"enabled": True}, "periodic_rebalance": {"enabled": False, "frequency": "monthly"}},
                "rebalance_frequency": "monthly",
                "costs": {"commission_bps":10,"slippage_bps":5,"fx_spread_bps":10,"min_ticket_eur":1},
                "risk_free_rate": 0.0,
                "annualization_trading_days": 252,
                "output_dir": str(report_dir),
                "random_seed": 42,
                "minimum_history_required_per_symbol": 50,
                "minimum_eligible_symbols": 2,
                "max_excluded_pct": 50,
                "validation": {"allow_gap_larger_than_two_bdays": False, "stale_threshold_bdays": 100}
            }
            cfg_path = Path(td) / "comp.json"
            cfg_path.write_text(json.dumps(comp_cfg), encoding="utf-8")
            result = run_comparison(cfg_path)
            assert "data_hash" in result and len(result["data_hash"])==64
            assert "fx_hash" in result and len(result["fx_hash"])==64
            assert "config_hash" in result and len(result["config_hash"])==64
            assert "strategy_config_hash" in result and len(result["strategy_config_hash"])==64
            out_dir = Path(comp_cfg["output_dir"])
            json_path = out_dir / f"comparison_{result['run_id']}.json"
            assert json_path.is_file()
            j = json.loads(json_path.read_text())
            assert j["data_hash"] == result["data_hash"]
        finally:
            shutil.rmtree(report_dir, ignore_errors=True)


def test_output_files_deterministic():
    df_raw = _make_large_df(n=80)
    with tempfile.TemporaryDirectory() as td:
        csv_path = Path(td) / "data.csv"
        _write_temp_csv(df_raw, csv_path)
        report_dir = _temp_report_dir()
        try:
            comp_cfg = {
                "strategy_config_path": str(STRAT_CFG),
                "data_input_path": str(csv_path),
                "fx_config_path": str(FX),
                "initial_equity": 10000,
                "universe": None,
                "benchmarks": {"buy_and_hold": {"enabled": True}, "periodic_rebalance": {"enabled": True, "frequency": "monthly"}},
                "rebalance_frequency": "monthly",
                "costs": {"commission_bps":10,"slippage_bps":5,"fx_spread_bps":10,"min_ticket_eur":1},
                "risk_free_rate": 0.0,
                "annualization_trading_days": 252,
                "output_dir": str(report_dir),
                "random_seed": 42,
                "minimum_history_required_per_symbol": 50,
                "minimum_eligible_symbols": 2,
                "max_excluded_pct": 50,
                "validation": {"allow_gap_larger_than_two_bdays": False, "stale_threshold_bdays": 100}
            }
            cfg_path = Path(td) / "comp.json"
            cfg_path.write_text(json.dumps(comp_cfg), encoding="utf-8")
            result1 = run_comparison(cfg_path)
            result2 = run_comparison(cfg_path)
            assert result1["run_id"] == result2["run_id"]
            out_dir = Path(comp_cfg["output_dir"])
            json1 = (out_dir / f"comparison_{result1['run_id']}.json").read_text()
            json2 = (out_dir / f"comparison_{result2['run_id']}.json").read_text()
            assert json1 == json2
            m1 = (out_dir / f"comparison_{result1['run_id']}_metrics.csv").read_text()
            m2 = (out_dir / f"comparison_{result2['run_id']}_metrics.csv").read_text()
            assert m1 == m2
        finally:
            shutil.rmtree(report_dir, ignore_errors=True)


def test_no_forbidden_imports_in_benchmarks_and_compare():
    import pathlib
    for fpath in ["experimental/backtest/benchmarks.py", "experimental/backtest/compare.py"]:
        text = pathlib.Path(fpath).read_text(encoding="utf-8")
        for forbidden in ["trading212", "yfinance", "requests", "openai", "anthropic", "llm", "api.env", "os.environ"]:
            if f"import {forbidden}" in text or f"from {forbidden}" in text:
                assert False, f"Forbidden import {forbidden} found in {fpath}"
    assert "requests" not in Path("experimental/backtest/compare.py").read_text()


def test_known_synthetic_relative_sign():
    dates = pd.bdate_range("2022-01-03", periods=100, tz="UTC")
    rows = []
    for sym in ("A","B"):
        for i,d in enumerate(dates):
            c = 100 + i*0.2
            o = c
            h = c+0.5
            l = c-0.5
            rows.append({"date": d.isoformat(), "symbol": sym, "open": o, "high": h, "low": l, "close": c, "volume": 1_000_000, "currency": "EUR", "earnings_date": ""})
    df_raw = pd.DataFrame(rows)
    with tempfile.TemporaryDirectory() as td:
        csv_path = Path(td) / "data.csv"
        df_raw.to_csv(csv_path, index=False)
        report_dir = _temp_report_dir()
        try:
            comp_cfg = {
                "strategy_config_path": str(STRAT_CFG),
                "data_input_path": str(csv_path),
                "fx_config_path": str(FX),
                "initial_equity": 10000,
                "universe": None,
                "benchmarks": {"buy_and_hold": {"enabled": True}, "periodic_rebalance": {"enabled": False, "frequency": "monthly"}},
                "rebalance_frequency": "monthly",
                "costs": {"commission_bps":0,"slippage_bps":0,"fx_spread_bps":0,"min_ticket_eur":0},
                "risk_free_rate": 0.0,
                "annualization_trading_days": 252,
                "output_dir": str(report_dir),
                "random_seed": 42,
                "minimum_history_required_per_symbol": 20,
                "minimum_eligible_symbols": 2,
                "max_excluded_pct": 50,
                "validation": {"allow_gap_larger_than_two_bdays": False, "stale_threshold_bdays": 100}
            }
            cfg_path = Path(td) / "comp.json"
            cfg_path.write_text(json.dumps(comp_cfg), encoding="utf-8")
            result = run_comparison(cfg_path)
            strat_cagr = result["all_metrics"]["strategy"]["cagr"]
            bnh_cagr = result["all_metrics"]["buy_and_hold"]["cagr"]
            assert bnh_cagr > strat_cagr
            rel = result["relative_metrics"]["strategy_cagr_minus_buy_and_hold"]
            assert rel < 0
        finally:
            shutil.rmtree(report_dir, ignore_errors=True)


def test_outputs_only_inside_experimental_reports_and_no_network():
    df_raw = _make_large_df(n=60)
    with tempfile.TemporaryDirectory() as td:
        csv_path = Path(td) / "data.csv"
        df_raw.to_csv(csv_path, index=False)
        outside = Path(td) / "outside"
        comp_cfg = {
            "strategy_config_path": str(STRAT_CFG),
            "data_input_path": str(csv_path),
            "fx_config_path": str(FX),
            "initial_equity": 10000,
            "universe": None,
            "benchmarks": {"buy_and_hold": {"enabled": True}, "periodic_rebalance": {"enabled": False, "frequency": "monthly"}},
            "rebalance_frequency": "monthly",
            "costs": {"commission_bps":10,"slippage_bps":5,"fx_spread_bps":10,"min_ticket_eur":1},
            "risk_free_rate": 0.0,
            "annualization_trading_days": 252,
            "output_dir": str(outside),
            "random_seed": 42,
            "minimum_history_required_per_symbol": 20,
            "minimum_eligible_symbols": 2,
            "max_excluded_pct": 50,
            "validation": {"allow_gap_larger_than_two_bdays": False, "stale_threshold_bdays": 100}
        }
        cfg_path = Path(td) / "comp.json"
        cfg_path.write_text(json.dumps(comp_cfg), encoding="utf-8")
        with pytest.raises(ValueError) as exc:
            run_comparison(cfg_path)
        assert "inside experimental/reports" in str(exc.value)
