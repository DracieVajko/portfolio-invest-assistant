"""Run 2026-09-28 fixes: LM Studio patience/pre-flight, reasoning guard,
discovery headlines plumbing, news-filter stats, adaptive S/R currency.

Strictly offline: all HTTP is mocked, FX uses injected/static rates.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


# ---------------------------------------------------------------------------
# broker_first: adaptive currency
# ---------------------------------------------------------------------------

def test_normalize_quote_currency_gbp_variants():
    from investment_engine.portfolio.broker_first import normalize_quote_currency as n

    assert n("GBp") == "GBX"
    assert n("GBX") == "GBX"
    assert n("gbx") == "GBX"
    assert n("USD") == "USD"
    assert n("DKK") == "DKK"
    assert n("") == ""


def test_convert_to_eur_passthrough_live_pence_unresolved_invalid():
    from investment_engine.portfolio.broker_first import convert_to_eur as cv

    assert cv(12.345, "EUR", {}) == (12.35, "EUR")
    assert cv(100.0, "USD", {"USD": 0.9}) == (90.0, "live-fx")
    # GBX pence: 1500p -> 15 GBP x static 1.183 = 17.745 -> 17.75
    val, status = cv(1500.0, "GBX", {})
    assert status == "static-fallback" and abs(val - 17.75) < 1e-9
    # Yahoo GBp normalizes to the same pence path
    val2, _ = cv(1500.0, "GBp", {})
    assert val2 == val
    # DKK via static fallback
    val3, status3 = cv(193.65, "DKK", {})
    assert status3 == "static-fallback" and abs(val3 - 193.65 * 0.134) < 0.01
    assert cv(10.0, "XXX", {}) == (None, "unresolved")
    assert cv(None, "USD", {}) == (None, "invalid")
    assert cv(float("nan"), "USD", {}) == (None, "invalid")


# ---------------------------------------------------------------------------
# lmstudio: pre-flight skip, patient warmup, no reasoning dump
# ---------------------------------------------------------------------------

def _lm():
    from investment_engine.providers import lmstudio as mod
    return mod


def test_warmup_budget_covers_slow_model_loads():
    mod = _lm()
    assert mod._WARMUP_READ_S >= 60  # 30B-class loads take 10-60s; 10s aborted them
    assert mod._WARMUP_CONNECT_S >= 10


def test_preflight_skips_unserved_model_without_post(monkeypatch):
    mod = _lm()
    mod.reset_server_models_cache()

    class _Models:
        status_code = 200

        def json(self):
            return {"data": [{"id": "qwen3.8-9b-distill"}]}

    posts = []
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: _Models())
    monkeypatch.setattr(mod.requests, "post",
                        lambda *a, **k: posts.append(1) or _never())
    p = mod.LMStudioProvider(base_url="http://x:1234/v1", model="qwen3.8-9b-distill")
    out = p.generate("hi", stage="decision", context={"model": "some-unserved-model"})
    assert out.startswith("[decision]")
    assert "not served" in out
    assert posts == []  # no warmup / generation attempted


def _never():  # pragma: no cover - must not be called
    raise AssertionError("POST must not be attempted for unserved model")


def test_preflight_unknown_server_proceeds(monkeypatch):
    mod = _lm()
    mod.reset_server_models_cache()

    def _down(*a, **k):
        raise ConnectionError("no server")

    monkeypatch.setattr(mod.requests, "get", _down)
    monkeypatch.setattr(mod.LMStudioProvider, "_warmup_model", lambda self, model: None)

    class _Ok:
        status_code = 200
        text = "{}"

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "hello"}}]}

    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: _Ok())
    p = mod.LMStudioProvider(base_url="http://x:1234/v1", model="m")
    assert p.generate("hi", stage="decision", context={}) == "hello"


def test_reasoning_content_never_leaks_into_reports(monkeypatch):
    """content='' + reasoning trace must be an error, not a thinking dump."""
    mod = _lm()
    mod.reset_server_models_cache()
    monkeypatch.setattr(mod, "server_model_ids", lambda base: frozenset({"m"}))
    monkeypatch.setattr(mod.LMStudioProvider, "_warmup_model", lambda self, model: None)

    class _Resp:
        status_code = 200
        text = "{}"

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {
                "content": "",
                "reasoning_content": "Thinking Process:\n1. analyze ..."}}]}

    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: _Resp())
    p = mod.LMStudioProvider(base_url="http://x:1234/v1", model="m")
    out = p.generate("hi", stage="decision", context={})
    assert out.startswith("[decision]")
    assert "Thinking Process" not in out


def test_server_model_ids_never_raises(monkeypatch):
    mod = _lm()
    mod.reset_server_models_cache()
    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert mod.server_model_ids("http://x:1234/v1") is None


# ---------------------------------------------------------------------------
# news filter stats
# ---------------------------------------------------------------------------

def _rec(title, url, hours_ago=5, relevance=80, symbol="AAPL"):
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {"title": title, "url": url, "source": "Reuters",
            "published_dt": dt, "relevance_score": relevance,
            "symbol_key": symbol, "symbol": symbol}


def test_filter_decision_news_stats_breakdown():
    from investment_engine.research.news_engine import filter_decision_news as f

    stats: dict = {}
    out = f({"AAPL": [
        _rec("Good earnings beat", "https://example.com/good", hours_ago=5),
        _rec("Stale story", "https://example.com/stale", hours_ago=100),
        _rec("Low relevance", "https://example.com/low", relevance=10),
        _rec("No url", "", hours_ago=5),
    ], "ZZZ": [
        _rec("Unrelated corp", "https://example.com/other", hours_ago=5, symbol="ZZZ"),
    ]}, owned={"AAPL"}, relevance_min=60, stats=stats)
    assert stats == {"input": 5, "no_title_url": 1, "below_relevance": 1,
                     "too_old": 1, "unlinked": 1, "kept": 1}
    assert len(out) == 1 and out[0]["title"] == "Good earnings beat"


# ---------------------------------------------------------------------------
# rows + monitoring: EUR levels, pence gate
# ---------------------------------------------------------------------------

def _pos(symbol, value, qty=1.0):
    return {
        "symbol": symbol, "quantity": qty,
        "average_price_eur": 10.0, "current_price_eur": 10.0,
        "value_eur": value, "cost_basis_eur": value,
        "pnl_eur": 0.0, "pnl_pct": 0.0,
        "is_pie_constituent": False,
        "validation_status": "PASS", "validation_reason": "",
    }


def _t212(all_positions, fx_rates=None):
    summary = {
        "total_equity": 1000.0, "all_positions": all_positions,
        "positions": list(all_positions),
        "cash_free": 0.0, "cash_pie": 0.0, "cash_blocked": 0.0,
        "reconciliation_threshold": 10.0,
    }
    if fx_rates is not None:
        summary["fx_rates_used"] = {"rates": dict(fx_rates), "source": "test"}
    return {"status": "ok", "account_summary": summary,
            "cash": {}, "positions": list(all_positions),
            "all_positions": list(all_positions)}


def test_rows_convert_sr_to_eur_per_ticker_currency():
    from investment_engine.reporting.regime_report import build_unified_portfolio_rows as rows

    tech = {"AAPL": {"Support": 280.0, "Resistance": 320.0,
                     "quote_currency": "USD", "RSI_14": 50.0}}
    pos = _pos("AAPL_US_EQ", 500.0)
    pos["current_price_eur"] = 297.42  # realistic anchor for USD levels
    pos["average_price_eur"] = 286.21
    data = _t212([pos], fx_rates={"USD": 0.9})
    (row,) = [r for r in rows(data, {}, tech, {}, {"AAPL"}) if r["ticker"] == "AAPL"]
    assert row["support"] == 280.0 and row["resistance"] == 320.0  # natives kept
    assert row["support_eur"] == 252.0 and row["resistance_eur"] == 288.0
    assert row["sr_currency"] == "USD" and row["sr_fx_status"] == "live-fx"


def test_rows_convert_gbx_pence_to_eur():
    from investment_engine.reporting.regime_report import build_unified_portfolio_rows as rows

    tech = {"EGT": {"Support": 11.99, "Resistance": 14.50,
                    "quote_currency": "GBX", "RSI_14": 40.0}}
    pos = _pos("EGTl_EQ", 200.0)
    pos["current_price_eur"] = 0.15  # realistic anchor: 14p ~= EUR 0.15
    pos["average_price_eur"] = 0.16
    data = _t212([pos], fx_rates={"GBP": 1.18})
    (row,) = [r for r in rows(data, {}, tech, {}, {"EGT"}) if r["ticker"] == "EGT"]
    # 11.99p -> 0.1199 GBP x 1.18 (anchor confirms: near the EUR 0.15 price)
    assert abs(row["support_eur"] - round(0.1199 * 1.18, 2)) < 1e-9
    assert row["sr_fx_status"] == "live-fx"


def test_monitoring_gate_treats_gbx_gbp_as_same_pence():
    from investment_engine.reporting.documents import build_monitoring_items as items

    row = {"display_symbol": "EGT", "ticker": "EGT", "company": "EGT",
           "signal": "HOLD", "weight": 1.0, "pnl_pct": 0.0, "market_value": 10.0,
           "quote_currency": "GBX", "external_quote_currency": "GBp",
           "current_price_native": 14.1, "support": 13.0, "resistance": 14.2,
           "support_eur": 0.15, "resistance_eur": 0.16,
           "external_mapping_status": "VERIFIED", "market_data_state": "SUPPORTED"}
    out = items([row], recon_status="PASS", report_date=date(2026, 9, 28))
    assert len(out) == 1  # gate passed despite GBX vs GBp spelling
    assert any("1.5%" in t for t in out[0]["triggers"].split(";"))
    assert out[0]["support_eur"] == 0.15 and out[0]["resistance_eur"] == 0.16


def test_eur_level_prefers_converted_falls_back_to_raw():
    from investment_engine.reporting.documents import _eur_level as lvl

    assert lvl({"support": 300.0, "support_eur": 276.6}, "support") == 276.6
    assert lvl({"support": 300.0}, "support") == 300.0
    assert lvl({}, "support") is None
    assert lvl(None, "support") is None


def test_eur_level_hides_raw_on_failed_conversion():
    """New-style rows with failed conversion must never leak raw natives.

    Run ccb9a975: C7A0 row had support_eur=None/sr_fx_status=unresolved yet
    the snapshot rendered raw 285.74 (CNY) next to a EUR 54.19 price.
    """
    from investment_engine.reporting.documents import _eur_level as lvl

    row = {"support": 285.74, "resistance": 356.50,
           "support_eur": None, "resistance_eur": None,
           "sr_currency": "CNY", "sr_fx_status": "unresolved"}
    assert lvl(row, "support") is None
    assert lvl(row, "resistance") is None
    assert lvl(row, "support_eur") is None


def test_rows_suppress_levels_on_tech_price_mismatch():
    """Yahoo mapping to a different listing/scale must suppress S/R.

    Run ccb9a975: ERNX (Yahoo 100.7 vs broker 5.62) and LAR0D (LOM.DE 445
    vs broker 296.25) rendered bogus levels + false breach triggers.
    """
    from investment_engine.reporting.regime_report import build_unified_portfolio_rows as rows

    tech = {"ERNX": {"price": 100.7, "Support": 100.6, "Resistance": 101.0,
                     "quote_currency": "EUR", "RSI_14": 77.1}}
    pos = _pos("ERNXd_EQ", 108.24)
    pos["current_price_eur"] = 5.62
    pos["average_price_eur"] = 5.60
    data = _t212([pos], fx_rates={"USD": 0.9})
    (row,) = [r for r in rows(data, {}, tech, {}, {"ERNX"}) if r["ticker"] == "ERNX"]
    assert row["support"] is None and row["resistance"] is None
    assert row["support_eur"] is None and row["resistance_eur"] is None
    assert str(row["sr_fx_status"]).startswith("suspect")


def test_rows_suppress_levels_on_cross_listing_mismatch():
    from investment_engine.reporting.regime_report import build_unified_portfolio_rows as rows

    tech = {"LAR0D": {"price": 448.6, "Support": 445.0, "Resistance": 471.6,
                      "quote_currency": "EUR", "RSI_14": 50.0}}
    pos = _pos("LAR0d_EQ", 28.82)
    pos["current_price_eur"] = 296.25
    pos["average_price_eur"] = 270.95
    data = _t212([pos], fx_rates={"USD": 0.9})
    (row,) = [r for r in rows(data, {}, tech, {}, {"LAR0D"}) if r["ticker"] == "LAR0D"]
    assert row["support"] is None and row["support_eur"] is None
    assert str(row["sr_fx_status"]).startswith("suspect")


def test_convert_to_eur_pence_override():
    from investment_engine.portfolio.broker_first import convert_to_eur as cv

    # Forced major-unit reading of a pence-labelled quote (LITM.L case).
    assert cv(5.34, "GBX", {"GBP": 1.18}, pence=False) == (round(5.34 * 1.18, 2), "live-fx")
    assert cv(5.34, "GBX", {"GBP": 1.18}, pence=True) == (round(0.0534 * 1.18, 2), "live-fx")


def test_anchor_corrects_mislabeled_gbx_history():
    from investment_engine.portfolio.broker_first import anchor_levels_to_eur as anchor

    # Live LITM.L case: 5.34/5.97 labelled GBX at a EUR 6.27 broker price.
    sup, res, status = anchor(5.34, 5.97, "GBX", 6.27, {"GBP": 1.18})
    assert sup == round(5.34 * 1.18, 2) and res == round(5.97 * 1.18, 2)
    assert "corrected" in status


def test_anchor_keeps_true_pence():
    from investment_engine.portfolio.broker_first import anchor_levels_to_eur as anchor

    # Live EGT case: 13-14p at a EUR 0.15 broker price — primary is right.
    sup, res, status = anchor(13.0, 14.2, "GBX", 0.15, {"GBP": 1.18})
    assert sup == round(0.13 * 1.18, 2) and res == round(0.142 * 1.18, 2)
    assert "corrected" not in status and status != "suspect"


def test_anchor_suspect_when_nothing_fits():
    from investment_engine.portfolio.broker_first import anchor_levels_to_eur as anchor

    assert anchor(100.0, 200.0, "USD", 1.0, {"USD": 0.9}) == (None, None, "suspect")


def test_anchor_no_broker_price_keeps_primary():
    from investment_engine.portfolio.broker_first import anchor_levels_to_eur as anchor

    assert anchor(280.0, 320.0, "USD", 0, {"USD": 0.9})[:2] == (252.0, 288.0)


def test_finance_stages_get_long_read_budget():
    import investment_engine.main as m
    from investment_engine.providers.fallback import ChainedFallbackProvider
    from investment_engine.providers.lmstudio import LMStudioProvider

    class _StubLM(LMStudioProvider):
        def __init__(self):
            self.seen = []

        @property
        def name(self):
            return "LM Studio"

        def generate(self, prompt, *, stage, context=None):
            self.seen.append(((context or {}).get("model"), (context or {}).get("read_timeout_s")))
            return "finance answer"

    s = {"lmstudio_enabled": True, "decision_model": "D", "writer_model": "W",
         "finance_model": "F", "thinking_model": "T"}
    chain = ChainedFallbackProvider([_StubLM()])
    served, out = m._try_lmstudio_stage(chain, "p", settings=s, stage="decision", tokens=100)
    assert (served, out) == ("F", "finance answer")
    assert chain.providers[0].seen == [("F", 1200)]


def test_rows_anchor_litm_like_mislabel_and_native_fallback():
    from investment_engine.reporting.regime_report import build_unified_portfolio_rows as rows

    tech = {"LITM": {"Support": 5.34, "Resistance": 5.97,
                     "quote_currency": "GBX", "RSI_14": 32.5}}
    pos = _pos("LITMm_EQ", 39.15)
    pos["current_price_eur"] = 6.27
    pos["average_price_eur"] = 6.87
    data = _t212([pos], fx_rates={"GBP": 1.18})
    (row,) = [r for r in rows(data, {}, tech, {}, {"LITM"}) if r["ticker"] == "LITM"]
    assert row["support_eur"] == round(5.34 * 1.18, 2)
    assert "corrected" in row["sr_fx_status"]
    # EUR position without a separate native quote falls back to EUR price.
    assert row["current_price_native"] == 6.27
