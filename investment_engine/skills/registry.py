"""Phase 6 skill registry: versioned prompts, pinned versions, safe dispatch.

Prompts load ONLY through this registry (never by direct file read).
A skill run never raises: provider/model failures yield the skill's
deterministic fallback output, labelled as such.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from investment_engine.skills import contracts

SKILLS_DIR = Path(__file__).resolve().parent

# Pinned skill versions (recorded in the run manifest).
SKILL_VERSIONS: dict[str, str] = {
    "portfolio_risk_manager": "v1",
    "news_event_analyst": "v1",
    "equity_research_analyst": "v1",
    "investment_committee": "v1",
}

_SKILL_MODULES = {
    "portfolio_risk_manager": "investment_engine.skills.portfolio_risk_manager.skill",
    "news_event_analyst": "investment_engine.skills.news_event_analyst.skill",
    "equity_research_analyst": "investment_engine.skills.equity_research_analyst.skill",
    "investment_committee": "investment_engine.skills.investment_committee.skill",
}


def load_prompt(skill: str, version: str | None = None) -> str:
    """Load a versioned skill prompt. Only entry point for skill prompts."""
    version = version or SKILL_VERSIONS[skill]
    path = SKILLS_DIR / skill / f"prompt_{version}.md"
    if not path.is_file():
        raise FileNotFoundError(f"skill prompt missing: {skill}/{version}")
    return path.read_text(encoding="utf-8")


def load_manifest(skill: str) -> dict[str, Any]:
    path = SKILLS_DIR / skill / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def get_skill(skill: str):
    """Import a skill module by registry name (KeyError on unknown skill)."""
    return importlib.import_module(_SKILL_MODULES[skill])


def run_skill(skill: str, inputs: dict[str, Any], provider=None,
              settings=None, language: str = "English",
              version: str | None = None) -> dict[str, Any]:
    """Run a skill with failure isolation (never raises)."""
    try:
        version = version or SKILL_VERSIONS[skill]
        module = get_skill(skill)
        output = module.run(inputs or {}, provider, settings, language)
        if not isinstance(output, dict) or "status" not in output:
            raise ValueError("skill returned no structured output")
        output.setdefault("skill", skill)
        output.setdefault("version", version)
        return output
    except Exception as exc:
        return contracts.insufficient_output(skill, version, f"{type(exc).__name__}: {exc}"[:200])
