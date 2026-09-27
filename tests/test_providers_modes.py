"""Provider modes, stage routing, llamacpp guard (Step 3).

Strictly offline: stub providers, no HTTP, no local endpoints.
(User rule: tests use API/offline only.)
"""
from __future__ import annotations

from types import SimpleNamespace


def _settings(**kw):
    from investment_engine.config.settings import EngineSettings
    base = dict(
        provider="auto", api_only=False, execution_mode="auto",
        lmstudio_enabled=False,
        lm_studio_base_url="http://127.0.0.1:9/v1",
        lm_studio_model="qwen3.8-9b-distill",
        lm_studio_context_tokens=32000, lm_studio_max_output_tokens=8192,
        request_timeout_seconds=60,
        decision_model="qwen3.8-9b-distill", writer_model="google/gemma-4-12b",
        finance_model="fin-o1-14b", thinking_model="think-30b",
        local_fallback_model="openai/gpt-oss-20b",
        fallback_model="openrouter/free",
        llamacpp_base_url="http://127.0.0.1:9/v1", llamacpp_model="qwen3.8-9b",
        llamacpp_context_tokens=4096,
        ollama_base_url="http://127.0.0.1:9", ollama_model="qwen3.8-9b-pi",
        ollama_context_tokens=128000,
        gemini_api_key="", mistral_api_key="", openrouter_api_key="",
        opencode_zen_api_key="",
    )
    base.update(kw)
    return EngineSettings(**{k: v for k, v in base.items()
                             if k in EngineSettings.__dataclass_fields__})


def test_resolve_execution_mode():
    from investment_engine.config.settings import resolve_execution_mode

    assert resolve_execution_mode(_settings(api_only=True)) == "public"
    assert resolve_execution_mode(_settings(execution_mode="public")) == "public"
    assert resolve_execution_mode(_settings(execution_mode="locals")) == "locals"
    assert resolve_execution_mode(_settings(execution_mode="LOCAL_ONLY")) == "locals"
    assert resolve_execution_mode(_settings()) == "auto"
    assert resolve_execution_mode(_settings(execution_mode="nonsense")) == "auto"
    assert resolve_execution_mode(None) == "auto"


def test_public_chain_has_no_locals():
    from investment_engine.providers.factory import ProviderFactory

    chain = ProviderFactory.create(_settings(execution_mode="public"))
    names = chain.name if hasattr(chain, "name") else ""
    assert "LM Studio" not in names and "llama.cpp" not in names and "Ollama" not in names
    # no keys configured -> deterministic terminal link, never a probe
    assert "NoCloudKey" in names


def test_locals_chain_has_no_cloud():
    from investment_engine.providers.factory import ProviderFactory

    chain = ProviderFactory.create(_settings(execution_mode="locals",
                                             gemini_api_key="x", mistral_api_key="y"))
    names = chain.name if hasattr(chain, "name") else ""
    assert "Gemini" not in names and "Mistral" not in names
    assert "Ollama" in names and "llama.cpp" in names  # LM Studio disabled here


def test_build_chain_uses_settings_llamacpp_url_and_order():
    from investment_engine.providers.factory import ProviderFactory

    custom = "http://10.9.9.9:11435/v1"
    links = ProviderFactory._build_chain(_settings(llamacpp_base_url=custom))
    by_name = {p.name: p for p in links}
    assert by_name["llama.cpp"].base_url == custom  # was hardcoded before
    order = [p.name for p in links]
    assert order.index("Ollama") < order.index("llama.cpp")  # big ctx first


def test_model_for_stage_map():
    import investment_engine.main as m

    s = {"decision_model": "D", "writer_model": "W",
         "finance_model": "F", "thinking_model": "T"}
    assert m._model_for_stage(s, "summary") == "W"
    assert m._model_for_stage(s, "discovery") == "T"
    assert m._model_for_stage(s, "decision") == "F"
    assert m._model_for_stage(s, "ai_recommendations") == "F"
    assert m._model_for_stage(s, "news_events") == "D"
    ns = SimpleNamespace(decision_model="D", writer_model="W",
                         finance_model="F", thinking_model="T")
    assert m._model_for_stage(ns, "decision") == "F"


def test_try_lmstudio_stage_paths():
    import investment_engine.main as m
    from investment_engine.providers.fallback import ChainedFallbackProvider
    from investment_engine.providers.lmstudio import LMStudioProvider

    class _StubLM(LMStudioProvider):
        def __init__(self, ok=True):
            self._ok = ok
            self.seen = []

        @property
        def name(self):
            return "LM Studio"

        def generate(self, prompt, *, stage, context=None):
            self.seen.append(((context or {}).get("model"), (context or {}).get("read_timeout_s")))
            if self._ok:
                return "finance answer"
            return f"[{stage}] LM Studio error: down"

    s = {"lmstudio_enabled": True, "decision_model": "D", "writer_model": "W",
         "finance_model": "F", "thinking_model": "T"}

    # disabled policy -> no attempt
    assert m._try_lmstudio_stage(ChainedFallbackProvider([_StubLM()]),
                                 "p", settings=dict(s, lmstudio_enabled=False),
                                 stage="decision", tokens=100) == (None, None)
    # undesignated stage -> chain default
    chain = ChainedFallbackProvider([_StubLM()])
    assert m._try_lmstudio_stage(chain, "p", settings=s,
                                 stage="news_events", tokens=100) == (None, None)
    assert chain.providers[0].seen == []
    # designated stage success, truthful served model + thinking timeout
    served, out = m._try_lmstudio_stage(chain, "p", settings=s,
                                        stage="discovery", tokens=100)
    assert (served, out) == ("T", "finance answer")
    assert chain.providers[0].seen == [("T", 1200)]
    # designated stage failure -> fall back to chain
    bad = ChainedFallbackProvider([_StubLM(ok=False)])
    assert m._try_lmstudio_stage(bad, "p", settings=s,
                                 stage="decision", tokens=100) == (None, None)
    # no LM Studio link in chain -> no attempt
    from investment_engine.providers.base import BaseLLMProvider

    class _Other(BaseLLMProvider):
        name = "Other"

        def generate(self, prompt, *, stage, context=None):
            return "x"

    assert m._try_lmstudio_stage(ChainedFallbackProvider([_Other()]), "p",
                                 settings=s, stage="decision", tokens=100) == (None, None)


def test_llamacpp_context_guard_offline():
    from investment_engine.providers.llamacpp import LlamaCppProvider

    p = LlamaCppProvider(base_url="http://127.0.0.1:9/v1", max_context_tokens=4096,
                         max_output_tokens=512)
    long_prompt = "x" * (4096 * 4 * 2)  # ~8k tokens, over 85% of 4096
    res = p.generate(long_prompt, stage="decision")
    assert res.startswith("[decision]") and "context budget" in res
    # short prompt would attempt HTTP (unreachable here) -> error string, no raise
    res2 = p.generate("hi", stage="decision")
    assert res2.startswith("[decision]")


def test_presets_thinking_and_finance():
    from investment_engine.providers.presets import (
        FINANCE_STAGES, THINKING_STAGES, WRITER_STAGES, preset_for)

    assert "discovery" in THINKING_STAGES and "summary" in WRITER_STAGES
    assert {"decision", "ai_recommendations"} <= set(FINANCE_STAGES)
    assert preset_for("discovery")["min_tokens_floor"] == 2048
    assert preset_for("decision")["temperature"] == 0.6
    assert preset_for("summary")["temperature"] == 0.2


def test_execution_mode_from_mapping():
    from investment_engine.config.settings import EngineSettings, resolve_execution_mode

    assert resolve_execution_mode(EngineSettings.from_mapping({"execution_mode": "locals"})) == "locals"
    assert resolve_execution_mode(EngineSettings.from_mapping({"api_only": True})) == "public"
