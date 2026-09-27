"""Phase 0 environment verifier — offline, read-only, never prints secrets.

Checks:
  - Python 3.12 runtime (major/minor; patch versions allowed)
  - core dependencies importable (importlib.find_spec, no imports executed)
  - optional modules (pandas_ta, talib, playwright, tzdata) reported separately
  - which technical-indicator engine would activate (pandas-ta vs manual)
  - pip check (local only, no network install)
  - api.env / reports / logs / data/cache are gitignored (secret guard)
  - source syntax of core production files (ast.parse, no bytecode written)

Exit codes: 0 OK, 1 missing core dependency/syntax/smoke failure,
2 secret-unignored (.gitignore missing or path not ignored).

Default writes no files. ``--smoke`` runs one fast offline pytest module.
Never prints environment variable values — only PRESENT/MISSING.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_PYTHON_MAJOR = 3
REQUIRED_PYTHON_MINOR = 12

CORE_MODULES = [
    "requests",
    "yfinance",
    "pandas",
    "ddgs",
    "dotenv",
    "pydantic",
    "scipy",
    "numpy",
    "feedparser",
    "finvizfinance",
    "tzdata",
]

OPTIONAL_MODULES = ["pandas_ta", "talib", "playwright", "tzdata"]

SECRET_GUARDED_PATHS = ["api.env", "reports", "logs", "data/cache"]

CORE_SOURCE_FILES = [
    "portfolio_ai_assistant.py",
    "investment_engine/main.py",
    "investment_engine/portfolio/broker_first.py",
    "investment_engine/portfolio/symbols.py",
    "investment_engine/reporting/regime_report.py",
    "investment_engine/research/technical_analysis.py",
    "investment_engine/providers/factory.py",
]


def _spec_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _check_pip() -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "check"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"pip check could not run: {type(exc).__name__}"
    if proc.returncode == 0:
        return True, "pip check: OK (no broken requirements)"
    first_lines = "\n".join(proc.stdout.splitlines()[:5])
    return False, f"pip check FAILED:\n{first_lines}"


def _indicator_engine() -> str:
    """Report which indicator engine would activate, without network calls."""
    if _spec_present("pandas_ta"):
        return "pandas-ta engine (HAS_PANDAS_TA=True if import succeeds)"
    return "manual fallback engine (_apply_manual_indicators)"


def _gitignore_covers(path: str) -> bool | None:
    """True if git ignores path, False if not, None if git unavailable."""
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", path],
            capture_output=True,
            cwd=str(ROOT),
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.returncode == 0


def _gitignore_fallback_mentions(path: str) -> bool:
    """Fallback when git is missing: look for a matching pattern line."""
    gi = ROOT / ".gitignore"
    if not gi.is_file():
        return False
    try:
        lines = gi.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    key = path.rstrip("/").lower()
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        norm = line.rstrip("/").lower()
        if key in norm or norm in key:
            return True
    return False


def _check_secret_guards() -> tuple[bool, list[str]]:
    report: list[str] = []
    ok = True
    if not (ROOT / ".gitignore").is_file():
        return False, [".gitignore: MISSING (secrets exposed)"]
    report.append(".gitignore: PRESENT")
    for path in SECRET_GUARDED_PATHS:
        covered = _gitignore_covers(path)
        if covered is None:
            covered = _gitignore_fallback_mentions(path)
            report.append(f"{path}: git unavailable, .gitignore pattern match -> {'IGNORED' if covered else 'EXPOSED'}")
        else:
            report.append(f"{path}: {'IGNORED' if covered else 'EXPOSED'}")
        if not covered:
            ok = False
    return ok, report


def _check_syntax() -> tuple[bool, list[str]]:
    report: list[str] = []
    ok = True
    for rel in CORE_SOURCE_FILES:
        target = ROOT / rel
        if not target.is_file():
            report.append(f"{rel}: MISSING FILE")
            ok = False
            continue
        try:
            source = target.read_text(encoding="utf-8")
        except OSError:
            report.append(f"{rel}: UNREADABLE")
            ok = False
            continue
        try:
            ast.parse(source, filename=rel)
        except SyntaxError as exc:
            report.append(f"{rel}: SYNTAX ERROR ({exc.msg} line {exc.lineno})")
            ok = False
        else:
            report.append(f"{rel}: OK")
    return ok, report


def _run_smoke() -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_result_contract.py", "-q", "--tb=line", "-p", "no:cacheprovider"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"smoke test could not run: {type(exc).__name__}"
    tail = "\n".join(proc.stdout.splitlines()[-3:])
    if proc.returncode == 0:
        return True, f"smoke (test_result_contract): PASSED\n{tail}"
    err_tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-8:])
    return False, f"smoke (test_result_contract): FAILED (exit {proc.returncode})\n{err_tail}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 0 offline environment verifier (no network, no secrets printed).")
    parser.add_argument("--smoke", action="store_true", help="Also run one fast offline pytest module.")
    args = parser.parse_args(argv)

    failures_core = False
    failures_secret = False

    print(f"python executable: {sys.executable}")
    print(f"python version: {sys.version.split()[0]}")
    if (sys.version_info.major, sys.version_info.minor) != (REQUIRED_PYTHON_MAJOR, REQUIRED_PYTHON_MINOR):
        print(f"FAIL: Python {REQUIRED_PYTHON_MAJOR}.{REQUIRED_PYTHON_MINOR}.x required (patch versions allowed)")
        failures_core = True
    else:
        print(f"python gate: OK ({REQUIRED_PYTHON_MAJOR}.{REQUIRED_PYTHON_MINOR}.x)")

    print("\n[core modules]")
    for mod in CORE_MODULES:
        present = _spec_present(mod)
        print(f"  {mod}: {'PRESENT' if present else 'MISSING'}")
        if not present:
            failures_core = True

    print("\n[optional modules]")
    for mod in OPTIONAL_MODULES:
        print(f"  {mod}: {'PRESENT' if _spec_present(mod) else 'MISSING'}")

    print(f"\n[indicator engine]\n  {_indicator_engine()}")

    print("\n[pip check]")
    pip_ok, pip_msg = _check_pip()
    print(f"  {pip_msg}")
    if not pip_ok:
        failures_core = True

    print("\n[secret guards]")
    guards_ok, guard_lines = _check_secret_guards()
    for line in guard_lines:
        print(f"  {line}")
    if not guards_ok:
        failures_secret = True

    print("\n[source syntax]")
    syntax_ok, syntax_lines = _check_syntax()
    for line in syntax_lines:
        print(f"  {line}")
    if not syntax_ok:
        failures_core = True

    if args.smoke:
        print("\n[smoke]")
        smoke_ok, smoke_msg = _run_smoke()
        print(f"  {smoke_msg}")
        if not smoke_ok:
            failures_core = True

    print()
    if failures_secret:
        print("RESULT: FAIL (secret-unignored, exit 2)")
        return 2
    if failures_core:
        print("RESULT: FAIL (missing core dependency/syntax/smoke, exit 1)")
        return 1
    print("RESULT: OK (exit 0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
