"""Guards against AI cash hallucinations + mapping fixes (offline, mocked)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from investment_engine.main import _format_cash_deployment


def _regime(cash_target=15.0):
    return SimpleNamespace(implications={"cash_target_pct": cash_target})


def _t212(free=0.0):
    return {"cash": {"free": free},
            "account_summary": {"cash_free": free, "total_equity": 3279.39}}


def _rows():
    return [{"internal_id": "AAPL_US_EQ", "ticker": "AAPL",
             "display_symbol": "AAPL"}]


def _ai_recs(free, deploy, reserve, targets):
    return {"cash_deployment": {
        "free_cash": free, "deploy_amount": deploy, "reserve": reserve,
        "targets": targets}}


def _pass_recon(monkeypatch):
    import investment_engine.reporting.regime_report as rr

    monkeypatch.setattr(
        rr, "compute_reconciliation", lambda *a, **k: {"status": "PASS"})


def test_hallucinated_cash_rejected(monkeypatch):
    """AI claims 491.10 free vs broker 0 -> deterministic branch, no XOM."""
    _pass_recon(monkeypatch)
    ai = _ai_recs(491.10, 73.67, 417.43,
                  [{"asset": "XOM_US_EQ", "amount": 112.0}])
    out = _format_cash_deployment(_regime(), _t212(free=0.0), ai, _rows())
    assert "XOM" not in out
    assert "491.10" not in out
    assert "No deployment" in out


def test_matching_cash_accepted_with_known_target(monkeypatch):
    _pass_recon(monkeypatch)
    ai = _ai_recs(40.0, 6.0, 34.0, [{"asset": "AAPL_US_EQ", "amount": 6.0}])
    out = _format_cash_deployment(_regime(), _t212(free=41.27), ai, _rows())
    assert "Deploy" in out and "AAPL" in out


def test_off_portfolio_target_dropped():
    ai = _ai_recs(40.0, 6.0, 34.0, [{"asset": "XOM_US_EQ", "amount": 112.0}])
    out = _format_cash_deployment(_regime(), _t212(free=41.27), ai, _rows())
    assert "XOM" not in out


def test_zero_cash_offers_rotation_not_suppression(monkeypatch):
    _pass_recon(monkeypatch)
    ai = _ai_recs(None, None, None, [])
    rows = [
        {"internal_id": "AAA_US_EQ", "ticker": "AAA", "display_symbol": "AAA",
         "signal": "BUY", "pnl_pct": 5.0},
        {"internal_id": "BBB_US_EQ", "ticker": "BBB", "display_symbol": "BBB",
         "signal": "HOLD", "pnl_pct": -12.5},
        {"internal_id": "CCC_US_EQ", "ticker": "CCC", "display_symbol": "CCC",
         "signal": "HOLD", "pnl_pct": -3.0},
    ]
    out = _format_cash_deployment(_regime(), _t212(free=0.0), ai, rows)
    assert "top-up" in out
    assert "BBB" in out  # weakest first
    assert "1 BUY signal" in out


def test_context_estimator_tracks_max_per_stage():
    from investment_engine.main import _note_context, get_context_estimates

    _note_context("probe-stage-xyz", "x" * 4000, 500)
    _note_context("probe-stage-xyz", "x" * 800, 100)
    est = get_context_estimates()["probe-stage-xyz"]
    assert est["input_tokens"] == 1000
    assert est["output_tokens"] == 500
    assert est["total"] == 1500
    assert est["calls"] == 2


def test_no_hardcoded_model_lie_in_reports():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for rel in ("investment_engine/reporting/regime_report.py",
                "investment_engine/reporting/report_structure.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "gpt-oss-20b / qwen3.8-9b / gemma4-12b" not in src
        assert "qwen3.8-9b-pi / qwen3-4b-pi / noema-2b" not in src
        assert "gemini-2.5-flash" not in src or "key-gated" in src


def test_unsupported_constant_restored():
    from investment_engine.portfolio.symbols import (
        support_state, UNSUPPORTED, UNRESOLVED,
    )

    assert UNSUPPORTED == "UNSUPPORTED"
    assert UNRESOLVED == "UNRESOLVED"
    assert UNSUPPORTED != UNRESOLVED
    assert support_state("", "market_data") == UNSUPPORTED
    assert support_state("UNKNOWN", "market_data") == UNSUPPORTED


def test_verified_registry_lifecycle():
    from investment_engine.portfolio.symbols import (
        support_state, note_verified, clear_verified,
    )

    clear_verified()
    assert support_state("ZZZ1Q", "market_data") == "UNRESOLVED"
    note_verified("ZZZ1Q")
    assert support_state("ZZZ1Q", "market_data") == "SUPPORTED"
    clear_verified()
    assert support_state("ZZZ1Q", "market_data") == "UNRESOLVED"


def test_live_retry_promotes_verified(monkeypatch):
    from investment_engine import main as engine

    monkeypatch.setattr(
        "investment_engine.portfolio.symbols.propose_yahoo_candidates",
        lambda *a, **k: [("SU.PA", "exchange_suffix:p")],
    )

    class FakeHist:
        empty = False

    class FakeTicker:
        def __init__(self, sym):
            self.sym = sym

        def history(self, period="5d"):
            assert self.sym == "SU.PA"
            return FakeHist()

    import yfinance as _yf
    monkeypatch.setattr(_yf, "Ticker", FakeTicker)
    from investment_engine.portfolio.symbols import clear_verified, support_state
    clear_verified()

    m = {"SU": None}
    engine._live_retry_unresolved(
        m, [{"display": "SU", "t212": "SUp_EQ"}], {})
    assert m["SU"] == "SU.PA"
    assert support_state("SU.PA", "market_data") == "SUPPORTED"
    clear_verified()


def test_alias_file_merge_precedence(tmp_path, monkeypatch):
    from investment_engine.main import _load_symbol_aliases

    alias_file = tmp_path / "data" / "ticker_aliases.json"
    alias_file.parent.mkdir(parents=True)
    alias_file.write_text(json.dumps({"AAA": "AAA.FILE", "BBB": "BBB.FILE"}),
                          encoding="utf-8")
    cfg = tmp_path / "portfolio_config.json"
    cfg.write_text(json.dumps({"symbol_aliases": {"BBB": "BBB.CFG"}}),
                   encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    out = _load_symbol_aliases({"config_path": str(cfg)})
    assert out["AAA"] == "AAA.FILE"   # from ticker_aliases.json
    assert out["BBB"] == "BBB.CFG"    # config wins
