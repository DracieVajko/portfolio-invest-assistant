"""T01 identity resolution: no silent substitution. Offline fixture."""
from __future__ import annotations

import json
import pathlib

from investment_engine.portfolio.instruments import resolve_asset

FIX = pathlib.Path(__file__).parent / "fixtures" / "t01_master.json"


def _master():
    return json.loads(FIX.read_text(encoding="utf-8"))


def test_alphabet_returns_two_share_classes_unresolved():
    out = resolve_asset("Alphabet", _master())
    assert out["resolved"] is False
    isins = {c["isin"] for c in out["candidates"]}
    assert isins == {"US02079K1079", "US02079K3059"}
    assert any("share classes" in n or "ambiguous" in n for n in out["notes"])


def test_apple_common_only_with_leverage_filter():
    out = resolve_asset("Apple", _master(), exclude_leveraged=True)
    assert out["resolved"] is True
    assert out["selected"]["isin"] == "US0378331005"
    assert out["selected"]["leveraged"] is False


def test_apple_etp_flagged_without_filter():
    out = resolve_asset("Apple", _master())
    assert out["resolved"] is False
    etp = [c for c in out["candidates"] if c["isin"] == "XS000APPLE03"]
    assert len(etp) == 1 and etp[0]["leveraged"] is True


def test_broker_ticker_maps_exact():
    out = resolve_asset("AAPL_US_EQ", _master())
    assert out["resolved"] is True
    assert out["selected"]["isin"] == "US0378331005"
    assert out["selected"]["tickers"] == {"NASDAQ": "AAPL"}


def test_unknown_returns_empty_never_substitute():
    out = resolve_asset("ZzzTop Nonexistent", _master())
    assert out == {"query": "ZzzTop Nonexistent", "resolved": False,
                   "selected": None, "candidates": [], "notes": []}
