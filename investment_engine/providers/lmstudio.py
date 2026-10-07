from __future__ import annotations

import logging
import threading
import time

import requests

from .base import BaseLLMProvider
from investment_engine.providers.presets import merge_sampling, preset_for

logger = logging.getLogger(__name__)

# Global lock for model warm-up
_warmup_lock = threading.Lock()
_model_warmed = set()
_model_failed = set()  # Cache models that failed to load

# Pre-flight cache: base_url -> (timestamp, frozenset(ids) | None).
# None means "unknown" (endpoint unreachable) — never a reason to skip.
_server_models_cache: dict[str, tuple[float, frozenset | None]] = {}
_SERVER_MODELS_TTL_S = 120.0

# Model loads on LM Studio take 6-60s (large models). The warmup read budget
# must cover the load, otherwise our own disconnect aborts the server-side
# startup ("Client disconnected. Stopping generation...") and every retry
# fails the same way. Seen live 2026-09-28: qwen (~6s load) passed with a
# 10s budget while slow loaders never finished in time.
_WARMUP_CONNECT_S = 15
_WARMUP_READ_S = 120


def server_model_ids(base_url: str) -> frozenset | None:
    """Best-effort ids from GET /v1/models. None = unknown, proceed as before.

    Never raises. Cached briefly so per-stage pre-flights cost one request.
    """
    base = (base_url or "").rstrip("/")
    now = time.time()
    hit = _server_models_cache.get(base)
    if hit is not None and (now - hit[0]) < _SERVER_MODELS_TTL_S:
        return hit[1]
    try:
        r = requests.get(f"{base}/models", timeout=(_WARMUP_CONNECT_S, 15))
        if r.status_code == 200:
            data = r.json().get("data") or []
            ids = frozenset(str(d.get("id", "")) for d in data
                             if isinstance(d, dict) and d.get("id"))
            _server_models_cache[base] = (now, ids)
            return ids
    except Exception as exc:
        logger.debug("LM Studio /v1/models unreachable on %s: %s", base, exc)
    _server_models_cache[base] = (now, None)
    return None


def reset_server_models_cache() -> None:
    """Clear the pre-flight cache (tests, server model changes)."""
    _server_models_cache.clear()


class LMStudioProvider(BaseLLMProvider):
    """LM Studio provider using the configured local endpoint (OpenAI-compatible)."""

    def __init__(
        self,
        base_url: str = "http://100.88.219.120:1234/v1",
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
            # Pre-flight: skip models the server does not serve instead of
            # burning a warmup + 30s retry + failed generation on each stage.
            # Transient (NOT cached in _model_failed): the user may load the
            # model into LM Studio mid-run and the next stage retries.
            known = server_model_ids(self.base_url)
            if known is not None and model not in known:
                raise RuntimeError(
                    f"Model '{model}' not served by LM Studio "
                    f"(server has: {sorted(known) or 'none'})")
            logger.info("Warming up model %s on %s...", model, self.base_url)
            _BUSY_MARKERS = (
                "already loaded",
                "already loaded",
                "model is currently loaded",
                "model is currently in use",
                "currently in use",
                "currently in use",
                "is busy",
                "model is busy",
                "server is busy",
            )
            _MAX_WARMUP_RETRIES = 3
            _WARMUP_RETRY_DELAY_S = 5.0

            for attempt in range(_MAX_WARMUP_RETRIES):
                try:
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
                        timeout=(_WARMUP_CONNECT_S, _WARMUP_READ_S),
                    )
                    if r.status_code == 200:
                        _model_warmed.add(key)
                        logger.info("Model %s warmed up successfully", model)
                        return
                    elif r.status_code == 400 and "Failed to load model" in r.text:
                        # Model cannot be loaded - fail fast so fallback can trigger
                        _model_failed.add(key)
                        error_msg = f"Model '{model}' failed to load: {r.text[:500]}"
                        logger.error(error_msg)
                        raise RuntimeError(error_msg)
                    elif r.status_code in (400, 503) and any(m in (r.text or "").lower() for m in _BUSY_MARKERS):
                        # Model busy / already loaded / server busy — wait and retry
                        if attempt < _MAX_WARMUP_RETRIES - 1:
                            logger.warning(
                                "Model %s busy (%s), waiting %.1fs before retry (%d/%d)",
                                model, r.text[:200], _WARMUP_RETRY_DELAY_S, attempt + 1, _MAX_WARMUP_RETRIES
                            )
                            time.sleep(_WARMUP_RETRY_DELAY_S)
                            continue
                        # Last attempt exhausted — treat as transient, don't cache
                        logger.warning(
                            "Model %s busy after %d retries, treating as transient",
                            model, _MAX_WARMUP_RETRIES
                        )
                    else:
                        # Transient (cold load, 5xx, ...): do NOT cache — the model
                        # may become ready by the next stage. Only the definitive
                        # 400 "Failed to load model" above is cached permanently.
                        logger.warning("Model warmup returned %s: %s", r.status_code, r.text[:200])
                    return
                except RuntimeError:
                    raise
                except Exception as e:
                    # Timeouts / connection errors while the model is (cold) loading
                    # must not poison the model for the whole run — next stage
                    # retries the warmup instead of falling back immediately.
                    logger.warning("Model warmup failed (transient, will retry): %s", e)

    def _server_root(self) -> str:
        """Server root without the /v1 suffix (for management endpoints)."""
        root = self.base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[: -len("/v1")]
        return root.rstrip("/")

    def unload_model(self, model: str) -> bool:
        """Best-effort unload of one model to free VRAM. Never raises.

        Tries known LM Studio management endpoint variants; servers without
        unload support just log a warning (use the app's auto-evict setting
        as the reliable path there). Also clears local warmup caches.
        """
        key = (self.base_url, model)
        _model_warmed.discard(key)
        _model_failed.discard(key)
        return _unload_via_api(self.base_url, model)

    def generate(self, prompt: str, *, stage: str, context: dict | None = None) -> str:
        requested_output_tokens = (context or {}).get("max_output_tokens", self.max_output_tokens)
        requested_model = str((context or {}).get("model", self.model))
        # Thinking models (30B) need room past the 5min default read budget.
        try:
            read_timeout = int((context or {}).get("read_timeout_s", 300) or 300)
        except (TypeError, ValueError):
            read_timeout = 300
        
        # Warm up the model on first use. A pre-flight/warmup RuntimeError
        # becomes a chain-visible error string (not an exception) so the
        # caller falls through to the next link instead of failing the stage.
        try:
            self._warmup_model(requested_model)
        except RuntimeError as exc:
            return f"[{stage}] LM Studio model '{requested_model}' unavailable: {exc}."
        
        preset = preset_for(stage)
        sampling = merge_sampling(preset, context)
        max_tokens = min(int(requested_output_tokens), self.max_output_tokens)
        floor = int(preset.get("min_tokens_floor", 0) or 0)
        if floor > max_tokens:
            # Reasoning trace needs budget or the answer comes back empty.
            max_tokens = min(floor, self.max_output_tokens)
        payload = {
            "model": requested_model,
            "messages": [
                {"role": "system", "content": f"You are the {stage} stage of the investment engine."},
                {"role": "user", "content": prompt},
            ],
            "temperature": sampling["temperature"],
            "top_p": sampling["top_p"],
            "top_k": sampling["top_k"],
            "repeat_penalty": sampling["repeat_penalty"],
            "max_tokens": max_tokens,
            "stream": False,
        }

        try:
            logger.info("LM Studio request: stage=%s model=%s", stage, requested_model)
            response = requests.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                timeout=(30, read_timeout),  # 30s connect, stage read budget
            )
            if response.status_code == 400:
                # Model is likely (cold) loading after a switch — the server
                # answers 400 instead of queueing. Wait, re-warmup, retry once.
                logger.warning(
                    "LM Studio 400 at stage=%s (likely model loading), "
                    "waiting 30s and retrying once: %s",
                    stage, (response.text or "")[:200])
                time.sleep(30)
                try:
                    self._warmup_model(requested_model)
                except Exception:
                    pass
                response = requests.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    timeout=(30, read_timeout),
                )
            logger.info("LM Studio response: stage=%s status=%s", stage, response.status_code)
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices") or []
            if not choices:
                return f"[{stage}] LM Studio returned no choices."
            message = choices[0].get("message", {})
            # NOTE: reasoning_content is chain-of-thought, NEVER report content.
            # Observed 2026-09-28: qwen fills the whole budget with reasoning
            # and returns content="" — dumping that into reports produced
            # thinking-process briefs. Empty content is an error so the chain
            # falls back instead of publishing the model's inner monologue.
            content = message.get("content") or ""
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


def _unload_via_api(base_url: str, model: str) -> bool:
    """Try known unload endpoint variants. Never raises. Returns True on success."""
    base = (base_url or "").rstrip("/")
    root = base[: -len("/v1")] if base.endswith("/v1") else base
    root = root.rstrip("/")
    candidates = [
        ("POST", f"{root}/api/v0/models/unload", {"model": model}),
        ("POST", f"{base}/models/unload", {"model": model}),
        ("DELETE", f"{base}/models/{model}", None),
    ]
    for method, url, payload in candidates:
        try:
            if method == "DELETE":
                r = requests.delete(url, timeout=(5, 15))
            else:
                r = requests.post(url, json=payload, timeout=(5, 15))
            body = (r.text or "")[:200]
            if r.status_code == 200 and "Unexpected endpoint" not in body:
                logger.info("Unloaded model %s via %s %s", model, method, url)
                return True
            logger.debug("Unload variant %s %s unsupported: %s %s",
                         method, url, r.status_code, body)
        except Exception as e:
            logger.debug("Unload variant %s %s failed: %s", method, url, e)
    logger.warning(
        "Model %s NOT unloaded: server has no unload API. "
        "Enable auto-evict in LM Studio app settings to free VRAM.", model)
    return False


def unload_lmstudio_models(base_url: str, models: list[str]) -> dict[str, bool]:
    """Best-effort unload of several models. Never raises. Returns {model: ok}."""
    out: dict[str, bool] = {}
    for model in dict.fromkeys(models):  # dedupe, keep order
        try:
            out[model] = _unload_via_api(base_url, model)
        except Exception:
            out[model] = False
    return out