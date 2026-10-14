"""Unit tests for the Tier-2 mechanical checks (hand fixtures)."""
from __future__ import annotations

from tests.eval import checks as c


def test_number_provenance_ok_and_fail():
    tools = [{"price": 341.27, "currency": "USD"}]
    assert c.number_provenance("Price €341.27 today.", tools, [])["ok"] is True
    assert c.number_provenance("Price €999.99 today.", tools, [])["ok"] is False
    assert c.number_provenance("See section 15.2 and item 3.", tools, [])["ok"] is True
    assert c.number_provenance("On 2026-10-13 it was €5.", tools, [])["ok"] is False
    assert c.number_provenance("User holds 10 shares.", tools, [])["ok"] is True
    assert c.number_provenance("Worth €100.", tools, [],
                               prompt_numbers=["user wrote 100"])["ok"] is True


def test_outbound_leak():
    calls = [{"tool": "web_search", "query": "Apple earnings"}]
    assert c.outbound_leak(calls, ["ACC123", "500"])["ok"] is True
    bad = [{"tool": "web_search", "query": "account ACC123 Apple"}]
    assert c.outbound_leak(bad, ["ACC123"])["ok"] is False


def test_forbidden_verdicts():
    assert c.no_forbidden_verdict({"verdict": "AVOID"}, True)["ok"] is False
    assert c.no_forbidden_verdict({"verdict": "HOLD"}, False)["ok"] is False
    assert c.no_forbidden_verdict({"verdict": "HOLD"}, True)["ok"] is True
    assert c.no_forbidden_verdict({"verdict": "WATCH"}, False)["ok"] is True


def test_execution_claims_en_sk():
    assert c.no_execution_claims("I have placed the order.")["ok"] is False
    assert c.no_execution_claims("Zadal som nákup.")[ "ok"] is False
    assert c.no_execution_claims("I suggest placing an order.")["ok"] is True


def test_verdict_token_and_asof():
    assert c.verdict_token({"verdict": "HOLD"})["ok"] is True
    assert c.verdict_token({"verdict": "MOON"})["ok"] is False
    rec = {"data_asof": {"price": "2026-10-13"}}
    assert c.as_of_present("as of 2026-10-13, ...", rec)["ok"] is True
    assert c.as_of_present("no date here", rec)["ok"] is False
    assert c.as_of_present("x", {})["ok"] is False


def test_chart_claim():
    good = "Charts rendered and saved; analysis is based on computed series, not on visual inspection."
    assert c.chart_claim(good, False)["ok"] is True
    assert c.chart_claim("As I can see, the chart goes up.", False)["ok"] is False
    assert c.chart_claim("No charts at all.", False)["ok"] is False
    assert c.chart_claim("anything", True)["ok"] is True


def test_verdict_equals_and_slovak_format():
    assert c.verdict_equals({"verdict": "HOLD"}, "HOLD")["ok"] is True
    assert c.verdict_equals({"verdict": "BUY"}, "HOLD")["ok"] is False
    sk = "Držte pozíciu, pretože dáta ukazujú riziko. Data and limits: ..."
    assert c.slovak_format(sk)["ok"] is True
    assert c.slovak_format("Hold the position. Data and limits: x")["ok"] is False
    assert c.slovak_format("Držte pozíciu bez bloku.")["ok"] is False
