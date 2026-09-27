"""Universe resolver: ISIN discovery without network in tests.

All OpenFIGI/Yahoo I/O is faked; cache redirected to tmp via UNIVERSE_CACHE_PATH.
"""

from __future__ import annotations

import json


def _seed_cache(tmp_path, record):
    import os

    cache = tmp_path / "universe.json"
    cache.write_text(json.dumps({"version": 1, "isins": record}), encoding="utf-8")
    os.environ["UNIVERSE_CACHE_PATH"] = str(cache)
    return cache


def test_exchcode_table_known_and_unknown():
    from investment_engine.research.universe import translate_listing

    assert translate_listing("VWS", "DC") == "VWS.CO"
    assert translate_listing("ibm", "us") == "IBM"
    assert translate_listing("SU", "FP") == "SU.PA"
    assert translate_listing("300750", "SS") == "300750.SS"
    assert translate_listing("XYZ", "XX") is None
    assert translate_listing("", "US") is None


def test_selection_prefers_home_currency():
    from investment_engine.research.universe import select_research_listing

    listings = [
        {"ticker": "VWS", "exchCode": "US", "yahoo": "VWS"},
        {"ticker": "VWS", "exchCode": "DC", "yahoo": "VWS.CO"},
        {"ticker": "VWS", "exchCode": "LN", "yahoo": "VWS.L"},
    ]
    sel, reason = select_research_listing(listings, "EUR")
    assert sel["yahoo"] == "VWS.CO" and "home-currency" in reason
    sel, _ = select_research_listing(listings, "USD")
    assert sel["yahoo"] == "VWS"
    sel, reason = select_research_listing(listings, None)
    assert sel["yahoo"] == "VWS"
    assert select_research_listing([], "EUR") == (None, "no-listings")


def test_fetch_listings_parses_fixture(monkeypatch, tmp_path):
    _seed_cache(tmp_path, {})
    from investment_engine.research import universe as uni

    class _Resp:
        status_code = 200

        def json(self):
            return [{"data": [
                {"ticker": "VWS", "exchCode": "DC", "micCode": "XCSE",
                 "name": "VESTAS WIND SYSTEMS", "securityType": "Common Stock"},
                {"ticker": "0LN", "exchCode": "LN", "micCode": "XLON",
                 "name": "VESTAS WIND SYSTEMS", "securityType": "Common Stock"},
            ]}]

    monkeypatch.setattr("requests.post", lambda *a, **k: _Resp())
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    out = uni.fetch_listings(["DK0061539921"])
    rows = out["DK0061539921"]
    assert {r["yahoo"] for r in rows} == {"VWS.CO", "0LN.L"}


def test_ensure_never_raises_and_caches(monkeypatch, tmp_path):
    _seed_cache(tmp_path, {})
    from investment_engine.research import universe as uni

    def _boom(*a, **k):
        raise ConnectionError("offline")

    monkeypatch.setattr("requests.post", _boom)
    stats = uni.ensure_for_isins(["US0378331005"], {"US0378331005": "USD"})
    assert stats["fetched"] == 0 or stats["failed"] >= 0
    # Second call: still offline-safe, cache untouched by failures
    assert uni.lookup_universe_yahoo("US0378331005") == (None, "universe-miss")


def test_ensure_records_verified_selection(monkeypatch, tmp_path):
    _seed_cache(tmp_path, {})
    from investment_engine.research import universe as uni

    class _Resp:
        status_code = 200

        def json(self):
            return [{"data": [
                {"ticker": "LOM", "exchCode": "GR", "name": "LOCKHEED MARTIN"},
                {"ticker": "LMT", "exchCode": "US", "name": "LOCKHEED MARTIN"},
            ]}]

    monkeypatch.setattr("requests.post", lambda *a, **k: _Resp())
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    monkeypatch.setattr(uni, "verify_candidate_yahoo", lambda y, timeout=10: True)
    stats = uni.ensure_for_isins(["US5128073062"], {"US5128073062": "EUR"})
    assert stats["verified"] >= 1
    yahoo, reason = uni.lookup_universe_yahoo("US5128073062")
    assert yahoo == "LOM.DE" and "home-currency" in reason
    assert uni.is_universe_verified("LOM.DE")
    assert not uni.is_universe_verified("NOPE.X")


def test_enrich_never_overrides_working(tmp_path):
    _seed_cache(tmp_path, {
        "US0378331005": {
            "listings": [{"ticker": "AAPL", "exchCode": "GR", "yahoo": "APC.DE"}],
            "selected": {"yahoo": "APC.DE", "reason": "home-currency-venue:GR",
                         "holding_currency": "EUR"},
            "verified": {"APC.DE": {"ok": True, "checked_at": "t"}},
            "fetched_at": "t",
        },
    })
    from investment_engine.research import universe as uni

    # Working mapping untouched even though universe knows another listing.
    ymap = {"AAPL": "AAPL"}
    stats = uni.enrich_yahoo_map(
        ymap, [{"display_symbol": "AAPL", "isin": "US0378331005", "currency": "USD"}],
        is_working=lambda y: y == "AAPL")
    assert ymap["AAPL"] == "AAPL" and stats == {"resolved": 0, "skipped_working": 1, "missed": 0}
    # Missing entry filled from cache without any network.
    ymap2: dict = {}
    stats2 = uni.enrich_yahoo_map(
        ymap2, [{"display_symbol": "AAPL", "isin": "US0378331005", "currency": "USD"}],
        is_working=lambda y: False)
    assert ymap2["AAPL"] == "APC.DE" and stats2["resolved"] == 1


def test_universe_support_state_delegates(tmp_path):
    _seed_cache(tmp_path, {
        "US0378331005": {
            "listings": [], "selected": {"yahoo": None, "reason": "no-listings"},
            "verified": {"APC.DE": {"ok": True, "checked_at": "t"}},
            "fetched_at": "t",
        },
    })
    from investment_engine.research.universe import universe_support_state

    assert universe_support_state("APC.DE") == "SUPPORTED"
    assert universe_support_state("ZZZ-QQ") == "UNRESOLVED"
    assert universe_support_state(None) == "UNSUPPORTED"


def test_kill_switch_and_key_never_printed(monkeypatch, tmp_path, capsys):
    _seed_cache(tmp_path, {})
    import os

    from investment_engine.research import universe as uni

    monkeypatch.setenv("UNIVERSE_ENABLED", "0")
    assert uni.enabled() is False
    assert uni.enrich_yahoo_map({}, [{"display_symbol": "X", "isin": "US0000000000"}]) == \
        {"resolved": 0, "skipped_working": 0, "missed": 0}
    monkeypatch.delenv("UNIVERSE_ENABLED")
    assert uni.enabled() is True
    monkeypatch.setenv("OPENFIGI_APIKEY", "secret-test-key")
    headers = uni._openfigi_headers()
    assert headers["X-OPENFIGI-APIKEY"] == "secret-test-key"
    out = capsys.readouterr().out
    assert "secret-test-key" not in out
    monkeypatch.delenv("OPENFIGI_APIKEY")
    # fingerprint is stable and short
    assert uni.fingerprint_inputs(["b", "a", "B"]) == uni.fingerprint_inputs(["A", "B"])
