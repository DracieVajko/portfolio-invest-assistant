# Portfolio AI Assistant — project handoff summary

Date: 2026-10-07. Audience: a frontier AI agent that will implement the next
changes. Language of the repo: English code/logs, Slovak config comments.
Runtime: **Python 3.12 only** (`py -3.12`), no venv (system interpreter).
Full offline suite: **469 tests passing** (`py -3.12 -m pytest tests/ -q`).

## 1. What it is

Read-only investment monitor. Syncs a Trading 212 portfolio (API, never trades),
enriches every holding/watchlist asset with market data + technicals + news,
runs local-LLM stages, and publishes deterministic Markdown + JSON reports.
Entry point: `portfolio_ai_assistant.py` (CLI, `--config portfolio_config.json`).
Batch helpers: `run_full_report.bat`, `run_automated.bat`, `run_t212_analysis.bat`,
`run_t212_export.bat`, `install.bat` / `install.sh`, `cleanup.bat`.

## 2. How a run works (pipeline)

1. `run_engine()` in `investment_engine/main.py`: config → T212 sync
   (`trading212/` package: `auth.py`, `portfolio.py`, `integration.py`) →
   broker-first reconciliation (canonical, threshold €2.00) → universe build →
   market data (yfinance) → technicals → news → regime → AI stages → reports.
2. Debug/pruning log prefix in the run log (`reports/debug/<run_id>/` atomically
   moved by `move_debug_outputs`).
3. Publish (`portfolio_ai_assistant.write_reports`, atomic `os.replace`):
   `reports/current/{portfolio_decision_brief.md, t212_portfolio_snapshot.md,
   portfolio_analysis.json, portfolio_intelligence_brief.md, failed_tickers.md
   (only if unresolvable), run_manifest.json}` + `reports/archive/<run_id>/` copy
   + `reports/ai_context/ai_context_<run_id>.md` machine archive.

## 3. Modules that matter

- `investment_engine/config/settings.py` — `EngineSettings` dataclass (env +
  JSON mapping, `num_ctx` alias → `lm_studio_context_tokens`). Execution modes:
  `auto` (locals→cloud), `locals`, `public`/`api_only` (cloud only, locals never
  probed). `resolve_execution_mode()` never raises.
- `investment_engine/portfolio/symbols.py` — ticker resolution. `resolve_alias()`
  is the ONLY place for `symbol_aliases` + static table. `support_state()`:
  `SUPPORTED / UNSUPPORTED / UNRESOLVED`. `UNRESOLVED_YAHOO` hard blocklist
  (EGTL, HY9H, C7A0, SU, AI, ENR, …). US-pattern tickers need explicit
  verification (SUPPORTED list or ISIN/currency/venue proof).
- `investment_engine/portfolio/broker_first.py` — canonical positions from T212
  (dedupe, FX, currency states incl. `CURRENCY_UNRESOLVED`).
- `investment_engine/research/` — `market_data.py` (yfinance + Playwright
  fallback), `technical_analysis.py` (manual engine canonical), `news_engine.py`
  + `news_sources.py` (RSS fallback; Yahoo RSS endpoint dead → 404, handled),
  `web_researcher.py` (Playwright TV scrape), `market_regime.py` (EXI2 multi
  timeframe), `corpus/adapters_tradingview.py` (TV sweep, max 8 symbols, 403
  kill-switch), `ticker_suggest.py` (Yahoo search-API symbol suggestions, NEW),
  `playwright_adapter.py` (cache + retry wrapper), `universe.py`, `indicators.py`.
- `investment_engine/providers/` — `base.py` (interface: `generate(prompt, *,
  stage, context)`), `factory.py` (chain builder), `fallback.py`
  (`ChainedFallbackProvider` + per-run circuit breaker), `lmstudio.py`,
  `ollama.py`, `llamacpp.py`, `openrouter.py`, `gemini.py`, `mistral.py`,
  `opencode_zen.py`, `presets.py` (sampling per role), `attribution.py`.
- `investment_engine/reporting/` — `failed_tickers.py` (ledger + md with
  suggestions table, NEW), `report_structure.py`, `documents.py`, `regime_report.py`.
- `investment_engine/research/market_regime.py` + `config` — EXI2 regime
  (intraday_15m/1h, daily, weekly, monthly; intraday often fails validation →
  warning, by design).
- `scripts/` — `verify_environment.py` (offline preflight),
  `verify_indicators.py`, `verify_manifest.py`, `build_universe.py`.
- `tools/t212_cli.py`, `trading212/` package (also legacy root
  `trading212_*.py` shims — do not extend, kept for compat).
- `experimental/` — isolated backtest suite (own tests, not part of main suite).

## 4. LLM providers (local-first chain)

Order: **LM Studio → llama.cpp (Pi 5, :11435) → Ollama (Pi, :11434) → Gemini →
OpenRouter (:free rotation) → Mistral → deterministic fallback**.
Zen is explicit-only (`PROVIDER=opencode_zen`). Stage routing
(`_model_for_stage`): `summary→writer_model` (gemma-4-26b, alt bonsai-27b /
ministral-3-3b), `discovery→thinking` (gpt-oss-20b, 32k ctx),
`decision/ai_recommendations→finance` (gpt-oss-20b), rest→decision_model. Only the direct LM Studio attempt honors the stage model;
the chain serves its own fixed models. Real winner per stage is recorded in
`run_manifest.json` (`providers_per_stage`). Live endpoints are Tailscale IPs
(`100.88.219.120:1234`, `100.125.47.31:11434/11435`).

## 5. Ticker resolution + failed ledger (A+C, implemented 2026-10-07)

- `collect_failed_tickers()` classifies only `HELD_UNRESOLVED` (nonzero broker
  qty/value + T212 id); watchlist/stale entries never enter the ledger.
- `ticker_suggest.suggest_yahoo_symbols()`: Yahoo search API
  (`query1.finance.yahoo.com/v1/finance/search`, keyless, `requests`, 10 s
  timeout), scored candidates (exact > prefix > name > type/exch). Never raises.
- `render_failed_tickers_md(..., suggestions=)` adds a suggestions table;
  the wrapper prints a console block (`display: tried → suggest`, max 10 HELD).
- Playwright is deliberately NOT an auto suggestion source (heavy, needs
  browser, bot-challenges) — manual quote-page check is the documented last resort.

## 6. What works

- End-to-end run: T212 sync → recon PASS → data → AI → published reports.
- Static contexts today: LM Studio 32000, Ollama 128000 (config), llamacpp 4096
  guard (85% rule, skips to next link instead of truncating).
- Manual technicals engine; pinned stack `pandas==2.2.3 numpy==1.26.4 scipy==1.13.1`,
  `pip check` clean.
- Playwright pip 1.63.0 + Chromium installed and smoke-tested (`chromium launch OK`);
  TV sweep / earnings layer-2 / technicals fallback are now live (were dormant).
- `install.bat` / `install.sh` installs everything incl. browser.
- 469/469 tests green.

## 7. What does NOT work / known issues

1. `SUI-USD`, `TAO-USD`: yfinance `No data found` (dead/wrong crypto symbols) →
   broker-only valuation, no technicals/news/signal. Awaiting user alias fix via
   new suggestions flow.
2. `HY9H` (and blocklist mates): structurally `UNRESOLVED`, skipped for earnings.
3. Yahoo Finance news RSS (`finance.yahoo.com/news/rssindex`): dead (404); RSS
   fallback uses the 3 other tier-2 sources.
4. `OllamaProvider` sends **no** `options.num_ctx` and uses wrong key `max_tokens`
   instead of `num_predict` (works, but wastes KV/VRAM at fixed 128k).
5. `LMStudioProvider._unload_via_api` tries non-existent management endpoints;
   correct API is `POST /api/v1/models/load {context_length}` /
   `unload {instance_id}` (proven in sibling project `LM_Studio_Optimiser`,
   read-only reference, do not modify it).
6. `Ollama 0.30.7` upstream issue #16814 reports `num_ctx` ignored per-request —
   verify with `ollama ps` on the deployed Pi before relying on it.
7. `pandas-ta` absent: INTENTIONAL (archived, incompatible with pinned stack).
   The import warning is expected/harmless. Do NOT "fix" by installing it.
8. `ddgs` is a declared dependency but used nowhere (future web-search source).
9. Intraday EXI2 timeframes often fail validation (warning, by design).
10. README model table (`fin-r1`…) is stale vs actual stage models (§4).

## 8. In-flight plans (not implemented)

- `docs/specs/2026-10-07-dynamic-context-size-design.md` (gitignored, local only):
  per-call minimal `needed_ctx = prompt + thinking_reserve + output + 10%`,
  LM Studio preload with `context_length`, Ollama `options.num_ctx`, default
  `context_mode=auto` (+`fixed`/`max`), static HW caps. Phase 2: per-asset
  chunking when `needed` exceeds caps. API providers explicitly out of scope.
- Preload cannot overlap data gathering (`needed` is known only from the final
  prompt) — accepted cost (one 6–60 s load per model/stage, then faster).

## 9. Rules for the next implementer

- Tests are offline/stub-only (no HTTP, no browser, no endpoints): monkeypatch
  transports. See `tests/test_playwright_sweep.py`, `tests/test_ticker_suggest.py`.
- Never raise from research/suggestion paths — chain-visible `[stage] …` error
  strings + circuit breaker instead.
- No secrets in code/logs (`api.env` gitignored, `*.env` ignored). Never commit
  `reports/`, `logs/`, `data/` (only `reports/.gitkeep` is tracked).
- Follow existing per-file patterns; keep diffs minimal; update the affected
  `tests/test_*.py` in the same change.
- Verify with `py -3.12 -m pytest tests/ -q` and `py -3.12 -m pip check`.
