"""Phase 6 skill contracts: evidence enforcement + output validation.

Every skill run follows: require_evidence() BEFORE any prompt render
(insufficient → INSUFFICIENT_EVIDENCE output, never LLM prose), then
structured run, then validate_output() (unknown item IDs rejected).
"""

from __future__ import annotations

from typing import Any

INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


def require_evidence(items: list[dict[str, Any]] | None,
                     min_items: int = 1,
                     max_age_hours: float = 48.0,
                     allowed_tiers: frozenset[int] = frozenset({0, 1, 2}),
                     require_urls: bool = True) -> tuple[bool, str]:
    """Check an evidence set meets the contract. Returns (ok, reason)."""
    items = [i for i in (items or []) if isinstance(i, dict)]
    if len(items) < min_items:
        return False, f"need>={min_items} items, have {len(items)}"
    for item in items:
        age = item.get("age_hours")
        if not isinstance(age, (int, float)) or not (0.0 <= age <= max_age_hours):
            return False, f"item {item.get('id', '?')} fails freshness (age={age})"
        try:
            tier = int(item.get("source_tier", 3))
        except (TypeError, ValueError):
            return False, f"item {item.get('id', '?')} has no tier"
        if tier not in allowed_tiers:
            return False, f"item {item.get('id', '?')} tier {tier} not decision-grade"
        if require_urls and not str(item.get("canonical_url", "")).startswith("http"):
            return False, f"item {item.get('id', '?')} has no URL"
        if str(item.get("fetch_status", "OK")) != "OK":
            return False, f"item {item.get('id', '?')} status {item.get('fetch_status')}"
    return True, "ok"


def insufficient_output(skill: str, version: str, reason: str) -> dict[str, Any]:
    """Standard fallback output when evidence or model is unavailable."""
    return {
        "skill": skill,
        "version": version,
        "status": INSUFFICIENT_EVIDENCE,
        "reason": reason,
        "confidence": {"value": 0.0, "basis": []},
        "unverified": [],
        "data": {},
    }


def validate_citations(output: dict[str, Any], known_ids: set[str]) -> tuple[bool, str]:
    """Every cited corpus ID must exist. Returns (ok, reason)."""
    cited: set[str] = set()
    data = output.get("data", {}) if isinstance(output, dict) else {}
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, val in node.items():
                if key in ("item_id", "item_ids", "evidence_ids", "rationale_ids"):
                    vals = val if isinstance(val, list) else [val]
                    cited.update(str(v) for v in vals if v)
                elif isinstance(val, (dict, list)):
                    stack.append(val)
        elif isinstance(node, list):
            stack.extend(node)
    unknown = cited - set(known_ids)
    if unknown:
        return False, f"unknown corpus IDs cited: {sorted(unknown)[:5]}"
    return True, "ok"


def confidence(value: float, basis: list[str]) -> dict[str, Any]:
    """Confidence model: 0..1 value + human-readable basis list."""
    try:
        v = max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        v = 0.0
    return {"value": v, "basis": [str(b) for b in (basis or [])]}
