"""Harness offline parts: allowlist, evaluate dispatch, report writer."""
from __future__ import annotations

import pytest

from tests.eval import harness as h


def test_allowlist_enforcement(monkeypatch):
    monkeypatch.setenv("EVAL_BASE_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.delenv("EVAL_ALLOW_HOSTS", raising=False)
    assert h.assert_allowed("http://127.0.0.1:1234/v1/chat/completions")
    with pytest.raises(RuntimeError):
        h.assert_allowed("http://evil.test/c?p=1")
    with pytest.raises(RuntimeError):
        h.assert_allowed("http://127.0.0.1:1234/v1/models")
    monkeypatch.setenv("EVAL_ALLOW_HOSTS", "100.88.219.120")
    monkeypatch.setenv("EVAL_BASE_URL", "http://100.88.219.120:1234/v1")
    assert h.assert_allowed("http://100.88.219.120:1234/v1/chat/completions")


def test_evaluate_dispatches_known_checks():
    scenario = {"checks": [
        {"check": "verdict_token"},
        {"check": "no_execution_claims"},
        {"check": "bogus-check"},
    ]}
    outcome = {"transcript": "Hold.", "tool_calls": [],
               "record": {"verdict": "HOLD"}}
    out = h.evaluate(scenario, outcome)
    assert out["verdict_token"]["ok"] is True
    assert out["no_execution_claims"]["ok"] is True
    assert out["bogus-check"]["ok"] is False


def test_write_report_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "HERE", tmp_path)
    dest = h.write_report([{"id": "b09", "critical": False, "runs": 1,
                            "passed": 1, "pass_rate": 1.0, "path": "runs/b09/x"}],
                          model="test-model")
    text = dest.read_text(encoding="utf-8")
    assert "b09" in text and "test-model" in text and "100%" in text


def test_fence_parsing_accepts_one_line_and_multiline():
    one = '```record {"verdict": "HOLD"}```'
    multi = '```record\n{"verdict": "HOLD"}\n```'
    assert h.RECORD_RE.findall(one)[-1].strip() == '{"verdict": "HOLD"}'
    assert h.RECORD_RE.findall(multi)[-1].strip() == '{"verdict": "HOLD"}'
    tool = '```tool ta_snapshot {"ticker": "TST"}```'
    assert h.TOOL_RE.findall(tool)[0][0] == "ta_snapshot"
    sloppy = '```tool ta_snapshot {json} {"ticker": "TST"}```'
    assert h.TOOL_RE.findall(sloppy)[0][0] == "ta_snapshot"
