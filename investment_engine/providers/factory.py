from __future__ import annotations

import logging
import requests

from investment_engine.config.settings import EngineSettings
from investment_engine.providers.base import BaseLLMProvider
from investment_engine.providers.lmstudio import LMStudioProvider
from investment_engine.providers.openrouter import OpenRouterProvider
from investment_engine.providers.ollama import OllamaProvider
from investment_engine.providers.fallback import FallbackProvider

logger = logging.getLogger(__name__)


class ProviderFactory:
    """Create a provider instance using automatic selection and fallback."""

    @staticmethod
    def create(settings: EngineSettings | None = None) -> BaseLLMProvider:
        settings = settings or EngineSettings.from_defaults()
        preferred = (settings.provider or "auto").lower()

        if preferred == "openrouter":
            return ProviderFactory._openrouter(settings)
        if preferred == "ollama":
            return ProviderFactory._ollama(settings)
        if preferred == "lmstudio":
            return ProviderFactory._lmstudio(settings)

        for candidate in ("lmstudio", "openrouter", "ollama"):
            try:
                if candidate == "openrouter":
                    provider = ProviderFactory._openrouter(settings)
                elif candidate == "ollama":
                    provider = ProviderFactory._ollama(settings)
                else:
                    provider = ProviderFactory._lmstudio(settings)
                if ProviderFactory._is_available(provider):
                    return provider
            except Exception:
                continue

        return ProviderFactory._lmstudio(settings)

    @staticmethod
    def _lmstudio(settings: EngineSettings) -> BaseLLMProvider:
        primary = LMStudioProvider(
            base_url=settings.lm_studio_base_url,
            model=settings.lm_studio_model,
            max_context_tokens=settings.lm_studio_context_tokens,
            max_output_tokens=settings.lm_studio_max_output_tokens,
            timeout_seconds=settings.request_timeout_seconds,
        )
        logger.info("ProviderFactory: openrouter_api_key present=%s", bool(settings.openrouter_api_key))
        if settings.openrouter_api_key:
            fallback = ProviderFactory._openrouter(settings)
            logger.info("ProviderFactory: Created FallbackProvider(LMStudio -> OpenRouter)")
            return FallbackProvider(primary=primary, fallback=fallback)
        logger.warning("ProviderFactory: No OpenRouter API key, using LMStudio only")
        return primary

    @staticmethod
    def _openrouter(settings: EngineSettings) -> BaseLLMProvider:
        return OpenRouterProvider(
            base_url=settings.openrouter_base_url,
            model=settings.openrouter_model,
            api_key=settings.openrouter_api_key,
            max_context_tokens=settings.openrouter_context_tokens,
            max_output_tokens=settings.openrouter_max_output_tokens,
        )

    @staticmethod
    def _ollama(settings: EngineSettings) -> BaseLLMProvider:
        primary = OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            max_context_tokens=settings.ollama_context_tokens,
            max_output_tokens=settings.lm_studio_max_output_tokens,
        )
        if settings.openrouter_api_key:
            fallback = ProviderFactory._openrouter(settings)
            return FallbackProvider(primary=primary, fallback=fallback)
        return primary

    @staticmethod
    def _is_available(provider: BaseLLMProvider) -> bool:
        if isinstance(provider, OpenRouterProvider):
            return bool(provider.api_key)
        if isinstance(provider, LMStudioProvider):
            try:
                response = requests.get(f"{provider.base_url}/models", timeout=5)
                return response.status_code == 200
            except Exception:
                return False
        return True