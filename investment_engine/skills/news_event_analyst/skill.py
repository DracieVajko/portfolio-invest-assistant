"""NewsEventAnalyst v1: deterministic event grouping + optional LLM enrichment.

Evidence enforced BEFORE any prompt render: each claimed event needs a fresh
decision-grade item. LLM enrichment (when a provider is passed and succeeds)
may refine labels; its citations are validated against known item IDs.
"""

from __future__ import annotations

import json
import re
from typing import Any

from investment_engine.skills import contracts
from investment_engine.skills import registry as _registry

SKILL = "news_event_analyst"
VERSION = "v1"

_TYPE_KEYWORDS = (
    ("EARNINGS", ("earnings", "eps", "results", "quarter")),
    ("GUIDANCE", ("guidance", "outlook", "forecast")),
    ("ANALYST", ("upgrade", "downgrade", "price target", "rating", "analyst")),
    ("POLICY", ("tariff", "regulation", "white house", "trump", "sanction", "export control")),
    ("MACRO", ("fed", "rate", "inflation", "jobs", "gdp", "ecb")),
    ("CORPORATE", ("merger", "acquisition", "ceo", "dividend", "buyback", "split")),
    ("SECTOR", ("sector", "industry")),
)


def _classify_type(title: str) -> str:
    low = title.lower()
    for label, keywords in _TYPE_KEYWORDS:
        if any(k in low for k in keywords):
            return label
    return "OTHER"


def _deterministic_events(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        tags = item.get("ticker_tags") or ["GENERAL"]
        by_ticker.setdefault(str(tags[0]).upper(), []).append(item)
    events = []
    for ticker, group in sorted(by_ticker.items()):
        pubs = {str(i.get("publisher", "")).lower() for i in group}
        state = "CONFIRMED" if len(pubs) >= 2 else "UNVERIFIED"
        best = sorted(group, key=lambda i: (i.get("source_tier", 3), -(i.get("relevance_score", 0) or 0)))[0]
        severities = {"EARNINGS": "high", "GUIDANCE": "high", "ANALYST": "medium",
                      "POLICY": "high", "MACRO": "medium", "CORPORATE": "medium"}
        etype = _classify_type(str(best.get("title", "")))
        events.append({
            "ticker": ticker,
            "type": etype,
            "item_ids": [str(i.get("id")) for i in group],
            "severity": severities.get(etype, "low"),
            "state": state,
        })
    return events


def run(inputs: dict[str, Any], provider=None, settings=None, language: str = "English") -> dict[str, Any]:
    items = [i for i in (inputs.get("decision_items", []) or []) if isinstance(i, dict)]
    ok, reason = contracts.require_evidence(items, min_items=1)
    if not ok:
        return contracts.insufficient_output(SKILL, VERSION, reason)

    events = _deterministic_events(items)
    basis = [f"items={len(items)}", f"events={len(events)}", "deterministic-grouping"]
    enriched = False
    if provider is not None:
        try:
            prompt = (_registry.load_prompt(SKILL, VERSION)
                      + f"\n\nEVIDENCE ({len(items)} items):\n"
                      + "\n".join(f"- [{i.get('id')}] {i.get('title', '')[:120]} ({i.get('publisher', '')})"
                                  for i in items[:12]))
            raw = provider.generate(prompt, stage="news_events",
                                    context={"max_output_tokens": 800})
            if isinstance(raw, str) and raw.strip().startswith("{"):
                parsed = json.loads(re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip())
                if isinstance(parsed.get("events"), list):
                    known = {str(i.get("id")) for i in items}
                    good, why = contracts.validate_citations({"data": parsed}, known)
                    if good:
                        events = parsed["events"]
                        enriched = True
                        basis.append("llm-enriched")
        except Exception:
            pass
    if enriched:
        basis = [b for b in basis if b != "deterministic-grouping"]
    return {
        "skill": SKILL,
        "version": VERSION,
        "status": "OK",
        "reason": "",
        "confidence": contracts.confidence(0.7 if enriched else 0.55, basis),
        "unverified": [e["ticker"] for e in events if e.get("state") == "UNVERIFIED"],
        "data": {"events": events, "enriched": enriched},
    }


def render(output: dict[str, Any]) -> str:
    """Markdown section for the AI context file."""
    lines = []
    for event in (output.get("data", {}) or {}).get("events", []) or []:
        lines.append(
            f"- **{event.get('ticker')}** [{event.get('type')}/{event.get('state')}] "
            f"severity={event.get('severity')} evidence={len(event.get('item_ids', []))}")
    return "\n".join(lines) if lines else "No classified events."
