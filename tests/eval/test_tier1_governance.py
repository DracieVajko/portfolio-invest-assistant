"""T10 log, T11 matrix, T14 profile, T15 gate — eval-named. Offline."""
from __future__ import annotations

import json
import pathlib

from scripts.decision_log import anti_churn, append_record, build_record, lookup_prior
from investment_engine.decision.matrix import decide
from investment_engine.reporting.regime_report import build_canonical_signals
from investment_engine.research.fundamentals import build_pack
from scripts.setup_agent import validate_profile

FIX = pathlib.Path(__file__).parent / "fixtures"


def test_t10_log_lifecycle(tmp_path):
    rec = build_record({"asset": {"ticker": "V", "isin": "US92826C8394"},
                        "mode": "M1", "verdict": "HOLD", "confidence": "high"},
                       created_utc="20260310T120000Z")
    append_record(rec, log_dir=tmp_path)
    import pytest

    with pytest.raises(FileExistsError):
        append_record(rec, log_dir=tmp_path)
    assert lookup_prior("V", log_dir=tmp_path)["verdict"] == "HOLD"
    assert lookup_prior("US92826C8394", log_dir=tmp_path) is not None
    fix = {"asset": {"ticker": "V"}, "mode": "M1", "verdict": "TRIM",
           "confidence": "medium", "supersedes": rec["id"]}
    append_record(build_record(fix, created_utc="20260311T120000Z"), log_dir=tmp_path)
    assert lookup_prior("V", log_dir=tmp_path)["verdict"] == "TRIM"
    assert anti_churn({"id": "a"}, regime_changed=True) == "repeat_prior"
    assert anti_churn({"id": "a"}, triggers_fired=["x"]) == "re-evaluate"
    assert anti_churn(None) == "no_prior"


def _decide(**kw):
    return decide(**kw)


def test_t11_matrix_rows():
    assert _decide(thesis="intact", valuation="attractive",
                   regime="STRONG_UPTREND", held=False)["default"] == "BUY"
    r = _decide(thesis="intact", valuation="attractive", regime="DOWNTREND",
                held=False, horizon="strategic", decline_thesis_relevant=False)
    assert set(r["allowed"]) == {"BUY", "WATCH"}
    r = _decide(thesis="intact", valuation="attractive", regime="DOWNTREND",
                held=False, fit_breach=True)
    assert "BUY" not in r["allowed"] and "ADD" not in r["allowed"]
    assert _decide(thesis="intact", valuation="fair", regime="UPTREND_PULLBACK",
                   held=True)["default"] == "HOLD"
    assert _decide(thesis="intact", valuation="fair", regime="DOWNTREND",
                   held=False, instrument_class="broad_fund")["default"] == "BUY"
    assert _decide(thesis="intact", valuation="stretched", regime="STRONG_UPTREND",
                   held=True, overweight=True)["default"] == "TRIM"
    assert _decide(thesis="intact", valuation="stretched", regime="STRONG_UPTREND",
                   held=True, overweight=False)["default"] == "HOLD"
    assert _decide(thesis="intact", valuation="stretched", regime="DOWNTREND",
                   held=True, overweight=False)["default"] == "HOLD"
    r = _decide(thesis="weakened", valuation="fair", regime="DOWNTREND", held=True)
    assert set(r["allowed"]) >= {"TRIM", "SELL"} and "AVOID" not in r["allowed"]
    assert _decide(thesis="weakened", valuation="fair", regime="DOWNTREND",
                   held=False)["default"] == "AVOID"
    assert _decide(thesis="broken", valuation="fair", regime="STRONG_UPTREND",
                   held=True)["default"] == "SELL"
    r = _decide(thesis="intact", valuation="attractive", regime="STRONG_UPTREND",
                modifiers=["EXTENDED"], held=False)
    assert r["default"] == "BUY" and any("tranche" in n for n in r["notes"])
    assert "WATCH" in _decide(thesis="intact", valuation="attractive",
                              regime="DOWNTREND", held=False,
                              horizon="tactical")["allowed"]
    # Guards hold on every row above by construction; spot-check:
    for row in ({"thesis": "broken", "valuation": "fair", "regime": "DOWNTREND",
                 "held": True},
                {"thesis": "weakened", "valuation": "fair", "regime": "DOWNTREND",
                 "held": False}):
        assert "AVOID" not in _decide(**row)["allowed"] or row["held"] is False


def test_t11_strengthened_maps_to_intact():
    a = _decide(thesis="strengthened", valuation="fair", regime="DOWNTREND", held=True)
    b = _decide(thesis="intact", valuation="fair", regime="DOWNTREND", held=True)
    assert a["allowed"] == b["allowed"] and a["default"] == b["default"]


def test_t14_profile_gates(tmp_path):
    full = tmp_path / "full.yaml"
    full.write_text(
        "schema_version: 1\nlanguage: sk\ntimezone: Europe/Bratislava\n"
        "base_currency: EUR\ntax_residency: SK\n"
        "accounts:\n  - broker: T212\n    coverage: complete\n"
        "risk_tolerance: medium\nmax_drawdown_tolerance_pct: 20\n"
        "max_single_stock_pct: 5\nmax_loss_per_position_pct: 1\n"
        "restrictions:\n  - no leverage\n"
        "benchmark: EXI2\nhigh_stakes_threshold_pct: 5\n", encoding="utf-8")
    assert validate_profile(full) == {"ok": True, "missing": [], "provisional": False}
    empty = tmp_path / "empty.yaml"
    empty.write_text("schema_version: 1\n", encoding="utf-8")
    got = validate_profile(empty)
    assert got["provisional"] is True and "max_loss_per_position_pct" in got["missing"]
    badver = tmp_path / "badver.yaml"
    badver.write_text("schema_version: 9\n", encoding="utf-8")
    assert validate_profile(badver)["ok"] is False


def test_t15_availability_gate():
    import pandas as pd

    def frame(rows, years):
        return pd.DataFrame(rows, index=list(years)).T

    years4 = ("2025", "2024", "2023", "2022")
    fin = frame({"Total Revenue": [130.0, 121.0, 110.0, 100.0],
                 "Net Income": [19.5, 18.15, 16.5, 15.0],
                 "Diluted EPS": [1.95, 1.815, 1.65, 1.5],
                 "Operating Cash Flow": [33.0, 30.0, 27.0, 25.0],
                 "Capital Expenditure": [-7.0, -6.0, -5.0, -5.0],
                 "Stock Based Compensation": [1.2, 1.0, 1.0, 1.0]}, years4)
    q = frame({"Total Revenue": [35.0, 33.0, 31.0, 29.0, 28.0],
               "Net Income": [5.5, 5.0, 4.5, 4.0, 3.8]},
              ("2025-Q4", "2025-Q3", "2025-Q2", "2025-Q1", "2024-Q4"))
    pack = build_pack(fin, pd.DataFrame(), pd.DataFrame(), q, symbol="G")
    assert pack["statements_available"] == {"annual": 4, "quarterly": 5}
    assert pack["ttm"]["revenue"] == 35.0 + 33.0 + 31.0 + 29.0
    assert pack["growth"]["revenue_cagr_10y"] is None
    assert pack["valuation_percentile"] == "not computable"
    assert pack["row_sources"]["eps_diluted"] == "Diluted EPS"
