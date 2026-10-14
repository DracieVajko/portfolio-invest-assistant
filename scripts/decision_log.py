"""Append-only decision log writer (operating instructions §13, Appendix C).

Usage:
    py scripts/decision_log.py new --data record.json
    py scripts/decision_log.py new --ticker AAPL --mode M1 --verdict HOLD --confidence medium
    py scripts/decision_log.py list [--ticker AAPL]

Records land in agent_data/decision_log/ (gitignored) as
{YYYYMMDDTHHMMSSZ}_{TICKER}_{MODE}.json plus one index.jsonl line.
Append-only: existing files are never overwritten (a collision suffix is
added instead). Never stores credentials.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "agent_data" / "decision_log"

VERDICTS = {"BUY", "HOLD", "ADD", "TRIM", "SELL", "WATCH", "AVOID",
            "INSUFFICIENT DATA"}
CONFIDENCE = {"high", "medium", "low"}
MODES = {"M1", "M3", "M5"}

_REQUIRED = ("mode", "asset", "verdict", "confidence")

_SECRET_PATTERNS = (
    r"(?i)(api[_-]?key|secret|token|password|passwd)\s*[:=]\s*\S{6,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\bsk-(live|proj)-[A-Za-z0-9]{8,}",
    r"\bghp_[A-Za-z0-9]{20,}",
    r"\bgho_[A-Za-z0-9]{20,}",
)


def _safe_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9.-]+", "_", str(value or "").strip())
    return token.strip("._") or "UNKNOWN"


def validate_record(record: dict) -> list[str]:
    """Return a list of problems (empty = valid). Never raises."""
    problems: list[str] = []
    try:
        if not isinstance(record, dict):
            return ["record must be an object"]
        for key in _REQUIRED:
            if key not in record:
                problems.append(f"missing required key: {key}")
        asset = record.get("asset")
        if not isinstance(asset, dict) or not str(asset.get("ticker", "")).strip():
            problems.append("asset.ticker is required")
        if "verdict" in record and record["verdict"] not in VERDICTS:
            problems.append(f"bad verdict: {record['verdict']!r}")
        if "confidence" in record and record["confidence"] not in CONFIDENCE:
            problems.append(f"bad confidence: {record['confidence']!r}")
        if "mode" in record and record["mode"] not in MODES:
            problems.append(f"bad mode (loggable: {sorted(MODES)}): {record['mode']!r}")
        notes = record.get("notes", "")
        blobs = [notes] if isinstance(notes, str) else (
            notes if isinstance(notes, list) else [])
        import re as _re

        for blob in blobs:
            if not isinstance(blob, str):
                continue
            for pat in _SECRET_PATTERNS:
                if _re.search(pat, blob):
                    problems.append("possible secret in notes: rejected")
                    break
            else:
                continue
            break
    except Exception as exc:  # never break the caller over validation itself
        problems.append(f"validator error: {type(exc).__name__}")
    return problems


def build_record(data: dict, *, created_utc: str | None = None) -> dict:
    """Assemble a full record (id + timestamps). Raises ValueError if invalid."""
    problems = validate_record(data)
    if problems:
        raise ValueError("; ".join(problems))
    stamp = created_utc or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ticker = _safe_token(data["asset"]["ticker"])
    record = {
        "id": f"{stamp}_{ticker}_{data['mode']}",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "user_action_confirmed": None,
        **{k: v for k, v in data.items()},
    }
    return record


def append_record(record: dict, log_dir: Path | None = None) -> Path:
    """Write one record + index line. Append-only: an existing id raises
    FileExistsError (corrections are new records with supersedes)."""
    target_dir = Path(log_dir) if log_dir else LOG_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / f"{record['id']}.json"
    if dest.exists():
        raise FileExistsError(f"decision record already exists: {dest.name} "
                              f"(write a correction with supersedes instead)")
    dest.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    index = target_dir / "index.jsonl"
    with open(index, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "id": record["id"],
            "created_utc": record.get("created_utc", ""),
            "ticker": (record.get("asset") or {}).get("ticker", ""),
            "isin": (record.get("asset") or {}).get("isin", ""),
            "mode": record.get("mode", ""),
            "verdict": record.get("verdict", ""),
            "supersedes": record.get("supersedes"),
            "parent_allocation_id": record.get("parent_allocation_id"),
        }, ensure_ascii=False) + "\n")
    return dest


def list_records(ticker: str | None = None,
                 log_dir: Path | None = None) -> list[dict]:
    """Read the index (no full-file scan). Never raises."""
    try:
        index = (Path(log_dir) if log_dir else LOG_DIR) / "index.jsonl"
        if not index.is_file():
            return []
        out = []
        for line in index.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if ticker and str(row.get("ticker", "")).upper() != ticker.upper():
                continue
            out.append(row)
        return out
    except Exception:
        return []


def lookup_prior(identifier: str, log_dir: Path | None = None) -> dict | None:
    """Latest non-superseded record for a ticker or ISIN (anti-churn, §10.7).

    A record is superseded when another record names its id in `supersedes`.
    Returns the full record dict, or None. Never raises.
    """
    try:
        key = str(identifier or "").strip().upper()
        if not key:
            return None
        target_dir = Path(log_dir) if log_dir else LOG_DIR
        rows = list_records(log_dir=target_dir)
        mine = [r for r in rows
                if str(r.get("ticker", "")).upper() == key
                or str(r.get("isin", "")).upper() == key]
        if not mine:
            return None
        superseded = {str(r.get("supersedes", "") or "") for r in rows}
        live = [r for r in mine if r.get("id") not in superseded]
        if not live:
            return None
        best = sorted(live, key=lambda r: str(r.get("created_utc", "")))[-1]
        doc = target_dir / f"{best['id']}.json"
        if not doc.is_file():
            return None
        record = json.loads(doc.read_text(encoding="utf-8"))
        return record if isinstance(record, dict) else None
    except Exception:
        return None


def anti_churn(prior: dict | None, *, triggers_fired: list | None = None,
               new_filing: bool = False, error: bool = False,
               regime_changed: bool = False) -> str:
    """§10.7: 'repeat_prior' unless an upgrade/downgrade trigger fired, a new
    filing arrived, or an error was found. A regime change alone never flips.
    Pure function (no I/O)."""
    try:
        if prior is None:
            return "no_prior"
        if error or new_filing or bool(triggers_fired):
            return "re-evaluate"
        return "repeat_prior"
    except Exception:
        return "re-evaluate"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Append-only decision log writer.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_new = sub.add_parser("new", help="validate + append one record")
    p_new.add_argument("--data", default="",
                       help="inline JSON object or @path/to/record.json")
    p_new.add_argument("--ticker", default="")
    p_new.add_argument("--mode", default="")
    p_new.add_argument("--verdict", default="")
    p_new.add_argument("--confidence", default="medium")
    sub.add_parser("list", help="list index rows").add_argument(
        "--ticker", default="")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        for row in list_records(args.ticker or None):
            print(json.dumps(row, ensure_ascii=False))
        return 0

    if args.data.startswith("@"):
        data = json.loads(Path(args.data[1:]).read_text(encoding="utf-8"))
    elif args.data:
        data = json.loads(args.data)
    else:
        data = {"asset": {"ticker": args.ticker}, "mode": args.mode,
                "verdict": args.verdict, "confidence": args.confidence}
    try:
        record = build_record(data)
    except ValueError as exc:
        print(f"ERROR: invalid record: {exc}", file=sys.stderr)
        return 2
    dest = append_record(record)
    print(str(dest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
