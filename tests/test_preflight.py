"""Engine-start pre-flight: dead local endpoints are skipped for the whole run.

Strictly offline: requests is monkeypatched, no HTTP, no endpoints.
(User rule: tests use API/offline only.)
"""
from __future__ import annotations

from types import SimpleNamespace


def _live_settings(**kw):
    base = dict(
        lmstudio_enabled=True,
        lm_studio_base_url="http://127.0.0.1:9/v1",
        llamacpp_base_url="http://127.0.0.1:9/v1",
        ollama_base_url="http://127.0.0.1:9",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_preflight_skips_all_dead_locals(monkeypatch):
    import requests

    import investment_engine.main as m
    from investment_engine.providers import fallback as fb

    fb.reset_circuit_breaker()
    try:
        monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no route")))
        m._note_unreachable_local_providers(_live_settings())
        assert fb.is_skipped("LM Studio")
        assert fb.is_skipped("llama.cpp")
        assert fb.is_skipped("Ollama")
    finally:
        fb.reset_circuit_breaker()


def test_preflight_keeps_reachable_locals(monkeypatch):
    import requests

    import investment_engine.main as m
    from investment_engine.providers import fallback as fb

    class _Resp:
        status_code = 200

    fb.reset_circuit_breaker()
    try:
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
        m._note_unreachable_local_providers(_live_settings())
        assert not fb.is_skipped("LM Studio")
        assert not fb.is_skipped("llama.cpp")
        assert not fb.is_skipped("Ollama")
    finally:
        fb.reset_circuit_breaker()


def test_try_lmstudio_stage_honors_preflight_skip():
    import investment_engine.main as m
    from investment_engine.providers import fallback as fb
    from investment_engine.providers.fallback import ChainedFallbackProvider
    from investment_engine.providers.lmstudio import LMStudioProvider

    class _StubLM(LMStudioProvider):
        def __init__(self):
            self.calls = []

        @property
        def name(self):
            return "LM Studio"

        def generate(self, prompt, *, stage, context=None):
            self.calls.append(prompt)
            raise AssertionError("must not be called while skipped")

    fb.reset_circuit_breaker()
    try:
        stub = _StubLM()
        fb.skip_for_run("LM Studio", "test")
        out = m._try_lmstudio_stage(
            ChainedFallbackProvider([stub]), "p",
            settings={"lmstudio_enabled": True, "decision_model": "D",
                      "writer_model": "W", "finance_model": "F",
                      "thinking_model": "T"},
            stage="decision", tokens=10)
        assert out == (None, None)
        assert stub.calls == []
    finally:
        fb.reset_circuit_breaker()


def test_chain_skips_preflight_tripped_link():
    from investment_engine.providers.base import BaseLLMProvider
    from investment_engine.providers import fallback as fb
    from investment_engine.providers.fallback import ChainedFallbackProvider

    class _Dead(BaseLLMProvider):
        name = "LM Studio"

        def generate(self, prompt, *, stage, context=None):
            raise AssertionError("tripped link must not be called")

    class _Live(BaseLLMProvider):
        name = "Other"

        def generate(self, prompt, *, stage, context=None):
            return "ok"

    fb.reset_circuit_breaker()
    try:
        fb.skip_for_run("LM Studio", "test")
        assert ChainedFallbackProvider([_Dead(), _Live()]).generate("p", stage="s") == "ok"
    finally:
        fb.reset_circuit_breaker()


def test_skip_cooldown_expires_then_due_for_reprobe():
    from investment_engine.providers import fallback as fb

    fb.reset_circuit_breaker()
    try:
        fb.skip_for_run("LM Studio", "test", cooldown_s=300)
        assert fb.is_skipped("LM Studio")
        assert not fb.due_for_reprobe("LM Studio")
        assert "LM Studio" in fb._SKIP_UNTIL  # cooldown, not permanent exile
        fb._SKIP_UNTIL["LM Studio"] = 0.0  # force expiry (no sleeping)
        assert not fb.is_skipped("LM Studio")
        assert fb.due_for_reprobe("LM Studio")
        fb.revive("LM Studio")
        assert not fb.due_for_reprobe("LM Studio")
    finally:
        fb.reset_circuit_breaker()


def test_eligible_readmits_cooldown_expired_link():
    from investment_engine.providers.base import BaseLLMProvider
    from investment_engine.providers import fallback as fb
    from investment_engine.providers.fallback import ChainedFallbackProvider

    class _LM(BaseLLMProvider):
        name = "LM Studio"

        def generate(self, prompt, *, stage, context=None):
            return "lm-ok"

    class _Live(BaseLLMProvider):
        name = "Other"

        def generate(self, prompt, *, stage, context=None):
            return "live-ok"

    fb.reset_circuit_breaker()
    try:
        fb.skip_for_run("LM Studio", "test", cooldown_s=300)
        chain = ChainedFallbackProvider([_LM(), _Live()])
        # Active cooldown: tripped link excluded, other link serves.
        assert [p.name for p in chain._eligible()] == ["Other"]
        assert chain.generate("p", stage="s") == "live-ok"
        fb._SKIP_UNTIL["LM Studio"] = 0.0
        assert [p.name for p in chain._eligible()] == ["LM Studio", "Other"]
        assert chain.generate("p", stage="s") == "lm-ok"
    finally:
        fb.reset_circuit_breaker()


def test_revive_locals_if_due_revives_on_200(monkeypatch):
    import requests

    import investment_engine.main as m
    from investment_engine.providers import fallback as fb
    from investment_engine.providers.fallback import ChainedFallbackProvider
    from investment_engine.providers.lmstudio import LMStudioProvider

    class _Resp:
        status_code = 200

    fb.reset_circuit_breaker()
    try:
        link = LMStudioProvider(base_url="http://127.0.0.1:9/v1", model="probe-model")
        fb.skip_for_run("LM Studio", "test", cooldown_s=300)
        fb._SKIP_UNTIL["LM Studio"] = 0.0
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
        m._revive_locals_if_due(ChainedFallbackProvider([link]))
        assert not fb.is_skipped("LM Studio")
    finally:
        fb.reset_circuit_breaker()


def test_revive_refreshes_cooldown_on_failure(monkeypatch):
    import requests
    import time

    import investment_engine.main as m
    from investment_engine.providers import fallback as fb
    from investment_engine.providers.fallback import ChainedFallbackProvider
    from investment_engine.providers.lmstudio import LMStudioProvider

    fb.reset_circuit_breaker()
    try:
        link = LMStudioProvider(base_url="http://127.0.0.1:9/v1", model="probe-model")
        fb.skip_for_run("LM Studio", "test", cooldown_s=300)
        fb._SKIP_UNTIL["LM Studio"] = 0.0
        monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
        m._revive_locals_if_due(ChainedFallbackProvider([link]))
        assert fb.is_skipped("LM Studio")
        assert fb._SKIP_UNTIL["LM Studio"] > time.time()
    finally:
        fb.reset_circuit_breaker()
