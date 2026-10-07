"""Phase 6 skills: registry, contracts, four skills, wiring.

Offline only. Provider doubles are local fakes (no network).
"""

from __future__ import annotations


def _item(id="i1", tier=1, age=5.0, ticker="AAPL", category="general",
          title="AAPL beats", publisher="Reuters", url=None):
    return {
        "id": id, "title": title, "canonical_url": url or f"https://example.com/{id}",
        "publisher": publisher, "published_at_utc": "2026-09-24T10:00:00+00:00",
        "age_hours": age, "source_tier": tier, "source_category": category,
        "ticker_tags": [ticker], "fetch_status": "OK",
    }


class _FakeProvider:
    def __init__(self, payload):
        self.payload = payload
        self.name = "Fake"

    def generate(self, prompt, *, stage, context=None):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def test_registry_versions_prompts_manifests():
    from investment_engine.skills import registry as reg

    assert set(reg.SKILL_VERSIONS) == {"portfolio_risk_manager", "news_event_analyst",
                                      "equity_research_analyst", "investment_committee"}
    for skill, version in reg.SKILL_VERSIONS.items():
        assert version == "v1"
        prompt = reg.load_prompt(skill)
        assert "JSON" in prompt and len(prompt) > 100
        manifest = reg.load_manifest(skill)
        assert set(manifest) >= {"name", "version", "purpose", "inputs", "evidence",
                                 "allowed", "forbidden", "model_routing", "fallback"}


def test_run_skill_never_raises():
    from investment_engine.skills import registry as reg

    out = reg.run_skill("no_such_skill", {}, None)
    assert out["status"] == "INSUFFICIENT_EVIDENCE"
    out = reg.run_skill("portfolio_risk_manager", None, None)
    assert out["status"] == "OK"  # broker-only evidence path needs no provider


def test_contracts_evidence_matrix():
    from investment_engine.skills import contracts as c

    assert c.require_evidence([_item()], 1)[0]
    assert not c.require_evidence([], 1)[0]
    assert not c.require_evidence([_item(age=50.0)], 1)[0]
    assert not c.require_evidence([_item(tier=3, category="reddit")], 1)[0]
    bad_url = _item()
    bad_url["canonical_url"] = ""
    assert not c.require_evidence([bad_url], 1)[0]
    undated = _item()
    undated["fetch_status"] = "UNDATED"
    assert not c.require_evidence([undated], 1)[0]
    assert c.confidence(9.9, ["a"])["value"] == 1.0
    assert c.confidence(-1, [])["value"] == 0.0


def test_contracts_citation_validation():
    from investment_engine.skills import contracts as c

    out = {"data": {"events": [{"item_ids": ["i1", "ghost"]}]}}
    ok, why = c.validate_citations(out, {"i1"})
    assert not ok and "ghost" in why
    assert c.validate_citations(out, {"i1", "ghost"})[0]


def test_risk_postures():
    from investment_engine.skills.portfolio_risk_manager import skill as risk

    rows = [{"display_symbol": "BIG", "weight": 25.0, "signal": "HOLD"}]
    fail = risk.run({"portfolio_rows": rows, "reconciliation": {"status": "FAIL"},
                     "cash": {"free": 100.0}})
    assert fail["data"]["posture"] == "DEFENSIVE"
    assert "withhold" in fail["data"]["cash_action"]
    zero = risk.run({"portfolio_rows": [], "reconciliation": {"status": "PASS"},
                     "cash": {"free": 0.0}})
    assert zero["data"]["posture"] == "CAUTIOUS"
    assert "zero" in zero["data"]["cash_action"]
    assert fail["data"]["breaches"][0]["ticker"] == "BIG"
    ok = risk.run({"portfolio_rows": [{"display_symbol": "A", "weight": 5.0}],
                   "reconciliation": {"status": "PASS"}, "cash": {"free": 50.0}})
    assert ok["data"]["posture"] == "NORMAL" and ok["data"]["breaches"] == []
    assert "BUY" not in ok["data"]["cash_action"] or "no BUY" in ok["data"]["cash_action"]


def test_news_evidence_and_states():
    from investment_engine.skills.news_event_analyst import skill as news

    assert news.run({"decision_items": []})["status"] == "INSUFFICIENT_EVIDENCE"
    one = news.run({"decision_items": [_item(publisher="Reuters")]})
    assert one["status"] == "OK"
    assert one["data"]["events"][0]["state"] == "UNVERIFIED"
    two = news.run({"decision_items": [_item(id="i1", publisher="Reuters"),
                                       _item(id="i2", publisher="AP",
                                             title="AAPL beats big", url="https://example.com/i2")]})
    assert two["data"]["events"][0]["state"] == "CONFIRMED"
    # LLM JSON with unknown IDs is rejected -> deterministic fallback stands
    bad_llm = _FakeProvider('{"events": [{"ticker": "AAPL", "type": "EARNINGS", '
                            '"item_ids": ["nope"], "severity": "high", "state": "CONFIRMED"}]}')
    out = news.run({"decision_items": [_item()]}, provider=bad_llm)
    assert out["data"]["enriched"] is False
    good_llm = _FakeProvider('{"events": [{"ticker": "AAPL", "type": "EARNINGS", '
                             '"item_ids": ["i1"], "severity": "high", "state": "UNVERIFIED"}]}')
    out2 = news.run({"decision_items": [_item()]}, provider=good_llm)
    assert out2["data"]["enriched"] is True
    dead = _FakeProvider(RuntimeError("down"))
    out3 = news.run({"decision_items": [_item()]}, provider=dead)
    assert out3["status"] == "OK" and out3["data"]["enriched"] is False


def test_equity_mapping_gate_and_vocab():
    from investment_engine.skills.equity_research_analyst import skill as eq

    assert eq.run({"portfolio_rows": []})["status"] == "INSUFFICIENT_EVIDENCE"
    rows = [{"display_symbol": "AAPL", "signal": "BUY", "pnl_pct": -12.0,
             "external_mapping_status": "VERIFIED"},
            {"display_symbol": "VWSB", "signal": "HOLD", "pnl_pct": 3.0,
             "external_mapping_status": "UNRESOLVED"}]
    out = eq.run({"portfolio_rows": rows,
                  "technicals": {"AAPL": {"RSI_14": 75, "Support": 200},
                                 "VWSB": {"RSI_14": 10}},
                  "earnings_status": {}, "corpus_items": []})
    notes = {n["ticker"]: n for n in out["data"]["notes"]}
    assert notes["AAPL"]["thesis"] == "ACCUMULATE"
    assert "technicals" in notes["AAPL"]
    assert "technicals" not in notes["VWSB"]
    assert any("VWSB" in u for u in out["unverified"])
    assert all(n["thesis"] in ("HOLD", "ACCUMULATE", "TRIM") for n in notes.values())
    text = eq.render(out)
    assert "AAPL" in text and "VWSB" in text


def test_committee_reconciliation_and_dissent():
    from investment_engine.skills.investment_committee import skill as com

    rows = [{"display_symbol": "AAPL", "signal": "BUY"},
            {"display_symbol": "MSFT", "signal": "HOLD"}]
    canon = {"AAPL": {"signal": "BUY"}, "MSFT": {"signal": "HOLD"}}
    base = {"risk_output": {"data": {"posture": "NORMAL"}, "unverified": []},
            "news_output": {"data": {"events": [{"ticker": "GHOST"}]}, "unverified": []},
            "equity_output": {"data": {"notes": []}, "unverified": []},
            "canonical_map": canon, "portfolio_rows": rows}
    ok = com.run(dict(base, reconciliation={"status": "PASS"}))
    assert {d["ticker"]: d["action"] for d in ok["data"]["decisions"]} == {"AAPL": "BUY", "MSFT": "HOLD"}
    assert any("GHOST" in n for n in ok["data"]["dissent_notes"])
    failed = com.run(dict(base, reconciliation={"status": "FAIL"}))
    assert {d["ticker"]: d["action"] for d in failed["data"]["decisions"]}["AAPL"] == "WATCH"
    assert com.run({"portfolio_rows": []})["status"] == "INSUFFICIENT_EVIDENCE"


def test_skill_modules_never_read_prompt_files_directly():
    import pathlib

    for skill in ("portfolio_risk_manager", "news_event_analyst",
                  "equity_research_analyst", "investment_committee"):
        src = (pathlib.Path("investment_engine/skills") / skill / "skill.py").read_text(encoding="utf-8")
        assert "read_text" not in src and "open(" not in src


def test_engine_wires_all_four_skills():
    import pathlib

    src = pathlib.Path("investment_engine/main.py").read_text(encoding="utf-8")
    for skill in ("portfolio_risk_manager", "news_event_analyst",
                  "equity_research_analyst", "investment_committee"):
        assert f'"{skill}"' in src
    assert '"skill_outputs"' in src and '"skill_versions"' in src
