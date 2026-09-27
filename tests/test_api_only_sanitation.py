"""API-only sanitation tests: provider chain, corpus, recon logs, failures.

Offline only. No live API. No local-endpoint contact (probes assertably skipped).
"""

from __future__ import annotations

import logging


def _settings(**over):
    from investment_engine.config.settings import EngineSettings

    base = EngineSettings()
    for key, val in over.items():
        setattr(base, key, val)
    return base


def test_api_only_chain_is_gemini_mistral_only():
    from investment_engine.providers.factory import ProviderFactory

    s = _settings(api_only=True, gemini_api_key="gk", mistral_api_key="mk",
                  lmstudio_enabled=True)
    chain = ProviderFactory.create(s)
    names = [p.name for p in getattr(chain, "providers", [chain])]
    assert names == ["Gemini", "Mistral"]
    assert "LM Studio" not in names and "Ollama" not in names


def test_api_only_chain_without_keys_degrades_silently():
    from investment_engine.providers.factory import ProviderFactory
    from investment_engine.providers.fallback import ChainedFallbackProvider

    # Blank keys explicitly: the developer machine's api.env may inject real
    # keys via dotenv, so "no keys" must be forced (and no network is touched:
    # _NoKey.generate never leaves the process).
    s = _settings(api_only=True, lmstudio_enabled=True,
                  gemini_api_key="", mistral_api_key="")
    chain = ProviderFactory.create(s)
    assert isinstance(chain, ChainedFallbackProvider)
    assert [p.name for p in chain.providers] == ["NoCloudKey"]
    try:
        chain.generate("hi", stage="decision")
        raised = False
    except Exception:
        raised = True
    assert raised  # -> main.py turns this into the deterministic fallback


def test_api_only_never_probes_locals(monkeypatch):
    import investment_engine.main as engine
    import requests

    calls = []

    def _boom(*args, **kwargs):
        calls.append(args)
        raise AssertionError("local endpoint probed in API-only mode")

    monkeypatch.setattr(requests, "get", _boom)
    engine._FALLBACK_EVENTS.clear()
    engine._note_unreachable_local_providers(_settings(api_only=True))
    assert calls == []
    assert any("API-only mode" in e for e in engine._FALLBACK_EVENTS)
    engine._FALLBACK_EVENTS.clear()


def test_execution_mode_lines_only_when_api_only():
    from investment_engine.main import _execution_mode_lines

    assert _execution_mode_lines(_settings(api_only=True)) == [
        "AI execution mode: API_ONLY",
        "Disabled providers: LM Studio, llama.cpp, Ollama",
    ]
    assert _execution_mode_lines(_settings()) == []


def test_manifest_lists_disabled_providers(tmp_path):
    import json

    import portfolio_ai_assistant as entry

    result = {"brief_markdown": "", "snapshot_markdown": "",
              "reconciliation": {"status": "UNKNOWN"}, "failed_tickers": [],
              "monitoring_items": [], "decision_news": [], "brief_metadata": {},
              "api_execution_mode": "API_ONLY",
              "disabled_providers": ["LM Studio", "llama.cpp", "Ollama"]}
    entry.write_reports(result, tmp_path / "o", tmp_path / "a", "api1")
    manifest = json.loads((tmp_path / "o" / "current" / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["ai_execution_mode"] == "API_ONLY"
    assert manifest["disabled_providers"] == ["LM Studio", "llama.cpp", "Ollama"]


def test_brief_shows_mode_and_research_note():
    from investment_engine.reporting.documents import build_intelligence_brief, render_brief

    mode = ["AI execution mode: API_ONLY", "Disabled providers: LM Studio, llama.cpp, Ollama"]
    note = "Research coverage insufficient: 1 validated decision-quality items in the last 48 hours."
    md = render_brief(
        generated_at="2026-09-25 09:00 CEST", regime_result=None,
        recon={"status": "UNKNOWN", "total_equity": 0}, rows=[], monitoring_items=[],
        earnings_7d={}, decision_news=[], ideas=[], cash_line="n/a",
        research_note=note, execution_mode_lines=mode)
    assert "AI execution mode: API_ONLY" in md
    assert "Disabled providers: LM Studio, llama.cpp, Ollama" in md
    assert note in md
    intel = build_intelligence_brief({"portfolio_rows": [], "reconciliation": {"status": "UNKNOWN"},
                                      "t212_data": {}, "decision_news": [], "earnings_7d": {},
                                      "ideas": [], "research_note": note,
                                      "execution_mode_lines": mode})
    assert note in intel and "AI execution mode: API_ONLY" in intel


def test_corpus_run_id_never_blank_and_rejected_render(tmp_path):
    import pytest

    from investment_engine.research.corpus.builder import build_corpus_files, render_rejected_markdown

    with pytest.raises(ValueError):
        build_corpus_files({"A": []}, run_id="  ", output_dir=str(tmp_path))
    assert "None" in render_rejected_markdown([], "r1")
    md = render_rejected_markdown([{"title": "t", "canonical_url": "https://e.example/x",
                                    "publisher": "p", "fetch_status": "UNDATED",
                                    "failure_reason_if_any": ""}], "r1")
    assert "no validated publication timestamp" in md


def test_corpus_decision_items_fully_validated_and_aggregator_labelled():
    from investment_engine.research.corpus.builder import build_corpus
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc)
    good = {"title": "t", "url": "https://news.google.com/rss/articles/x",
            "source": "Reuters", "published_dt": now - timedelta(hours=3),
            "category": "general", "tier": 1, "relevance_score": 90}
    no_url = {"title": "no link", "source": "X", "published_dt": now,
              "category": "general"}
    corpus = build_corpus({"AAPL": [good, no_url]}, now)
    for item in corpus["decision_items"]:
        assert item["title"] and str(item["canonical_url"]).startswith("http")
        assert item["publisher"] and item["published_at_utc"]
        assert isinstance(item["age_hours"], float) and item["source_tier"] in (0, 1, 2)
        assert item["source_category"]
    agg = [i for i in corpus["decision_items"] if i["title"] == "t"]
    assert agg and agg[0]["url_kind"] == "aggregator_url"
    assert corpus["counts"]["rejected"] >= 1  # the URL-less record


def test_guard_reads_no_legacy_r1_fields():
    import pathlib

    src = pathlib.Path("investment_engine/main.py").read_text(encoding="utf-8")
    assert "T212 Reconciliation:" not in src
    guard_at = src.find("Canonical broker-first snapshot FIRST")
    assert guard_at != -1
    guard_block = src[guard_at:guard_at + 4000]
    assert 'summary.get("reconciliation_status"' not in guard_block
    assert 'summary.get("reconciliation_difference"' not in guard_block


def test_legacy_disagreement_message_exact():
    import pathlib

    src = pathlib.Path("trading212/portfolio.py").read_text(encoding="utf-8")
    # The legacy-module check must never impersonate the canonical verdict
    # (run 524fffa0 logged "canonical FAIL" while canonical PASSed).
    assert "legacy-module check, ignored for decisions" in src
    assert "Authoritative verdict comes from the " in src
    assert "Canonical broker-first result:" not in src


def test_slovak_hard_failure_disables_site_once(monkeypatch, caplog):
    import requests
    from types import SimpleNamespace

    from investment_engine.research.news_engine import StrictNewsFetcher

    fetcher = StrictNewsFetcher(enable_file_cache=False)

    def _raise_403(*args, **kwargs):
        err = requests.HTTPError("403")
        err.response = SimpleNamespace(status_code=403)
        raise err

    monkeypatch.setattr(fetcher._session, "get", _raise_403)
    with caplog.at_level(logging.WARNING, logger="investment_engine.research.news_engine"):
        assert fetcher.fetch_slovak_news() == []
    assert fetcher._slovak_disabled == {"Aktuality", "Pravda", "TERAZ", "Google News"}
    warned = [r for r in caplog.records if "disabled for the rest of the run" in r.getMessage()]
    assert len(warned) == 4  # once per site, then silence
    caplog.clear()
    calls = []
    monkeypatch.setattr(fetcher._session, "get", lambda *a, **k: calls.append(1))
    with caplog.at_level(logging.WARNING, logger="investment_engine.research.news_engine"):
        assert fetcher.fetch_slovak_news() == []
    assert calls == []  # disabled sites are never re-requested
    assert not [r for r in caplog.records if "disabled for the rest of the run" in r.getMessage()]


def test_finviz_kill_switch_logs_once(caplog):
    from investment_engine.research import technical_analysis as ta

    saved = ta._FINVIZ_DISABLED
    ta._FINVIZ_DISABLED = False
    try:
        with caplog.at_level(logging.INFO, logger="investment_engine.research.technical_analysis"):
            ta.disable_finviz("boom")
            ta.disable_finviz("boom")
        hits = [r for r in caplog.records if "remainder of the run" in r.getMessage()]
        assert len(hits) == 1
        assert not ta.finviz_enabled()
    finally:
        ta._FINVIZ_DISABLED = saved


def test_no_delisted_claims_repo_wide():
    import pathlib
    import re

    offenders = []
    for path in pathlib.Path("investment_engine").rglob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"delist", line, re.IGNORECASE) and \
               "previously" not in line.lower() and "never reported as delisted" not in line.lower():
                offenders.append(f"{path}:{i}")
    assert not offenders, offenders
