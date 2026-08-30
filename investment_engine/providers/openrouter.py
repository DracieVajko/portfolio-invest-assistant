from __future__ import annotations

import os
import re
import time
from typing import Any

import requests

from .base import BaseLLMProvider


def _clean_llm_output(content: str) -> str:
    """Clean LLM output: strip <answer> tags, markdown fences, chain-of-thought."""
    if not content:
        return ""
    content = str(content).strip()
    # Strip <answer>...</answer> tags
    if content.startswith("<answer>") and content.endswith("</answer>"):
        content = content[8:-9].strip()
    # Strip markdown code fences
    if content.startswith("```"):
        lines = content.split('\n')
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        content = '\n'.join(lines).strip()
    # Strip chain-of-thought / thinking tags (some models use <think)
    content = re.sub(r'.*?', '', content, flags=re.DOTALL).strip()
    content = re.sub(r'<thinking>.*?</thinking>', '', content, flags=re.DOTALL).strip()
    return content


class OpenRouterProvider(BaseLLMProvider):
    """OpenRouter-compatible provider for the modular investment engine."""

    @property
    def name(self) -> str:
        return "OpenRouter"

    def __init__(self, base_url: str = "https://openrouter.ai/api/v1", model: str = "openai/gpt-oss-20b", api_key: str | None = None, max_context_tokens: int = 62000, max_output_tokens: int = 8192) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        self.max_context_tokens = max_context_tokens
        self.max_output_tokens = max_output_tokens

    def generate(self, prompt: str, *, stage: str, context: dict | None = None) -> str:
        if not self.api_key:
            return f"[{stage}] OpenRouter provider is not configured."

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": f"You are the {stage} stage of the investment engine."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.15,
            "max_tokens": min(self.max_output_tokens, 8192),
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        max_retries = 3
        base_delay = 2
        for attempt in range(max_retries):
            try:
                response = requests.post(f"{self.base_url}/chat/completions", json=payload, headers=headers, timeout=120)
                if response.status_code == 429:
                    # Rate limited - wait and retry
                    delay = base_delay * (2 ** attempt)
                    time.sleep(delay)
                    continue
                response.raise_for_status()
                data = response.json()
                choices = data.get("choices") or []
                if not choices:
                    return f"[{stage}] OpenRouter returned no choices."
                message = choices[0].get("message", {})
                content = message.get("content") or message.get("reasoning") or ""
                return _clean_llm_output(content) or f"[{stage}] OpenRouter returned an empty response."
            except requests.exceptions.Timeout:
                if attempt == max_retries - 1:
                    return f"[{stage}] OpenRouter timeout after {max_retries} attempts"
                time.sleep(base_delay * (2 ** attempt))
            except Exception as exc:
                if attempt == max_retries - 1:
                    return f"[{stage}] OpenRouter error: {exc}"
                time.sleep(base_delay * (2 ** attempt))
        
        return f"[{stage}] OpenRouter failed after {max_retries} attempts"
