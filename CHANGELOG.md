# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) for tagged releases.

## [Unreleased]

- V4 hardening: deterministic pipeline, broker-vs-market mapping gates, canonical actions, atomic publishing

## [4.1.0] — 2026-09-20 — V4 Hardening Release

Stable, deterministic, read-only portfolio monitoring with local AI models (Ollama / LM Studio / OpenRouter).

### Added

- **V4 hardening core** (`v4_hardening.py`): exit codes, OS-level single-run lock (stale-safe), atomic file publishing, secret redaction, phase timing logger, strict config validation (errors vs warnings)
- **Broker mapping audit** with price/currency sanity gates (exact, alias, prefix, auto-discovered) — blocks ambiguous/invalid mappings from mutating actions
- **Data-quality states** + **canonical action vocabulary**: `ADD_CANDIDATE`, `HOLD`, `WAIT`, `REVIEW`, `REDUCE_CANDIDATE`, `DATA_UNAVAILABLE`, `REVIEW_MAPPING` (hard gates: mapping → data → contradiction → thresholds)
- **Stop-loss / take-profit gating** (informational only, never orders) — only for `SHORT_TERM_TRADING` group with actionable mapping and valid data
- **Transient-only HTTP retry helper** (408/429/5xx, honors `Retry-After`, jitter)
- **AI backend chain** with warmup: LM Studio → Ollama → OpenRouter → Gemini → Mistral; warmup runs in background, preloads local models
- **Per-asset DuckDuckGo news** (48h, keyword sentiment) + global/discovery news
- **Deterministic technical signals**: RSI, MA crossovers, golden/death cross, momentum, trend
- **Atomic report publishing**: `reports/latest/` + `reports/archive/YYYY-MM-DD/<run_id>/` + legacy compat copies
- **Exit codes**: `0` SUCCESS · `1` FATAL · `2` CONFIG_ERROR · `3` ALREADY_RUNNING · `4` PARTIAL · `5` BROKER_FAILED
- **Pre-flight checks**: `--validate-only` (offline schema), `--dry-run --no-ai` (full pipeline in memory), `pytest tests/` (55 offline tests)

### Changed

- `portfolio_ai_assistant.py` refactored: consolidated imports, removed dead code (`ddg_search`, `load_config`, `validate_config`, `fetch_tradingview_technical_data`, `pct`, `recommendation_label`, `data_quality_reason`, `detect_symbol_mismatch`, `resolve_yahoo_symbol`, `_asset_line`, `action_label_for_asset`, `format_news_items`, legacy backend helpers `_detect_backend`, `check_lmstudio_available`, `check_ollama_available`, `get_working_model`, `validate_model_available`, `list_lmstudio_models`, `list_ollama_models`)
- Fixed broken `_yf_cache` (removed — it never cached) and fixed `build_asset_summary` / `calculate_recommendation` to read `info`/`technicals` from top level (fundamental scoring and MA/trend/currency now work)
- Deduplicated news collectors with shared date-filter and annotation helpers
- Removed trailing module-level logging that ran at import time
- Moved local imports (`base64`, `random`, `subprocess`, `uuid`) to top-level

### Fixed

- `NameError: _resolve_env` in AI warmup thread (function now defined before `call_stage`)
- `build_asset_summary` and `calculate_recommendation` now correctly read fundamentals (PE, PB, dividend, beta) and technicals from top-level data (was nested under `price` → always None)
- `--validate-only` and `--dry-run` work without network calls

### Removed

- Stale one-shot patch scripts: `fix_call_stage.py`, `fix_settings.py`
- Duplicate test bootstrap: `tests/conftest_V2415_Sep-16-1553-2026_1.py`
- Duplicate cache file: `data/cache/cookies_V2415_Sep-16-1553-2026_1.db`
- Empty stub directory: `investment_engine/`
- Unused `python-dotenv` dependency (code uses custom `api.env` loader)

## [0.6.0] — Historical monolith (stable)

Previous stable monolithic release (pre-V4 hardening). Reference only.

## [0.5.0] — Historical

Feature-rich monolithic milestone with multi-source research. Reference only.

## [0.1.0] — Historical

Initial baseline portfolio tracker. Reference only.