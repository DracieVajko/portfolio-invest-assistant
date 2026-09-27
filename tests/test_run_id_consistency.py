"""Step 1 (run 524fffa0 findings): run_id consistency, metadata, FinViz group table.

Offline only: synthetic DataFrames/dicts, source-shape asserts, no network.
"""
from __future__ import annotations

import inspect
import pathlib
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace


def test_as_record_synthesizes_published_and_flags_undated():
    from investment_engine.research.news_sources import _as_record

    rss_shape = {"title": "T", "url": "https://e.example/x", "source": "CNBC",
                 "published_str": "2026-09-26 14:20"}
    out = _as_record(rss_shape)
    assert out["published"] == "2026-09-26 14:20"
    assert "undated" not in out
    # input dict is not mutated
    assert "published" not in rss_shape and "undated" not in rss_shape

    dt_shape = {"title": "T2", "published_dt": datetime(2026, 9, 26, 14, 20, tzinfo=timezone.utc)}
    assert _as_record(dt_shape)["published"] == "2026-09-26 14:20"

    dateless = _as_record({"title": "T3"})
    assert dateless.get("undated") is True
    assert dateless.get("published", "") == ""


def test_group_sector_table_parsing():
    pd = __import__("pandas")
    from investment_engine.research.technical_analysis import group_sector_breadth_from_table

    df = pd.DataFrame([
        # Change arrives via finvizfinance number_convert as a fraction:
        {"Name": "Technology", "Change": -0.0212, "Stocks": 500},
        # ... but raw "x%" strings must also parse:
        {"Name": "Energy", "Change": "0.83%", "Stocks": "120"},
        {"Name": "Utilities", "Change": None, "Stocks": None},
    ])
    out = group_sector_breadth_from_table(df, ["Technology", "Energy", "Utilities", "Missing"])
    assert out["Technology"]["avg_change"] == -2.12
    assert out["Technology"]["count"] == 500
    assert out["Energy"]["avg_change"] == 0.83
    assert out["Energy"]["count"] == 120
    assert out["Utilities"]["avg_change"] is None
    assert out["Utilities"]["count"] is None
    # per-ticker distribution is not in the aggregate table: declared None
    assert out["Technology"]["advancing"] is None
    assert out["Technology"]["declining"] is None
    assert "Missing" not in out
    assert group_sector_breadth_from_table(None) == {}
    assert group_sector_breadth_from_table(pd.DataFrame()) == {}


def test_normalize_ts_to_iso():
    from investment_engine.reporting.regime_report import _normalize_ts_to_iso

    now = time.time()
    iso = _normalize_ts_to_iso(now)
    assert iso and "T" in iso  # epoch seconds accepted
    assert _normalize_ts_to_iso(str(now)) == iso  # numeric string accepted
    assert _normalize_ts_to_iso(int(now * 1000))[:19] == iso[:19]  # epoch millis accepted
    assert _normalize_ts_to_iso("2026-09-26T16:08:11+00:00") == "2026-09-26T16:08:11+00:00"
    assert _normalize_ts_to_iso(now + 400 * 86400) == ""  # ~+400d future rejected
    assert _normalize_ts_to_iso(now - 72 * 3600) == ""  # >48h old rejected
    assert _normalize_ts_to_iso("") == ""
    assert _normalize_ts_to_iso(None) == ""
    assert _normalize_ts_to_iso("garbage") == ""


def test_run_metadata_timestamps_catalog_and_fx():
    from investment_engine.reporting.regime_report import AIContextReportBuilder

    now = time.time()
    fetched_at = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    recon = SimpleNamespace(reconciliation_status="PASS",
                            reconciliation_delta_eur=-0.86)
    t212 = {"account_summary": {"timestamp": "2026-09-26T18:03:37+02:00"}}
    fx = {"retrieved_at": now, "source": "live-fx", "stale": True}
    cat = {"fetched_at": fetched_at, "age_days": 5.0, "count": 16565}
    md = AIContextReportBuilder()._build_run_metadata(
        None, t212, fx, cat, recon, "abc12345")
    assert 'run_id: "abc12345"' in md
    # summary "timestamp" key is honored (was "unavailable" in 524fffa0)
    assert "2026-09-26T18:03:37+02:00" in md
    assert "broker_account_snapshot_timestamp: \"unavailable\"" not in md
    # real age_days renders as a number (5.0, not "unavailable")
    assert "instrument_catalog_age_days: 5.0" in md
    # epoch float becomes ISO, stale source is flagged
    assert "1758890836" not in md and "1790438611" not in md
    assert "live-fx (stale fallback)" in md
    # future FX timestamp degrades to unavailable instead of a false fact
    fx_bad = {"retrieved_at": now + 400 * 86400, "source": "live-fx-cache"}
    md_bad = AIContextReportBuilder()._build_run_metadata(
        None, t212, fx_bad, cat, recon, "abc12345")
    assert 'fx_timestamp: "unavailable"' in md_bad

    # fresh-today catalog (age 0.0) must NOT render as unavailable (0.0 falsy!)
    cat_fresh = {"fetched_at": fetched_at, "age_days": 0.0, "count": 16565}
    md_fresh = AIContextReportBuilder()._build_run_metadata(
        None, t212, fx, cat_fresh, recon, "abc12345")
    assert "instrument_catalog_age_days: 0.0" in md_fresh


def test_engine_reuses_wrapper_run_id_source_shape():
    src = pathlib.Path("investment_engine/main.py").read_text(encoding="utf-8")
    # no fresh uuid for the ai-context run_id (primary or fallback path)
    assert "run_id=str(_uuid.uuid4())[:8]" not in src
    assert "run_id = str(_uuid.uuid4())[:8]" not in src
    assert 'get("run_id", "") or "") or "UNKNOWN"' in src
    # news pipeline accepts the wrapper run_id explicitly ...
    import investment_engine.main as _main

    sig = inspect.signature(_main._fetch_all_news_parallel)
    assert "run_id" in sig.parameters
    # ... and the caller threads portfolio_context's run_id through it
    call_at = src.find("_fetch_all_news_parallel(\n        settings_dict, all_active_assets,")
    assert call_at != -1
    assert 'get("run_id", "")' in src[call_at:call_at + 300]
    # settings_dict fallback is kept only as a default inside the callee
    assert 'run_id = settings_dict.get("run_id", "")' not in src


def test_news_context_run_id_header():
    from investment_engine.research.news_sources import NewsContextBuilder

    out = NewsContextBuilder().build_news_context(
        news_by_symbol={"RSS_FALLBACK": [
            {"title": "T", "url": "https://e.example/x", "source": "CNBC",
             "published_str": "2026-09-26 14:20"}]},
        run_id="abc12345")
    assert "Run ID: `abc12345`" in out
    # RSS shape (published_str, no "published") now renders its date
    assert "Date: 2026-09-26 14:20" in out


def test_atomic_write_preserves_lf_for_manifest_hashes(tmp_path):
    """Finding 16: text-mode CRLF translation made every manifest hash/bytes
    mismatch the file on disk (deterministic, all 5 files, every run)."""
    import hashlib

    import portfolio_ai_assistant as _wrapper

    dest = tmp_path / "report.md"
    text = "# Title\n\nline one\nline two\n"
    _wrapper._atomic_write_text(dest, text)
    raw = dest.read_bytes()
    assert b"\r" not in raw
    assert hashlib.sha256(raw).hexdigest() == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_wrapper_manifest_run_id_fields(tmp_path):
    import json

    import portfolio_ai_assistant as _wrapper

    out = tmp_path / "o"
    arch = tmp_path / "a"
    result = {
        "brief_markdown": "b", "snapshot_markdown": "s",
        "context_file": str(tmp_path / "ai_context_abc12345.md"),
        "market_regime": {"finviz_data": {"sector_breadth": {"Technology": {}}}},
    }
    published = _wrapper.write_reports(result, out, arch, "abc12345")
    manifest = json.loads((out / "current" / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["context_run_id"] == "abc12345"
    assert manifest["run_id_consistent"] is True
    assert manifest["finviz_sectors"] == 1
    assert published["run_manifest.json"] == out / "current" / "run_manifest.json"

    bad = dict(result, context_file=str(tmp_path / "ai_context_7e520fb5.md"))
    _wrapper.write_reports(bad, out, arch, "abc12345")
    manifest2 = json.loads((out / "current" / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest2["context_run_id"] == "7e520fb5"
    assert manifest2["run_id_consistent"] is False
