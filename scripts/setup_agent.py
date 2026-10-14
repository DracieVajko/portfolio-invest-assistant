"""Agent onboarding: copy examples without overwriting, validate the profile.

Usage:
    py scripts/setup_agent.py [--agent-dir agent_data] [--strict]

- Copies *.example.* to real names only when the real file is missing.
- Validates investor_profile.yaml against Appendix A: prints missing fields
  and whether suitability conclusions are provisional.
- Warns when AGENTS.md (the operating spec) is absent from the repo root.
- Exit 0 always, except --strict exits 1 while provisional.
Never overwrites user files. No network. No secrets printed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXAMPLE_PAIRS = (
    ("investor_profile.example.yaml", "investor_profile.yaml"),
    ("tax_facts.example.yaml", "tax_facts.yaml"),
    ("base_rates.example.md", "base_rates.md"),
)

# Appendix A fields required for non-provisional suitability conclusions.
REQUIRED_PROFILE_FIELDS = (
    "language",
    "timezone",
    "base_currency",
    "tax_residency",
    "accounts",
    "risk_tolerance",
    "max_drawdown_tolerance_pct",
    "max_single_stock_pct",
    "max_loss_per_position_pct",
    "restrictions",
    "benchmark",
    "high_stakes_threshold_pct",
)


def copy_examples(agent_dir: Path | None = None) -> dict:
    """Copy missing example files. Returns {name: 'created'|'kept'|'missing-example'}."""
    base = Path(agent_dir) if agent_dir else ROOT / "agent_data"
    result: dict[str, str] = {}
    for example_name, real_name in EXAMPLE_PAIRS:
        src, dest = base / example_name, base / real_name
        try:
            if dest.exists():
                result[real_name] = "kept"
            elif src.is_file():
                dest.write_bytes(src.read_bytes())
                result[real_name] = "created"
            else:
                result[real_name] = "missing-example"
        except OSError as exc:
            result[real_name] = f"error:{type(exc).__name__}"
    return result


def _present(mapping: dict, dotted: str) -> bool:
    try:
        node: object = mapping
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
        if node is None:
            return False
        if isinstance(node, (str, list, dict)) and not node:
            return False
        return True
    except Exception:
        return False


def validate_profile(path: Path | str | None = None) -> dict:
    """Validate investor_profile.yaml. Returns {ok, missing, provisional}."""
    target = Path(path) if path else ROOT / "agent_data" / "investor_profile.yaml"
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None  # type: ignore
    try:
        if yaml is None or not target.is_file():
            return {"ok": False, "missing": ["profile file (PyYAML or file)"],
                    "provisional": True}
        data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return {"ok": False, "missing": [f"unreadable: {type(exc).__name__}"],
                "provisional": True}
    if not isinstance(data, dict):
        return {"ok": False, "missing": ["profile root must be a mapping"],
                "provisional": True}
    if str(data.get("schema_version", "")) != "1":
        return {"ok": False, "missing": ["schema_version == 1"],
                "provisional": True}
    missing = [f for f in REQUIRED_PROFILE_FIELDS if not _present(data, f)]
    return {"ok": not missing, "missing": missing,
            "provisional": bool(missing)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Agent onboarding setup.")
    ap.add_argument("--agent-dir", default="")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 while suitability is provisional")
    args = ap.parse_args(argv)
    base = Path(args.agent_dir) if args.agent_dir else ROOT / "agent_data"

    print("== agent_data setup ==")
    for name, state in copy_examples(base).items():
        print(f"  {name}: {state}")
    if not (ROOT / "AGENTS.md").is_file():
        print("  WARNING: AGENTS.md missing from repo root "
              "(operating spec lives there per project convention)")
    profile = validate_profile(base / "investor_profile.yaml")
    if profile["missing"]:
        print("  profile missing fields:")
        for field in profile["missing"]:
            print(f"    - {field}")
    print(f"  suitability provisional: {profile['provisional']}")
    if args.strict and (profile["provisional"] or not profile["ok"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
