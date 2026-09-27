"""Phase 0 foundation tests: runtime gate, secret hygiene, logger safety.

Offline only. Never reads the real api.env values.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_python_gate_accepts_312():
    import portfolio_ai_assistant as entry

    assert entry.REQUIRED_PYTHON_MAJOR == 3
    assert entry.REQUIRED_PYTHON_MINOR == 12
    assert entry.check_python_version((3, 12)) == (3, 12)


def test_python_gate_rejects_other_minors():
    import portfolio_ai_assistant as entry

    for version in [(3, 11), (3, 13), (3, 10), (2, 7)]:
        with pytest.raises(SystemExit) as exc_info:
            entry.check_python_version(version)
        assert "3.12" in str(exc_info.value)


def test_python_gate_actionable_message():
    import portfolio_ai_assistant as entry

    with pytest.raises(SystemExit) as exc_info:
        entry.check_python_version((3, 11))
    msg = str(exc_info.value)
    assert "py -3.12" in msg


def test_module_level_logger_exists():
    """write_reports references module-level logger on archive-copy failure."""
    import logging

    import portfolio_ai_assistant as entry

    assert isinstance(entry.logger, logging.Logger)
    assert entry.logger.name == entry.__name__


def test_write_reports_survives_archive_copy_failure(tmp_path, monkeypatch):
    """Archive-copy OSError must not raise NameError (latent logger bug)."""
    import shutil

    import portfolio_ai_assistant as entry

    def _boom(*args, **kwargs):
        raise OSError("disk full (simulated)")

    monkeypatch.setattr(shutil, "copy2", _boom)
    result = {
        "brief_markdown": "# brief",
        "snapshot_markdown": "# snapshot",
        "reconciliation": {"status": "UNKNOWN"},
        "failed_tickers": [],
        "monitoring_items": [],
        "decision_news": [],
        "brief_metadata": {},
    }
    published = entry.write_reports(result, tmp_path / "out", tmp_path / "arch", "testrun1")
    assert (tmp_path / "out" / "current" / "portfolio_decision_brief.md").is_file()
    assert (tmp_path / "out" / "current" / "run_manifest.json").is_file()
    assert not (tmp_path / "arch" / "testrun1" / "run_manifest.json").is_file()
    assert set(published) >= {"portfolio_decision_brief.md", "run_manifest.json"}


def _parse_env_example(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    return values


def test_api_env_example_has_only_placeholders():
    example = ROOT / "api.env.example"
    assert example.is_file(), "api.env.example must exist"
    values = _parse_env_example(example)
    assert "TRADING212_API_KEY" in values
    assert "TRADING212_API_SECRET" in values
    for key, val in values.items():
        if not val:
            continue  # empty = provider skipped, safe
        lowered = val.lower()
        assert any(
            marker in lowered
            for marker in ("paste-your", "placeholder", "example", "your-", "127.0.0.1", "localhost", "auto")
        ), f"{key} in api.env.example does not look like a placeholder"
        assert len(val) < 64, f"{key} value suspiciously long for a placeholder"


def test_api_env_example_no_localhost_secrets_leak(tmp_path):
    """Guard shape: example keys must be a subset of known non-sensitive names."""
    values = _parse_env_example(ROOT / "api.env.example")
    allowed = {
        "TRADING212_API_KEY", "TRADING212_API_SECRET", "TRADING212_ACCOUNT_ID",
        "PROVIDER", "LM_STUDIO_BASE_URL", "LM_STUDIO_MODEL",
        "OLLAMA_BASE_URL", "OLLAMA_MODEL", "LLAMACPP_BASE_URL", "LLAMACPP_MODEL",
        "OPENROUTER_API_KEY", "GEMINI_API_KEY", "MISTRAL_API_KEY", "OPENCODE_ZEN_API_KEY",
        "DECISION_MODEL", "WRITER_MODEL",
    }
    assert set(values) <= allowed, f"unexpected keys: {set(values) - allowed}"


def test_verify_environment_never_prints_secret_values(monkeypatch, capsys):
    """Sentinel env value must not appear in verifier stdout."""
    from scripts import verify_environment as ve

    sentinel = "SENTINEL-SECRET-VALUE-9f8e7d6c5b4a"
    monkeypatch.setenv("TRADING212_API_KEY", sentinel)
    monkeypatch.setenv("GEMINI_API_KEY", sentinel)
    rc = ve.main([])
    out = capsys.readouterr().out
    assert sentinel not in out
    assert rc in (0, 1, 2)


def test_verify_environment_reports_indicator_engine(capsys):
    from scripts import verify_environment as ve

    engine = ve._indicator_engine()
    assert engine in (
        "pandas-ta engine (HAS_PANDAS_TA=True if import succeeds)",
        "manual fallback engine (_apply_manual_indicators)",
    )


def test_running_interpreter_is_312():
    assert (sys.version_info.major, sys.version_info.minor) == (3, 12)


def test_no_broken_typing_imports_repo_wide():
    """Guard against the 'from typing X' (missing import) typo in tracked sources."""
    import re

    root = ROOT
    offenders = []
    for path in list((root / "investment_engine").rglob("*.py")) + \
            list((root / "scripts").rglob("*.py")) + \
            list((root / "tools").rglob("*.py")) + \
            list((root / "tests").rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if re.match(r"from typing (?!import\b)[A-Za-z]", line.strip()):
                offenders.append(f"{path.relative_to(root)}:{i}")
    assert not offenders, offenders
