"""Phase 5 corpus: schema, tiers, freshness, dedupe, adapters, builder.

Offline only (frozen fixtures, fixed clock). No live API.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


def _rec(title="AAPL beats", url="https://example.com/a", source="Reuters",
         hours_ago=5, category="general", tier=1, relevance=80):
    return {
        "title": title, "url": url, "source": source,
        "published_dt": NOW - timedelta(hours=hours_ago),
        "published_str": "2026-09-24",
        "category": category, "tier": tier, "relevance_score": relevance,
        "preview": "preview text",
    }


def test_schema_valid_and_forbids_extra():
    from investment_engine.schemas.research_item import ResearchItem

    item = ResearchItem(
        id="abc123", title="t", canonical_url="https://example.com/x",
        published_at_utc="2026-09-24T10:00:00+00:00", age_hours=2.0,
        source_tier=1, fetch_status="OK")
    assert item.decision_relevant()
    with pytest.raises(Exception):
        ResearchItem(id="x", title="t", canonical_url="https://example.com/x",
                     published_at_utc=NOW.isoformat(), fetch_status="OK",
                     totally_made_up_field=1)


def test_schema_rejects_bad_url_and_undated_ok():
    from pydantic import ValidationError

    from investment_engine.schemas.research_item import ResearchItem

    with pytest.raises(ValidationError):
        ResearchItem(id="x", title="t", canonical_url="not-a-url",
                     published_at_utc=NOW.isoformat(), fetch_status="OK")
    with pytest.raises(ValidationError):
        ResearchItem(id="x", title="t", canonical_url="https://example.com/x",
                     published_at_utc="", fetch_status="OK")
    undated = ResearchItem(id="x", title="t", canonical_url="https://example.com/x",
                           fetch_status="UNDATED")
    assert not undated.decision_relevant()
    assert undated.background_label() == "UNDATED"


def test_freshness_boundaries():
    from investment_engine.research.corpus import freshness as f

    assert f.age_hours(NOW - timedelta(hours=47.9), NOW) == pytest.approx(47.9)
    assert f.age_hours(None, NOW) is None
    assert f.age_hours("undated", NOW) is None
    assert f.classify(47.9, 1, "general") == "DECISION"
    assert f.classify(48.1, 1, "general") == "BACKGROUND"
    assert f.classify(5.0, 3, "reddit") == "BACKGROUND"
    assert f.classify(None, 0, "general") == "EXCLUDED-UNDATED"


def test_tiers_social_never_decision():
    from investment_engine.research.corpus import tiers as t

    assert t.tier_for_category("reddit", 0) == 3
    assert not t.may_enter_decision(0, "reddit")
    assert t.may_enter_decision(2, "general")
    assert not t.may_enter_decision(3, "general")


def test_dedupe_trackers_and_fuzzy_titles():
    from investment_engine.research.corpus import dedupe as d

    assert d.normalize_url("https://Example.COM/x?utm_source=a&b=1") == "https://example.com/x?b=1"
    assert d.item_id("https://example.com/x?utm_source=a") == d.item_id("https://example.com/x")
    assert d.titles_similar("Apple unveils new AI chip datacenters today",
                            "Apple unveils new AI chip datacenters today updated")
    assert not d.titles_similar("Apple earnings beat", "Tesla deliveries miss")
    items = [
        {"id": "1", "title": "Apple unveils new AI chip for datacenters",
         "canonical_url": "https://a.example/1"},
        {"id": "2", "title": "Apple unveils new AI chip for datacenters",
         "canonical_url": "https://b.example/2"},
    ]
    assert len(d.dedupe(items)) == 1


def test_adapter_never_raises_and_marks_undated():
    from investment_engine.research.corpus import adapters as ad

    out = ad.adapt_symbol_records("AAPL", [_rec(), {"garbage": True}, "not-a-dict",
                                           _rec(title="HTML scrape", hours_ago=0) | {"published_dt": None}],
                                  "AAPL", NOW)
    assert all(isinstance(i, dict) for i in out)
    undated = [i for i in out if i["title"] == "HTML scrape"]
    assert undated and undated[0]["fetch_status"] == "UNDATED"
    assert all(i["fetch_status"] != "OK" or i["published_at_utc"] for i in out)


def test_builder_counts_and_files(tmp_path):
    from investment_engine.research.corpus.builder import build_corpus, build_corpus_files

    news = {
        "AAPL": [_rec(hours_ago=5), _rec(title="Old story", url="https://example.com/old", hours_ago=100)],
        "REDDIT": [_rec(title="Hype thread", url="https://reddit.example/r", source="r/wallstreetbets",
                         category="reddit", tier=3)],
        "SLOVAK": [{"title": "HTML scrape", "url": "https://sme.example/x", "source": "SME",
                    "published_dt": None, "published_str": "undated", "undated": True, "category": "slovak"}],
    }
    corpus = build_corpus(news, NOW)
    assert corpus["counts"]["total"] >= 3
    assert corpus["counts"]["decision"] == 1
    assert corpus["counts"]["undated"] == 1
    assert corpus["decision_items"][0]["title"].startswith("AAPL beats")
    info = build_corpus_files(news, run_id="t5", output_dir=str(tmp_path), now=NOW)
    assert info["path_md"] and info["path_json"]
    assert "Decision-relevant" in open(info["path_md"], encoding="utf-8").read()


def test_builder_survives_garbage_buckets():
    from investment_engine.research.corpus.builder import build_corpus

    corpus = build_corpus({"X": None, "Y": "junk", "Z": [{"ok": False}]}, NOW)
    assert corpus["counts"]["total"] >= 0  # never raises; degraded by design


def test_decision_filter_default_is_48h():
    import inspect

    from investment_engine.research.news_engine import filter_decision_news

    assert inspect.signature(filter_decision_news).parameters["max_age_hours"].default == 48


def test_sector_templates_healthcare():
    from investment_engine.research.sector_templates import build_dynamic_search_queries

    assert any("FDA" in q for q in build_dynamic_search_queries("XYZ", "healthcare"))
    assert any("PDUFA" in q or "trial" in q.lower() for q in build_dynamic_search_queries("XYZ", "biotech"))
