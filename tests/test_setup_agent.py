"""Setup/onboarding: example copying without overwrite, profile validation."""
from __future__ import annotations

import pytest

pytest.importorskip("yaml")


def _examples(agent_dir, monkeypatch=None):
    from scripts import setup_agent as setup

    base = agent_dir
    (base / "investor_profile.example.yaml").write_text(
        "schema_version: 1\nlanguage: sk\n", encoding="utf-8")
    (base / "tax_facts.example.yaml").write_text("# tax\n", encoding="utf-8")
    (base / "base_rates.example.md").write_text("# rates\n", encoding="utf-8")
    return setup


def test_copy_never_overwrites(tmp_path):
    from scripts import setup_agent as setup

    _examples(tmp_path)
    first = setup.copy_examples(tmp_path)
    assert first["investor_profile.yaml"] == "created"
    (tmp_path / "investor_profile.yaml").write_text("mine: true\n",
                                                   encoding="utf-8")
    second = setup.copy_examples(tmp_path)
    assert second["investor_profile.yaml"] == "kept"
    assert (tmp_path / "investor_profile.yaml").read_text(
        encoding="utf-8") == "mine: true\n"


def test_profile_validator_matrix(tmp_path):
    from scripts import setup_agent as setup

    full = tmp_path / "full.yaml"
    full.write_text(
        "schema_version: 1\nlanguage: sk\ntimezone: Europe/Bratislava\n"
        "base_currency: EUR\ntax_residency: SK\n"
        "accounts:\n  - broker: T212\n    coverage: complete\n"
        "risk_tolerance: medium\nmax_drawdown_tolerance_pct: 20\n"
        "max_single_stock_pct: 5\nmax_loss_per_position_pct: 1\n"
        "restrictions:\n  - no leverage\nbenchmark: EXI2\nhigh_stakes_threshold_pct: 5\n",
        encoding="utf-8")
    assert setup.validate_profile(full) == {"ok": True, "missing": [],
                                            "provisional": False}
    empty = tmp_path / "empty.yaml"
    empty.write_text("schema_version: 1\n", encoding="utf-8")
    partial = setup.validate_profile(empty)
    assert partial["provisional"] is True
    assert "risk_tolerance" in partial["missing"]
    assert "max_loss_per_position_pct" in partial["missing"]
    assert setup.validate_profile(tmp_path / "nope.yaml")["provisional"] is True
    badver = tmp_path / "badver.yaml"
    badver.write_text("schema_version: 2\n", encoding="utf-8")
    assert setup.validate_profile(badver)["ok"] is False


def test_opencode_json_parses_and_points_at_agents_md():
    import json

    from pathlib import Path

    cfg = json.loads((Path(__file__).resolve().parent.parent
                      / "opencode.json").read_text(encoding="utf-8"))
    assert "AGENTS.md" in cfg.get("instructions", [])
    num_ctx = cfg["provider"]["ollama"]["models"]
    assert any(m.get("options", {}).get("num_ctx", 0) >= 65536
               for m in num_ctx.values())
