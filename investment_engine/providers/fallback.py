from __future__ import annotations

import logging
from typing import Any

from .base import BaseLLMProvider

logger = logging.getLogger(__name__)


class FallbackProvider(BaseLLMProvider):
    """Wrap a primary provider with a fallback provider."""

    def __init__(self, primary: BaseLLMProvider, fallback: BaseLLMProvider) -> None:
        self.primary = primary
        self.fallback = fallback

    @property
    def name(self) -> str:
        return f"Fallback({self.primary.name}/{self.fallback.name})"

    def generate(self, prompt: str, *, stage: str, context: dict | None = None) -> str:
        # Use each provider's own default model - don't pass model from context to primary
        primary_context = {k: v for k, v in (context or {}).items() if k != "model"}
        fallback_context = {k: v for k, v in (context or {}).items() if k != "model"}
        
        try:
            primary_result = self.primary.generate(prompt, stage=stage, context=primary_context)
            logger.debug("FallbackProvider: primary (%s) succeeded", self.primary.name)
            return primary_result
        except Exception as exc:
            # Primary provider raised an exception - fall back
            logger.warning("FallbackProvider: primary (%s) failed: %s. Trying fallback (%s)", 
                          self.primary.name, exc, self.fallback.name)
            try:
                return self.fallback.generate(prompt, stage=stage, context=fallback_context)
            except Exception as fallback_exc:
                logger.error("FallbackProvider: fallback (%s) also failed: %s", 
                            self.fallback.name, fallback_exc)
                raise
