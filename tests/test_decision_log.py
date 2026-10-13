"""Decision-log writer: validation, append-only behavior, index. Offline."""
from __future__ import annotations


def _good():
    return {"asset": {"ticker": "AAPL", "isin": "US0378331005"},
            "mode": "M1", "verdict": "HOLD", "confidence": "medium"}


def test_validate_matrix():
    from scripts.decision_log import validate_record

    assert validate_record(_good()) == []
    assert validate_record({}) != []
    assert validate_record("nope") != []
    bad = dict(_good(), verdict="MOON")
    assert any("verdict" in p for p in validate_record(bad))
    bad = dict(_good(), mode="M9")
    assert any("mode" in p for p in validate_record(bad))
    bad = {"mode": "M1", "asset": {}, "verdict": "HOLD", "confidence": "low"}
    assert any("ticker" in p for p in validate_record(bad))


def test_build_record_id_shape():
    from scripts.decision_log import build_record

    rec = build_record(_good(), created_utc="20261013T120000Z")
    assert rec["id"] == "20261013T120000Z_AAPL_M1"
    assert rec["user_action_confirmed"] is None
    assert rec["created_utc"] != ""


def test_append_never_overwrites(tmp_path):
    from scripts.decision_log import append_record, build_record, list_records

    first = append_record(build_record(_good(), created_utc="20261013T120000Z"),
                          log_dir=tmp_path)
    second = append_record(build_record(_good(), created_utc="20261013T120000Z"),
                           log_dir=tmp_path)
    assert first != second and first.is_file() and second.is_file()
    rows = list_records(log_dir=tmp_path)
    assert len(rows) == 2
    assert all(r["ticker"] == "AAPL" and r["verdict"] == "HOLD" for r in rows)
    assert list_records("aapl", log_dir=tmp_path) == rows
    assert list_records("MSFT", log_dir=tmp_path) == []


def test_cli_new_and_list(tmp_path, monkeypatch):
    import json

    from scripts import decision_log as dl

    monkeypatch.setattr(dl, "LOG_DIR", tmp_path)
    assert dl.main(["new", "--ticker", "NVDA", "--mode", "M1",
                    "--verdict", "WATCH", "--confidence", "low"]) == 0
    assert dl.main(["new", "--ticker", "NVDA", "--mode", "M9",
                    "--verdict", "WATCH"]) == 2
    assert dl.main(["list", "--ticker", "NVDA"]) == 0
    rows = dl.list_records()
    assert len(rows) == 1 and rows[0]["mode"] == "M1"
    payload = json.dumps(_good())
    assert dl.main(["new", "--data", payload]) == 0
