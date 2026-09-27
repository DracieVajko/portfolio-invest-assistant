"""Phase 4 reports: topology, attribution, brief rules, stance unity.

Offline only. No live API.
"""

from __future__ import annotations

import json
import re

from investment_engine.reporting import report_structure
from investment_engine.reporting.documents import build_intelligence_brief, portfolio_stance


def _result(**over):
    base = {
        "portfolio_rows": [],
        "monitoring_items": [],
        "regime_result": None,
        "decision_news": [],
        "earnings_7d": {},
        "ideas": [],
        "portfolio_names": {},
        "failed_tickers": [],
        "t212_data": {"account_summary": {"total_equity": 10000.0},
                      "cash": {"free": 500.0, "pie_cash": 0.0, "blocked": 0.0}},
        "reconciliation": {"status": "PASS"},
        "account_performance": {"net_pnl_after_costs_eur": 100.0, "return_pct": 1.0},
        "brief_metadata": {},
        "brief_markdown": "",
        "snapshot_markdown": "",
    }
    base.update(over)
    return base


def test_topology_current_and_archive(tmp_path):
    import portfolio_ai_assistant as entry

    result = _result(intelligence_brief_markdown="# intel")
    published = entry.write_reports(result, tmp_path / "o", tmp_path / "a", "run9")
    for name in ("portfolio_decision_brief.md", "t212_portfolio_snapshot.md",
                 "portfolio_analysis.json", "run_manifest.json",
                 "portfolio_intelligence_brief.md"):
        assert (tmp_path / "o" / "current" / name).is_file(), name
        assert (tmp_path / "a" / "run9" / name).is_file(), name
    manifest = json.loads((tmp_path / "o" / "current" / "run_manifest.json").read_text(encoding="utf-8"))
    assert set(manifest) >= {"run_id", "generated_at", "recon_status", "files",
                             "counts", "provider", "stance", "providers_per_stage",
                             "indicator_engine", "versions"}
    assert published["portfolio_intelligence_brief.md"].name == "portfolio_intelligence_brief.md"


def test_brief_rules_fail_once_short_no_orders():
    result = _result(
        reconciliation={"status": "FAIL"},
        monitoring_items=[{"display": "AAPL", "presentation": "HOLD — steady"},
                          {"display": "MSFT", "presentation": "WATCH — earnings soon"}],
        decision_news=[{"title": "Markets steady", "source": "Wire"}],
        earnings_7d={"in_window": [{"display": "AAPL", "company": "Apple", "status": "confirmed"}]},
        ideas=[{"ticker": "NVDA", "reason": "datacenter demand"}],
    )
    brief = build_intelligence_brief(result)
    lines = brief.splitlines()
    assert len(lines) <= 60
    assert sum("Account data quality" in line for line in lines) == 1
    assert sum("Reconciliation: **FAIL**" in line for line in lines) == 1
    for pat in (r"(?i)\bbuy\s+\d", r"(?i)\bshares?\s+@", r"(?i)\blimit\s+€",
                r"(?i)\bstop[-\s]?loss\s+€", r"(?i)\bqty\s*="):
        assert not re.search(pat, brief), pat


def test_brief_rules_pass_no_warning():
    brief = build_intelligence_brief(_result())
    assert "Account data quality" not in brief
    assert len(brief.splitlines()) <= 60


def test_stance_single_source():
    assert report_structure._get_portfolio_stance([], "FAIL") == "DEGRADED"
    assert report_structure._get_portfolio_stance([], "UNKNOWN") == "DEGRADED"
    assert report_structure._get_portfolio_stance([], "PASS") == portfolio_stance([], "PASS") == "HOLD"
    rows = [{"signal": "BUY"}, {"signal": "HOLD"}]
    assert report_structure._get_portfolio_stance(rows, "PASS") == portfolio_stance(rows, "PASS") == "BUY"


def test_generate_human_brief_delegates_and_keeps_signature():
    import inspect

    params = set(inspect.signature(report_structure.generate_human_brief).parameters)
    assert params == {"result", "portfolio_rows", "monitoring_items", "regime_result",
                      "t212_data", "decision_news", "earnings_7d", "ideas",
                      "portfolio_names", "reconciliation"}
    result = _result()
    assert report_structure.generate_human_brief(
        result=result, portfolio_rows=[], monitoring_items=[], regime_result=None,
        t212_data=result["t212_data"], decision_news=[], earnings_7d={},
        ideas=[], portfolio_names={}, reconciliation={"status": "PASS"},
    ) == build_intelligence_brief(result)


def test_chain_records_winner_without_network():
    from investment_engine.providers.base import BaseLLMProvider
    from investment_engine.providers.fallback import ChainedFallbackProvider

    class Dead(BaseLLMProvider):
        name = "Dead"
        model = "dead-1"
        def generate(self, prompt, *, stage, context=None):
            raise RuntimeError("down")

    class Alive(BaseLLMProvider):
        name = "Alive"
        model = "alive-2"
        def generate(self, prompt, *, stage, context=None):
            return "fine content"

    chain = ChainedFallbackProvider([Dead(), Alive()])
    assert chain.generate("p", stage="decision") == "fine content"
    assert chain.last_attribution == {"provider": "Alive", "model": "alive-2", "depth": 1}


def test_attribution_helpers_label_deterministic():
    from investment_engine.providers import attribution as attr

    rec = attr.note_deterministic(attr.new_stage_record("summary", "writer-x"), "balanced")
    assert rec["provider"] == "deterministic" and rec["fallback_depth"] == 99
    assert "summary:deterministic/" in attr.summarize_winners({"summary": rec})


def test_lmstudio_disabled_by_default():
    from investment_engine.config.settings import EngineSettings
    from investment_engine.providers.factory import ProviderFactory

    assert EngineSettings().lmstudio_enabled is False
    chain = ProviderFactory._build_chain(EngineSettings())
    assert "LM Studio" not in [p.name for p in chain]


def test_manifest_provider_is_winners_summary(tmp_path):
    import portfolio_ai_assistant as entry

    result = _result(providers_per_stage={
        "decision": {"provider": "Gemini", "model_served": "gemini-2.5-flash",
                     "fallback_depth": 3, "error_class": ""},
        "summary": {"provider": "deterministic", "model_served": "deterministic(balanced)",
                    "fallback_depth": 99, "error_class": ""},
    }, brief_metadata={"stance": "- Portfolio stance: **HOLD**"})
    entry.write_reports(result, tmp_path / "o", tmp_path / "a", "w1")
    manifest = json.loads((tmp_path / "o" / "current" / "run_manifest.json").read_text(encoding="utf-8"))
    assert "decision:Gemini/" in manifest["provider"]
    assert manifest["stance"] == "- Portfolio stance: **HOLD**"
    assert manifest["providers_per_stage"]["summary"]["provider"] == "deterministic"
