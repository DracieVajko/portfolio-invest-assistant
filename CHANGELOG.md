# Changelog

All notable changes to this project are documented in this file.

## [v0.6.0-monolith] - 2026-08-29

Maintenance-oriented continuation of the monolithic line. Based on the historical `portfolio_ai_assistant_V4_5` snapshot, sanitized for public staging.

### Added
- Sanitized `api.env.example` with safe localhost placeholders (no real credentials).
- Sanitized `portfolio_config.example.json` with 3 demo assets (`AAPL`, `MSFT`, `BTC-USD`), conservative defaults (`ai_max_assets=10`, `max_news_per_asset=10`), deduplicated keys, and sanitized URLs (`http://127.0.0.1:1234/v1`, `http://127.0.0.1:7000`).
- Local `portfolio_config.json` (gitignored) identical to the example for out-of-the-box `python portfolio_ai_assistant.py --no-ai`.
- `PIEs/README.md` and four `PIEs/example_*.csv` demo exports (header + 2 fictitious rows).
- `.gitignore` covering secrets, runtime output, caches, and temporary patches.

### Fixed
- `load_dotenv` import: graceful fallback via `try: from dotenv import load_dotenv` with `_HAS_DOTENV` flag; `load_environment_variables()` no longer crashes if `api.env` is absent or `python-dotenv` is missing.
- `run_t212_analysis.bat` / `run_t212_export.bat`: now call `python trading212\integration.py` with `%~dp0`-relative paths instead of the non-existent `trading212_integration.py`.
- `requirements.txt`: removed exact duplicate `ddgs>=0.2.0` line.
- Duplicate definitions in `portfolio_ai_assistant.py`: removed earlier identical `migrate_legacy_config` and `validate_config` blocks; later definitions remain effective.
- Temporary patch artefacts `fix_call_stage.py` / `fix_settings.py`: intentionally not carried over (their changes already present in the monolith).

### Known Technical Debt (documented, not migrated)
- Dual Trading 212 read-only implementation: inline logic in `portfolio_ai_assistant.py` (`fetch_trading212_positions`, `normalize_t212_ticker`, …) and `trading212/` package (`auth.py`, `portfolio.py`, `integration.py`) coexist.
- Remaining duplicate definitions: `check_ollama_available` (2) and `get_working_model` (2) — later definitions win at runtime; left for minimal-risk maintenance.
- No test suite; live providers (yfinance, DDGS, Trading 212) may be unavailable or rate-limited.

### Lineage
- `v0.5.0` — previous historical monolith milestone.
- `v0.6.0-monolith` — this maintenance snapshot (staging, not yet tagged).
- `v1.0.0` — separate modular rewrite, WIP/experimental, not part of the monolith line.

No Git, tag, or network operations were performed during staging.

## [v0.5.0] - historical

Previous historical monolithic milestone preserved in `Zalohy/portfolio_ai_assistant_V4_5/`. Not sanitized; contains private credentials, real PIE exports, and runtime caches. Reference only.

## [v1.0.0] — WIP (modular)

Separate modular rewrite on the `main` branch (`portfolio-invest-assistant`, modular). Experimental and unstable; not published as a release. No lineage merge with `v0.6.0-monolith`.

