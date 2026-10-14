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


def test_append_same_id_raises_and_correction_supersedes(tmp_path):
    """T10: same id raises; corrections are new records with supersedes."""
    import pytest

    from scripts.decision_log import (append_record, build_record,
                                      lookup_prior)

    first = append_record(build_record(_good(), created_utc="20261013T120000Z"),
                          log_dir=tmp_path)
    assert first.is_file()
    with pytest.raises(FileExistsError):
        append_record(build_record(_good(), created_utc="20261013T120000Z"),
                      log_dir=tmp_path)
    fix = dict(_good(), supersedes="20261013T120000Z_AAPL_M1",
               verdict="TRIM", notes="take-profit")
    second = append_record(build_record(fix, created_utc="20261013T130000Z"),
                           log_dir=tmp_path)
    assert second.is_file()
    prior = lookup_prior("AAPL", log_dir=tmp_path)
    assert prior is not None and prior["verdict"] == "TRIM"
    assert prior["id"] == "20261013T130000Z_AAPL_M1"
    assert lookup_prior("US0378331005", log_dir=tmp_path)["id"] == prior["id"]
    assert lookup_prior("MSFT", log_dir=tmp_path) is None


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


def test_anti_churn_matrix():
    from scripts.decision_log import anti_churn

    prior = {"id": "x", "verdict": "HOLD"}
    assert anti_churn(None) == "no_prior"
    assert anti_churn(prior, triggers_fired=[], new_filing=False,
                      error=False, regime_changed=True) == "repeat_prior"
    assert anti_churn(prior, triggers_fired=["rsi>80"]) == "re-evaluate"
    assert anti_churn(prior, new_filing=True) == "re-evaluate"
    assert anti_churn(prior, error=True) == "re-evaluate"


def test_secrets_in_notes_rejected_and_action_stays_null():
    from scripts.decision_log import append_record, build_record, validate_record

    evil = dict(_good(), notes="contact broker, api_key=AKIAIOSFODNN7EXAMPLE")
    assert any("secret" in p for p in validate_record(evil))
    evil2 = dict(_good(), notes=["-----BEGIN RSA PRIVATE KEY-----\nx"])
    assert any("secret" in p for p in validate_record(evil2))
    ok = build_record(dict(_good(), parent_allocation_id="alloc-1",
                           notes="thesis intact"))
    assert ok["user_action_confirmed"] is None
    assert ok["parent_allocation_id"] == "alloc-1"
    import pytest

    with pytest.raises(ValueError):
        build_record(evil)
