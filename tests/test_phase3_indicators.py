"""Phase 3 indicator policy A: canonical manual engine + reference parity.

Frozen seeded fixture (no network). Documents intentional manual-vs-Wilder
divergence instead of forcing equality.
"""

from __future__ import annotations

import pytest

from investment_engine.research.indicators import (
    ENGINE_VERSION,
    MANUAL_COLUMNS,
    IndicatorConfig,
    apply_manual_indicators,
    resolve_engine,
)


def _fixture():
    import sys

    sys.path.insert(0, ".")
    from scripts.verify_indicators import build_fixture

    return build_fixture()


def test_engine_version_pinned():
    assert ENGINE_VERSION == "manual-v1"


def test_column_contract():
    out = apply_manual_indicators(_fixture())
    missing = [c for c in MANUAL_COLUMNS if c not in out.columns]
    assert not missing, missing


def test_resolve_engine_matrix():
    assert resolve_engine("manual", True) == "manual"
    assert resolve_engine("manual", False) == "manual"
    assert resolve_engine("reference", True) == "reference"
    assert resolve_engine("auto", True) == "reference"
    assert resolve_engine("auto", False) == "manual"
    with pytest.raises(RuntimeError):
        resolve_engine("reference", False)


def test_analyzer_manual_matches_canonical_function():
    import pandas as pd

    from investment_engine.research.technical_analysis import TechnicalAnalyzer

    df = _fixture()
    a = TechnicalAnalyzer(engine="manual").analyze(df.copy())
    b = apply_manual_indicators(df.copy())
    pd.testing.assert_frame_equal(a, b)


def test_analyzer_engine_flag():
    from investment_engine.research.technical_analysis import TechnicalAnalyzer

    assert TechnicalAnalyzer(engine="manual").resolved_engine == "manual"


def test_sma_ema_macd_match_reference_exactly():
    from experimental.backtest.indicators import ema, macd, sma

    df = _fixture()
    m = apply_manual_indicators(df.copy())
    last = m.index[-1]
    assert abs(m.loc[last, "SMA_20"] - sma(df["close"], 20).iloc[-1]) < 1e-9
    assert abs(m.loc[last, "EMA_12"] - ema(df["close"], 12).iloc[-1]) < 1e-6
    ref = macd(df["close"]).iloc[-1].tolist()
    assert abs(m.loc[last, "MACD"] - ref[0]) < 1e-9
    assert abs(m.loc[last, "MACD_SIGNAL"] - ref[1]) < 1e-9
    assert abs(m.loc[last, "MACD_HIST"] - ref[2]) < 1e-9


def test_rsi_atr_documented_divergence_bounds():
    """SMA-variant vs Wilder: bounded, never equal-forced (observed 6.0 / 21%)."""
    from experimental.backtest.indicators import atr, rsi

    df = _fixture()
    m = apply_manual_indicators(df.copy())
    last = m.index[-1]
    assert abs(m.loc[last, "RSI_14"] - rsi(df["close"]).iloc[-1]) < 15.0
    ref_atr = atr(df["high"], df["low"], df["close"]).iloc[-1]
    assert abs(m.loc[last, "ATR_14"] - ref_atr) / ref_atr < 0.50


def test_adx_direction_agreement():
    from experimental.backtest.indicators import adx

    df = _fixture()
    m = apply_manual_indicators(df.copy())
    last = m.index[-1]
    ref = adx(df["high"], df["low"], df["close"]).iloc[-1]
    assert (m.loc[last, "DMP"] > m.loc[last, "DMN"]) == (ref["PLUS_DI"] > ref["MINUS_DI"])


def test_supertrend_state_agreement_on_frozen_fixture():
    """Frozen fixture + frozen code: state must agree here; self-consistency exact."""
    import numpy as np

    from experimental.backtest.indicators import supertrend

    df = _fixture()
    m = apply_manual_indicators(df.copy())
    last = m.index[-1]
    ref_dir = supertrend(df["high"], df["low"], df["close"]).iloc[-1]["SUPERT_DIR"]
    assert m.loc[last, "SUPERT_DIR"] == ref_dir
    expect = np.where(df["close"] > m["SUPERT_LONG"], 1, -1)
    assert (m["SUPERT_DIR"].to_numpy() == expect).all()


def test_market_data_uses_adjust_false():
    import pathlib

    src = pathlib.Path("investment_engine/research/market_data.py").read_text(encoding="utf-8")
    assert "close.ewm(span=12, adjust=False)" in src
    assert "close.ewm(span=26, adjust=False)" in src
    assert "macd.ewm(span=9, adjust=False)" in src


def test_manifest_carries_indicator_attribution(tmp_path):
    import json

    import portfolio_ai_assistant as entry

    result = {
        "brief_markdown": "", "snapshot_markdown": "",
        "reconciliation": {"status": "UNKNOWN"},
        "failed_tickers": [], "monitoring_items": [], "decision_news": [],
        "brief_metadata": {},
    }
    entry.write_reports(result, tmp_path / "o", tmp_path / "a", "t3")
    manifest = json.loads((tmp_path / "o" / "current" / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["indicator_engine"] in ("manual", "reference")
    assert isinstance(manifest["pandas_ta_present"], bool)
    assert isinstance(manifest["talib_present"], bool)
    assert set(manifest["versions"]) >= {"python", "pandas", "numpy", "scipy"}
    assert "stance" in manifest and "providers_per_stage" in manifest


def test_no_reference_imports_outside_allowed_modules():
    import pathlib

    for rel in ("investment_engine/portfolio", "investment_engine/reporting",
                "investment_engine/schemas"):
        for path in pathlib.Path(rel).rglob("*.py"):
            src = path.read_text(encoding="utf-8")
            assert "import pandas_ta" not in src, path
            assert "import talib" not in src, path
