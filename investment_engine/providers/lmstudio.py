from __future__ import annotations

import logging
import threading
import time

from .base import BaseLLMProvider

logger = logging.getLogger(__name__)

# Global lock for model warm-up
_warmup_lock = threading.Lock()
_model_warmed = set()
_model_failed = set()  # Cache models that failed to load


class LMStudioProvider(BaseLLMProvider):
    """LM Studio provider using the configured local endpoint (OpenAI-compatible)."""

    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        model: str = "oda-fin-rl-8b",
        max_context_tokens: int = 32000,
        max_output_tokens: int = 8192,
        timeout_seconds: int = 7200,  # 2 hours for long model loading/generation
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_context_tokens = max_context_tokens
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds
        self._connect_timeout = 60  # seconds to establish connection
        self._read_timeout = 7200    # seconds to read response (model generation) - 2 hours

    @property
    def name(self) -> str:
        return "LM Studio"

    def _warmup_model(self, model: str) -> None:
        """Warm up the model with a simple request to ensure it's loaded."""
        key = (self.base_url, model)
        if key in _model_warmed:
            return
        if key in _model_failed:
            raise RuntimeError(f"Model '{model}' previously failed to load")
        with _warmup_lock:
            if key in _model_warmed:
                return
            if key in _model_failed:
                raise RuntimeError(f"Model '{model}' previously failed to load")
            logger.info("Warming up model %s on %s...", model, self.base_url)
            try:
                import requests
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 5,
                    "temperature": 0.1,
                    "stream": False,
                }
                r = requests.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    timeout=(5, 10),  # Fail fast: 5s connect, 10s read
                )
                if r.status_code == 200:
                    _model_warmed.add(key)
                    logger.info("Model %s warmed up successfully", model)
                elif r.status_code == 400 and "Failed to load model" in r.text:
                    # Model cannot be loaded - fail fast so fallback can trigger
                    _model_failed.add(key)
                    error_msg = f"Model '{model}' failed to load: {r.text[:500]}"
                    logger.error(error_msg)
                    raise RuntimeError(error_msg)
                else:
                    logger.warning("Model warmup returned %s: %s", r.status_code, r.text[:200])
                    _model_failed.add(key)  # Cache other failures too
            except RuntimeError:
                raise
            except Exception as e:
                logger.warning("Model warmup failed: %s", e)
                _model_failed.add(key)  # Cache timeout/connection failures

    def generate(self, prompt: str, *, stage: str, context: dict | None = None) -> str:
        requested_output_tokens = (context or {}).get("max_output_tokens", self.max_output_tokens)
        requested_model = str((context or {}).get("model", self.model))
        
        # Warm up the model on first use
        self._warmup_model(requested_model)
        
        payload = {
            "model": requested_model,
            "messages": [
                {"role": "system", "content": f"You are the {stage} stage of the investment engine."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": min(int(requested_output_tokens), self.max_output_tokens),
            "stream": False,
        }

        try:
            import requests

            logger.info("LM Studio request: stage=%s model=%s", stage, requested_model)
            response = requests.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                timeout=(30, 300),  # 30s connect, 5min read
            )
            logger.info("LM Studio response: stage=%s status=%s", stage, response.status_code)
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices") or []
            if not choices:
                return f"[{stage}] LM Studio returned no choices."
            message = choices[0].get("message", {})
            # Some models (e.g., qwen3) put response in reasoning_content instead of content
            content = message.get("content") or message.get("reasoning_content") or ""
            content = str(content or "").strip()
            # Strip <answer> tags if present (some models wrap JSON in <answer> tags)
            if content.startswith("<answer>") and content.endswith("</answer>"):
                content = content[8:-9].strip()
            # Strip markdown code fences if present (some models wrap JSON in ```json ... ```)
            if content.startswith("```"):
                lines = content.split('\n')
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                content = '\n'.join(lines).strip()
            return content.strip() or f"[{stage}] LM Studio returned an empty response."
        except requests.exceptions.HTTPError as exc:
            logger.exception("LM Studio generation failed at stage=%s", stage)
            raise
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.exception("LM Studio generation failed at stage=%s", stage)
            raise