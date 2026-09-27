"""Phase 5 source tiers and decision gating."""

from __future__ import annotations

# T0 broker / official IR / exchange notices. T1 major wire + regulators.
# T2 established financial press + data pages. T3 social/forums/ideas.
TIER_OFFICIAL = 0
TIER_WIRE = 1
TIER_PRESS = 2
TIER_SOCIAL = 3

DECISION_TIERS = frozenset({0, 1, 2})

# Categories that are always LOW_CONFIDENCE sentiment-only (never actionable).
SOCIAL_CATEGORIES = frozenset({"reddit", "social", "stocktwits", "tradingview_ideas", "x"})


def tier_for_category(category: str, explicit_tier: int | None = None) -> int:
    """Social categories are ALWAYS T3 (explicit overrides cannot launder
    social content into the decision set). Otherwise explicit tier wins."""
    if str(category or "").strip().lower() in SOCIAL_CATEGORIES:
        return TIER_SOCIAL
    if explicit_tier is not None:
        try:
            return max(0, min(3, int(explicit_tier)))
        except (TypeError, ValueError):
            pass
    return TIER_PRESS


def may_enter_decision(tier: int, category: str) -> bool:
    """T0-T2 only; social categories never enter the decision set."""
    if str(category or "").strip().lower() in SOCIAL_CATEGORIES:
        return False
    return int(tier) in DECISION_TIERS
