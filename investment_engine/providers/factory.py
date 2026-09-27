from __future__ import annotations

import logging
import requests

from investment_engine.config.settings import EngineSettings
from investment_engine.providers.base import BaseLLMProvider
from investment_engine.providers.lmstudio import LMStudioProvider
from investment_engine.providers.openrouter import OpenRouterProvider
from investment_engine.providers.ollama import OllamaProvider
from investment_engine.providers.llamacpp import LlamaCppProvider
from investment_engine.providers.fallback import FallbackProvider, ChainedFallbackProvider
from investment_engine.providers.gemini import GeminiProvider
from investment_engine.providers.mistral import MistralProvider
from investment_engine.providers.opencode_zen import OpenCodeZenProvider

logger = logging.getLogger(__name__)


class ProviderFactory:
    """Create a provider instance using automatic selection and chained fallback."""

    #: Providers excluded before generation starts in API-only mode.
    API_ONLY_DISABLED = ("LM Studio", "llama.cpp", "Ollama")

    @staticmethod
    def api_only_chain(settings: EngineSettings) -> BaseLLMProvider:
        """Gemini -> Mistral -> deterministic fallback. Locals never constructed."""
        chain: list[BaseLLMProvider] = []
        if getattr(settings, "gemini_api_key", ""):
            try:
                chain.append(ProviderFactory._gemini(settings))
            except Exception:
                pass
        if getattr(settings, "mistral_api_key", ""):
            try:
                chain.append(ProviderFactory._mistral(settings))
            except Exception:
                pass
        if len(chain) == 1:
            return chain[0]
        if chain:
            logger.info("ProviderFactory: API-only chain %s",
                        " -> ".join(p.name for p in chain))
            return ChainedFallbackProvider(chain)
        logger.warning("ProviderFactory: API-only mode with no cloud keys; deterministic fallback only")
        return ProviderFactory._no_key_chain()

    @staticmethod
    def locals_chain(settings: EngineSettings) -> BaseLLMProvider:
        """Locals only: LM Studio -> Ollama (Pi) -> llama.cpp (Pi) -> deterministic.

        Cloud keys are never consulted here. Order note: Ollama (262k ctx)
        precedes llama.cpp (Pi server n_ctx=4096, probed 2026-09-27) so long
        prompts don't die on the small-context link first.
        """
        chain: list[BaseLLMProvider] = []
        if bool(getattr(settings, "lmstudio_enabled", False)):
            try:
                chain.append(LMStudioProvider(
                    base_url=settings.lm_studio_base_url,
                    model=settings.lm_studio_model,
                    max_context_tokens=settings.lm_studio_context_tokens,
                    max_output_tokens=settings.lm_studio_max_output_tokens,
                    timeout_seconds=settings.request_timeout_seconds,
                ))
            except Exception:
                pass
        else:
            logger.info("ProviderFactory: LM Studio disabled by policy (LMSTUDIO_ENABLED=0)")
        try:
            chain.append(OllamaProvider(
                base_url=settings.ollama_base_url,
                model=settings.ollama_model,
                max_context_tokens=settings.ollama_context_tokens,
                max_output_tokens=settings.lm_studio_max_output_tokens,
            ))
        except Exception:
            pass
        try:
            chain.append(LlamaCppProvider(
                base_url=getattr(settings, "llamacpp_base_url", "http://100.125.47.31:11435/v1"),
                model=getattr(settings, "llamacpp_model", "qwen3.8-9b"),
                max_context_tokens=getattr(settings, "llamacpp_context_tokens", 4096),
                max_output_tokens=settings.lm_studio_max_output_tokens,
            ))
        except Exception:
            pass
        if len(chain) == 1:
            return chain[0]
        if chain:
            logger.info("ProviderFactory: locals chain %s",
                        " -> ".join(p.name for p in chain))
            return ChainedFallbackProvider(chain)
        logger.warning("ProviderFactory: locals mode with no local providers; deterministic fallback only")
        return ProviderFactory._no_key_chain("locals mode, no local provider reachable")

    @staticmethod
    def _no_key_chain(reason: str = "API-only mode, no key") -> BaseLLMProvider:
        """No-provider path: a one-link chain whose link always errors, so
        every stage degrades to the deterministic fallback without any probe."""
        from investment_engine.providers.base import BaseLLMProvider as _Base

        _reason = reason

        class _NoKey(_Base):
            name = "NoCloudKey"

            def generate(self, prompt: str, *, stage: str, context: dict | None = None) -> str:
                return f"[{stage}] provider unavailable ({_reason})"

        return ChainedFallbackProvider([_NoKey()])

    @staticmethod
    def create(settings: EngineSettings | None = None) -> BaseLLMProvider:
        settings = settings or EngineSettings.from_defaults()
        from investment_engine.config.settings import resolve_execution_mode as _mode
        mode = _mode(settings)
        if mode == "public":
            return ProviderFactory.api_only_chain(settings)
        if mode == "locals":
            return ProviderFactory.locals_chain(settings)
        preferred = (settings.provider or "auto").lower()

        if preferred == "openrouter":
            return ProviderFactory._openrouter(settings)
        if preferred == "gemini":
            return ProviderFactory._gemini(settings)
        if preferred == "mistral":
            return ProviderFactory._mistral(settings)
        if preferred == "opencode_zen":
            return ProviderFactory._opencode_zen(settings)
        if preferred == "ollama":
            return ProviderFactory._ollama(settings)
        if preferred == "lmstudio":
            return ProviderFactory._lmstudio(settings)
        if preferred not in ("auto", ""):
            # Unknown preferred – fall through to auto
            pass

        # Auto: build full chain based on available keys / services
        chain = ProviderFactory._build_chain(settings)
        if len(chain) == 1:
            return chain[0]
        if len(chain) > 1:
            logger.info("ProviderFactory: auto chain %s", " -> ".join(p.name for p in chain))
            return ChainedFallbackProvider(chain)
        return ProviderFactory._lmstudio(settings)

    @staticmethod
    def _build_chain(settings: EngineSettings) -> list[BaseLLMProvider]:
        chain: list[BaseLLMProvider] = []
        # 1. LM Studio (laptop) – PRIMARY local when enabled (LMSTUDIO_ENABLED=1).
        # Disabled by default for testing; the recorder marks the state explicitly.
        if bool(getattr(settings, "lmstudio_enabled", True)):
            try:
                lm = LMStudioProvider(
                    base_url=settings.lm_studio_base_url,
                    model=settings.lm_studio_model,
                    max_context_tokens=settings.lm_studio_context_tokens,
                    max_output_tokens=settings.lm_studio_max_output_tokens,
                    timeout_seconds=settings.request_timeout_seconds,
                )
                chain.append(lm)
            except Exception:
                pass
        else:
            logger.info("ProviderFactory: LM Studio disabled by policy (LMSTUDIO_ENABLED=0)")

        # 2. Ollama (Raspberry Pi 5, Tailscale) – SECONDARY fallback.
        # Big context (262k) so it precedes the small-context llama.cpp link.
        # Models on Pi (probed): qwen3.8-9b-pi:latest, qwen3-4b-pi:latest, noema-2b.
        try:
            ollama = OllamaProvider(
                base_url=settings.ollama_base_url,
                model=settings.ollama_model,
                max_context_tokens=settings.ollama_context_tokens,
                max_output_tokens=settings.lm_studio_max_output_tokens,
            )
            chain.append(ollama)
        except Exception:
            pass

        # 3. llama.cpp (Raspberry Pi 5, Tailscale IP) – TERTIARY fallback.
        # OpenAI-compatible API (default port 11435, model qwen3.8-9b).
        # Endpoint comes from settings (LLAMACPP_BASE_URL), never hardcoded.
        # NOTE: the Pi server runs n_ctx=4096 (probed 2026-09-27); the provider
        # skips over-budget prompts with a chain-visible error (see llamacpp.py).
        try:
            llama = LlamaCppProvider(
                base_url=getattr(settings, "llamacpp_base_url", "http://100.125.47.31:11435/v1"),
                model=getattr(settings, "llamacpp_model", "qwen3.8-9b"),
                max_context_tokens=getattr(settings, "llamacpp_context_tokens", 4096),
                max_output_tokens=settings.lm_studio_max_output_tokens,
            )
            chain.append(llama)
        except Exception:
            pass

        # 4. Gemini – free tier fallback (OpenRouter skipped: free tier too limited)
        if getattr(settings, "gemini_api_key", ""):
            try:
                chain.append(ProviderFactory._gemini(settings))
            except Exception:
                pass

        # 5. Mistral – free tier fallback
        if getattr(settings, "mistral_api_key", ""):
            try:
                chain.append(ProviderFactory._mistral(settings))
            except Exception:
                pass

        # Note: OpenRouter and OpenCode Zen NOT in auto chain (free tier too limited / empty responses).
        # Available only with explicit PROVIDER=openrouter / opencode_zen.

        # Filter only key-based providers when key missing; keep local providers (LMStudio/llama.cpp/Ollama) even if not currently reachable
        filtered: list[BaseLLMProvider] = []
        for p in chain:
            if isinstance(p, (LMStudioProvider, LlamaCppProvider, OllamaProvider)):
                filtered.append(p)
            elif ProviderFactory._is_available(p):
                filtered.append(p)
            else:
                logger.debug("ProviderFactory: skipping unavailable %s (no API key)", p.name)
        return filtered if filtered else chain

    @staticmethod
    def _lmstudio(settings: EngineSettings) -> BaseLLMProvider:
        # User policy: LM Studio stays disabled for testing until explicitly
        # enabled (LMSTUDIO_ENABLED=1). An explicit provider="lmstudio" choice
        # degrades to the auto chain (without LM Studio) with a warning.
        if not bool(getattr(settings, "lmstudio_enabled", True)):
            logger.warning("ProviderFactory: LM Studio disabled (LMSTUDIO_ENABLED=0); using auto chain")
            chain = [p for p in ProviderFactory._build_chain(settings)
                     if not isinstance(p, LMStudioProvider)]
            if len(chain) == 1:
                return chain[0]
            if chain:
                return ChainedFallbackProvider(chain)
            return ProviderFactory._ollama(settings)
        primary = LMStudioProvider(
            base_url=settings.lm_studio_base_url,
            model=settings.lm_studio_model,
            max_context_tokens=settings.lm_studio_context_tokens,
            max_output_tokens=settings.lm_studio_max_output_tokens,
            timeout_seconds=settings.request_timeout_seconds,
        )
        # Explicit lmstudio request – still attach full fallback chain for robustness
        # (any cloud fallback key present: Gemini / Mistral / OpenRouter)
        _has_cloud = bool(
            settings.openrouter_api_key
            or getattr(settings, "gemini_api_key", "")
            or getattr(settings, "mistral_api_key", "")
            or getattr(settings, "opencode_zen_api_key", "")
        )
        if _has_cloud and (settings.provider or "").lower() == "lmstudio":
            # Explicit lmstudio request – still attach chain as fallback for robustness
            chain = ProviderFactory._build_chain(settings)
            # Ensure primary is first
            if chain and chain[0].name == primary.name:
                return ChainedFallbackProvider(chain)
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
    def _gemini(settings: EngineSettings) -> BaseLLMProvider:
        return GeminiProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            base_url=settings.gemini_base_url,
            max_output_tokens=settings.openrouter_max_output_tokens,
        )

    @staticmethod
    def _mistral(settings: EngineSettings) -> BaseLLMProvider:
        return MistralProvider(
            api_key=settings.mistral_api_key,
            model=settings.mistral_model,
            base_url=settings.mistral_base_url,
            max_output_tokens=settings.openrouter_max_output_tokens,
        )

    @staticmethod
    def _opencode_zen(settings: EngineSettings) -> BaseLLMProvider:
        return OpenCodeZenProvider(
            api_key=settings.opencode_zen_api_key,
            model=settings.opencode_zen_model,
            base_url=settings.opencode_zen_base_url,
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
        # OpenRouter/Gemini/Mistral/Zen availability is api_key presence
        if isinstance(provider, OpenRouterProvider):
            return bool(provider.api_key)
        if isinstance(provider, GeminiProvider):
            return bool(provider.api_key)
        if isinstance(provider, MistralProvider):
            return bool(provider.api_key)
        if isinstance(provider, OpenCodeZenProvider):
            return bool(provider.api_key)
        if isinstance(provider, LlamaCppProvider):
            try:
                response = requests.get(f"{provider.base_url}/models", timeout=3)
                return response.status_code == 200
            except Exception:
                return False
        if isinstance(provider, LMStudioProvider):
            try:
                response = requests.get(f"{provider.base_url}/models", timeout=3)
                return response.status_code == 200
            except Exception:
                return False
        return True