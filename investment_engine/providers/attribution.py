"""Phase 4 provider attribution: record the actual winner per LLM stage.

Configured model names (decision_model/writer_model) describe intent; this
module records what really served each stage: provider name, served model,
fallback depth, and error class. Deterministic-fallback stages are labeled
explicitly instead of masquerading as model output.
"""

from __future__ import annotations

from typing import Any


def new_stage_record(stage: str, model_requested: str | None = None) -> dict[str, Any]:
    """Blank per-stage record. Never contains secrets (names only)."""
    return {
        "stage": stage,
        "provider": "unknown",
        "model_requested": model_requested or "",
        "model_served": "",
        "fallback_depth": -1,
        "error_class": "",
    }


def note_chain_winner(record: dict[str, Any], provider_name: str, model_served: str | None, depth: int) -> dict[str, Any]:
    record["provider"] = provider_name or "unknown"
    record["model_served"] = model_served or provider_name or ""
    record["fallback_depth"] = int(depth)
    return record


def note_single_provider(record: dict[str, Any], provider) -> dict[str, Any]:
    name = getattr(provider, "name", "provider") or "provider"
    record["provider"] = name
    record["model_served"] = getattr(provider, "model", "") or name
    record["fallback_depth"] = 0
    return record


def note_deterministic(record: dict[str, Any], profile: str = "") -> dict[str, Any]:
    record["provider"] = "deterministic"
    record["model_served"] = f"deterministic({profile})" if profile else "deterministic"
    record["fallback_depth"] = 99
    return record


def note_error(record: dict[str, Any], exc: BaseException | str) -> dict[str, Any]:
    record["error_class"] = type(exc).__name__ if isinstance(exc, BaseException) else str(exc)[:80]
    return record


def apply_chain_hint(record: dict[str, Any], provider) -> dict[str, Any]:
    """Absorb a ChainedFallbackProvider.last_attribution hint when present."""
    hint = getattr(provider, "last_attribution", None)
    if isinstance(hint, dict) and hint.get("provider"):
        record["provider"] = str(hint["provider"])
        record["model_served"] = str(hint.get("model") or hint["provider"])
        try:
            record["fallback_depth"] = int(hint.get("depth", 0))
        except (TypeError, ValueError):
            record["fallback_depth"] = 0
        return record
    return note_single_provider(record, provider)


def summarize_winners(stages: dict[str, dict[str, Any]]) -> str:
    """Compact manifest string: stage=winner/model (no secrets)."""
    parts = []
    for stage in sorted(stages):
        rec = stages[stage] or {}
        parts.append(f"{stage}:{(rec.get('provider') or '?')}/{(rec.get('model_served') or '?')}")
    return "; ".join(parts) or "none"


#: Providers served from local endpoints (LM Studio workstation, Pi).
LOCAL_PROVIDERS = frozenset({"LM Studio", "llama.cpp", "Ollama"})

#: Providers served over public APIs.
CLOUD_PROVIDERS = frozenset({"Gemini", "Mistral", "OpenRouter", "OpenCodeZen"})


def stage_origin(provider_name: str | None) -> str:
    """Where a stage was served: 'local' | 'API' | 'Python' | 'none'."""
    name = str(provider_name or "")
    if name in LOCAL_PROVIDERS:
        return "local"
    if name in CLOUD_PROVIDERS:
        return "API"
    if name == "deterministic" or name.startswith("deterministic("):
        return "Python"
    return "none"


def format_stage_line(stage: str, rec: dict[str, Any] | None) -> str:
    """One human-readable line: which AI served a stage, model, and where.

    Example: ``decision: Gemini/gemini-2.5-flash (API, depth 1)``.
    Skills served deterministically render as
    ``risk: deterministic (Python)``. Never raises.
    """
    try:
        rec = rec or {}
        provider = str(rec.get("provider") or "unknown")
        served = str(rec.get("model_served") or provider)
        if provider == "deterministic" or provider.startswith("deterministic("):
            return f"{stage}: deterministic (Python)"
        try:
            depth = int(rec.get("fallback_depth", 0))
        except (TypeError, ValueError):
            depth = 0
        return f"{stage}: {provider}/{served} ({stage_origin(provider)}, depth {depth})"
    except Exception:
        return f"{stage}: unknown"
