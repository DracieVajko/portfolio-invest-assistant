"""Model presets: tuned sampling params per pipeline role.

Roles (mirrors _model_for_stage in investment_engine.main):
- REASONING: deep analysis stages (decision, discovery, pie eval,
  ai_recommendations). Tuned for qwen3.8-9b (was deepseek-r1-finance-14b,
  retired for reasoning-trace leakage into strict-JSON stages).
- FINANCE: finance-specialist stages (decision, ai_recommendations).
  Served by fin-o1-14b; same reasoning-friendly sampling as REASONING.
- THINKING: open research (discovery). Served by the 30B-A3B MoE
  (tongyi-deepresearch); generous output floor, longer reads tolerated.
- SUMMARY: faithful compression (summary stage). Tuned for gemma-4-12b.

All fields verified accepted (HTTP 200) by the LM Studio server; unknown
fields would be silently ignored, so the payload only carries these keys.
Explicit per-call context values (temperature/top_p/top_k/repeat_penalty)
always win over the preset.
"""

from __future__ import annotations

# Stages that compress instead of reason. Single source of truth —
# investment_engine.main imports this (do not duplicate the set).
WRITER_STAGES = frozenset({"summary"})

# Open-research stages served by the thinking model (30B-A3B MoE).
THINKING_STAGES = frozenset({"discovery"})

# Finance-specialist stages served by fin-o1-14b (reasoning-friendly).
FINANCE_STAGES = frozenset({"decision", "ai_recommendations", "decision_detail"})

# Reasoning models (R1 family) degrade with near-zero temperature:
# DeepSeek's guidance is temperature ~0.6. A low cap also truncates the
# thinking trace into an empty answer, hence the output floor.
REASONING_PRESET: dict = {
    "temperature": 0.6,
    "top_p": 0.95,
    "top_k": 40,
    "repeat_penalty": 1.0,  # off: penalties can break reasoning chains
    "min_tokens_floor": 2048,  # reasoning trace needs budget (else empty content)
}

# Summaries must stay faithful and tight: low temperature, mild anti-loop.
SUMMARY_PRESET: dict = {
    "temperature": 0.2,
    "top_p": 0.9,
    "top_k": 40,
    "repeat_penalty": 1.05,
    "min_tokens_floor": 0,  # stage token budget is enough for compression
}

# Thinking/research (30B-A3B MoE): reasoning-friendly, room to think.
THINKING_PRESET: dict = {
    "temperature": 0.6,
    "top_p": 0.95,
    "top_k": 40,
    "repeat_penalty": 1.0,
    "min_tokens_floor": 2048,
}

# Payload keys the LM Studio server accepts (probed: all HTTP 200).
SAMPLING_KEYS = ("temperature", "top_p", "top_k", "repeat_penalty")


def preset_for(stage: str) -> dict:
    """Return a copy of the preset for a pipeline stage."""
    if stage in WRITER_STAGES:
        return dict(SUMMARY_PRESET)
    if stage in THINKING_STAGES:
        return dict(THINKING_PRESET)
    return dict(REASONING_PRESET)  # FINANCE stages share reasoning sampling


def merge_sampling(preset: dict, context: dict | None) -> dict:
    """Merge explicit per-call context sampling over the preset.

    Only SAMPLING_KEYS are taken from context; everything else ignored.
    """
    merged = dict(preset)
    for key in SAMPLING_KEYS:
        value = (context or {}).get(key)
        if value is not None:
            merged[key] = value
    return merged
