"""Ticker suggestions via Yahoo search API (offline: requests monkeypatched).

User rule: tests use API/offline only, no browser, no network.
"""
from __future__ import annotations


def test_score_candidate_prefers_exact_symbol():
    from investment_engine.research.ticker_suggest import score_candidate

    exact = score_candidate("SUI", {"symbol": "SUI-USD", "shortname": "Sui", "exch": "CCC", "type": "CRYPTO"})
    other = score_candidate("SUI", {"symbol": "SUICIDE-SQUEEZE", "shortname": "Unrelated", "exch": "PNK", "type": "EQUITY"})
    assert exact > other


def test_suggest_maps_yahoo_response(monkeypatch):
    import investment_engine.research.ticker_suggest as ts

    class _Resp:
        status_code = 200

        def json(self):
            return {"quotes": [
                {"symbol": "SUI-USD", "shortname": "Sui", "exchange": "CCC", "quoteType": "CRYPTOCURRENCY"},
                {"symbol": "SU", "shortname": "Schneider Electric", "exchange": "PAR", "quoteType": "EQUITY"},
            ]}

        def raise_for_status(self):
            return None

    monkeypatch.setattr(ts.requests, "get", lambda *a, **k: _Resp())
    out = ts.suggest_yahoo_symbols("SUI", limit=5)
    assert out and out[0]["symbol"] == "SUI-USD"
    assert all("score" in c for c in out)


def test_suggest_never_raises_on_network_error(monkeypatch):
    import investment_engine.research.ticker_suggest as ts

    def _boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(ts.requests, "get", _boom)
    assert ts.suggest_yahoo_symbols("HY9H") == []


def test_render_failed_tickers_includes_suggestions():
    from investment_engine.reporting.failed_tickers import render_failed_tickers_md

    failed = [{"display": "SUI", "t212_id": "SUI_EQ", "yahoo_attempted": "SUI-USD",
               "support_state": "UNRESOLVED", "fetch_failed": True,
               "qty": 1, "value_eur": 10.0, "category": "HELD_UNRESOLVED"}]
    suggestions = {"SUI": [{"symbol": "SUI-USD", "name": "Sui", "exch": "CCC",
                            "type": "CRYPTO", "score": 90}]}
    md = render_failed_tickers_md(failed, "run1", suggestions=suggestions)
    assert "SUI-USD" in md and "symbol_aliases" in md
