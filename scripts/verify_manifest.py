"""Verify run_manifest.json against files on disk (finding 16).

Re-hashes reports/current/* (except the manifest itself) and compares
sha256/bytes with the manifest entries. Also checks:
  - manifest["run_id_consistent"] is True,
  - ai_context/ai_context_<run_id>.md exists and its internal
    run_metadata run_id matches the manifest run_id.

Exit 0 when everything matches, 1 otherwise. Never touches the network.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


def _fail(problems: list[str], msg: str) -> None:
    problems.append(msg)
    print(f"FAIL: {msg}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify run manifest integrity.")
    ap.add_argument("--reports-dir", default="reports",
                    help="Reports root dir (default: reports)")
    args = ap.parse_args()

    reports = Path(args.reports_dir)
    current = reports / "current"
    manifest_path = current / "run_manifest.json"
    problems: list[str] = []

    if not manifest_path.is_file():
        print(f"FAIL: missing {manifest_path}")
        return 1
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: manifest unreadable: {exc}")
        return 1

    run_id = str(manifest.get("run_id", "") or "")
    print(f"run_id: {run_id or '?'}")

    files = manifest.get("files", {}) or {}
    for name, meta in files.items():
        p = current / name
        if not p.is_file():
            _fail(problems, f"missing file on disk: {name}")
            continue
        raw = p.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if not isinstance(meta, dict):
            _fail(problems, f"manifest entry not a dict: {name}")
            continue
        if digest != meta.get("sha256"):
            _fail(problems, f"sha256 mismatch: {name}")
        if len(raw) != meta.get("bytes"):
            _fail(problems, f"bytes mismatch: {name} (disk={len(raw)} manifest={meta.get('bytes')})")

    if not manifest.get("run_id_consistent", False):
        _fail(problems,
              f"run_id_consistent is not True (context_run_id={manifest.get('context_run_id')!r})")

    # ai_context internal run_id must match the manifest run_id.
    ctx_name = f"ai_context_{run_id}.md" if run_id else ""
    ctx_path = reports / "ai_context" / ctx_name if ctx_name else None
    if ctx_path is None or not ctx_path.is_file():
        _fail(problems, f"missing ai_context file: {ctx_name or '?'}")
    else:
        head = ctx_path.read_text(encoding="utf-8")[:2000]
        m = re.search(r'run_id:\s*"([^"]+)"', head)
        inner = m.group(1) if m else ""
        if inner != run_id:
            _fail(problems, f"ai_context inner run_id={inner!r} != manifest run_id={run_id!r}")

    if problems:
        print(f"verify_manifest: {len(problems)} problem(s)")
        return 1
    print("verify_manifest: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
