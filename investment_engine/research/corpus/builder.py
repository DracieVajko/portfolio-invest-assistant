"""Phase 5 corpus builder: full corpus + curated decision set + files.

Writes (into the ai_context/ layer):
  research_corpus_<run_id>.json   every normalized item (tier-labelled)
  research_corpus_<run_id>.md     human/audit-readable full corpus

Never raises: failures degrade to FAILED items + degraded-coverage note.
Writes no files when output_dir is None (pure build mode for tests).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def build_corpus(news_by_symbol: dict[str, list[dict[str, Any]]],
                 now: datetime | None = None) -> dict[str, Any]:
    """Normalize + dedupe + classify one run's fetched records."""
    from investment_engine.research.corpus import adapters as _ad
    from investment_engine.research.corpus import dedupe as _dedupe
    from investment_engine.research.corpus import freshness as _fresh

    ref = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    from investment_engine.schemas.research_item import ResearchItem

    items: list[dict[str, Any]] = []
    failed = 0
    invalid = 0
    for symbol_key, records in (news_by_symbol or {}).items():
        try:
            adapted = _ad.adapt_symbol_records(str(symbol_key), records or [], str(symbol_key), ref)
        except Exception as exc:
            logger.debug("Corpus bucket failed for %s: %s", symbol_key, type(exc).__name__)
            adapted = [_ad.failed_item(str(symbol_key), type(exc).__name__)]
            failed += 1
        for item in adapted:
            # Schema validation: anything violating the ResearchItem contract
            # becomes an explicitly rejected FAILED item (never decision input).
            try:
                ResearchItem(**{k: v for k, v in item.items()
                                if k in ResearchItem.model_fields})
                items.append(item)
            except Exception as exc:
                bad = _ad.failed_item(str(symbol_key), f"schema: {type(exc).__name__}")
                bad["title"] = str(item.get("title", "") or bad["title"])[:140]
                items.append(bad)
                invalid += 1
    items = _dedupe.dedupe(items)
    decision = [i for i in items
                if _fresh.classify(i.get("age_hours"), int(i.get("source_tier", 3)),
                                   str(i.get("source_category", ""))) == "DECISION"]
    background = [i for i in items if i not in decision]
    undated = [i for i in items if i.get("fetch_status") == "UNDATED"]
    rejected = [i for i in items if i.get("fetch_status") in ("UNDATED", "FAILED")]
    return {
        "items": items,
        "decision_items": decision,
        "background_items": background,
        "rejected_items": rejected,
        "counts": {
            "total": len(items),
            "decision": len(decision),
            "background": len(background),
            "undated": len(undated),
            "rejected": len(rejected),
            "failed_buckets": failed,
            "schema_invalid": invalid,
        },
    }


def render_rejected_markdown(rejected: list[dict[str, Any]], run_id: str) -> str:
    """Rejected/undated research with explicit reasons. Never empty output."""
    lines = [f"# Rejected / Undated Research `{run_id}`", ""]
    if not rejected:
        lines.append("None — every collected item carried a validated publication timestamp.")
        return "\n".join(lines)
    for item in rejected:
        lines.append(
            f"- [{item.get('title', '')}]({item.get('canonical_url', '')}) — "
            f"{item.get('publisher', '')} — status={item.get('fetch_status')} — "
            f"reason: {item.get('failure_reason_if_any') or 'no validated publication timestamp'}")
    return "\n".join(lines)


def render_corpus_markdown(corpus: dict[str, Any], run_id: str) -> str:
    """Full corpus, tier-labelled. Decision section first, background after."""
    lines = [f"# Research Corpus `{run_id}`", ""]
    counts = corpus.get("counts", {})
    lines.append(
        f"Total: {counts.get('total', 0)} | Decision (≤48h, T0–T2): {counts.get('decision', 0)} | "
        f"Background: {counts.get('background', 0)} | Undated: {counts.get('undated', 0)}")
    lines.append("")
    for section, key in (("Decision-relevant (≤48h)", "decision_items"),
                         ("Background / older / social / undated", "background_items")):
        lines.append(f"## {section}")
        entries = corpus.get(key, []) or []
        if not entries:
            lines.append("None.")
        for item in entries[:200]:
            age = item.get("age_hours")
            age_s = f"{age:.1f}h" if isinstance(age, (int, float)) else "n/a"
            lines.append(
                f"- [{item.get('title', '')}]({item.get('canonical_url', '')}) "
                f"— {item.get('publisher', '')} — T{item.get('source_tier', '?')} "
                f"{item.get('source_category', '')} — {age_s} "
                f"[{(item.get('ticker_tags') or ['-'])[0]}]")
        lines.append("")
    return "\n".join(lines).rstrip()


def build_corpus_files(news_by_symbol: dict[str, list[dict[str, Any]]],
                       run_id: str = "", output_dir: str | Path = "reports",
                       now: datetime | None = None) -> dict[str, Any]:
    """Build + persist the corpus. Returns counts + paths (None when unwritten).

    run_id must never be blank (callers pass the engine run_id).
    """
    stamp = str(run_id or "").strip()
    if not stamp:
        raise ValueError("corpus run_id must never be blank")
    corpus = build_corpus(news_by_symbol, now)
    info: dict[str, Any] = {"counts": corpus["counts"], "path_md": None, "path_json": None,
                            "path_rejected": None,
                            "items": corpus["items"], "decision_items": corpus["decision_items"],
                            "rejected_items": corpus["rejected_items"]}
    try:
        out_dir = Path(output_dir) / "ai_context"
        out_dir.mkdir(parents=True, exist_ok=True)
        md_path = out_dir / f"research_corpus_{stamp}.md"
        json_path = out_dir / f"research_corpus_{stamp}.json"
        md_path.write_text(render_corpus_markdown(corpus, stamp), encoding="utf-8")
        json_path.write_text(json.dumps(
            {"run_id": stamp,
             "generated_at": datetime.now(timezone.utc).isoformat(),
             "counts": corpus["counts"],
             "items": corpus["items"]},
            indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        info["path_md"], info["path_json"] = str(md_path), str(json_path)
    except Exception as exc:
        logger.debug("Corpus persistence skipped: %s", type(exc).__name__)
    return info
