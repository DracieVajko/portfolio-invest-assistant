# V4 Hardening Changelog

Release: `v4.1.0-hardening` (schema `v4.1`). Entry point unchanged:
`portfolio_ai_assistant.py`. New module: `v4_hardening.py` (stdlib-only).

## Files changed / created

- `portfolio_ai_assistant.py` — lifecycle, gates, reports, CLI (see below).
- `v4_hardening.py` — **new**: `RunExit` codes, `RunLock`, atomic writes,
  secret redaction, phase logger, `validate_config_full`, mapping audit +
  price/currency gates, `finalize_action` (canonical actions), TP/SL gate,
  transient-only retry helper, run-identity helpers.
- `tests/conftest.py`, `tests/test_v4_hardening.py` — **new**, 55 offline tests.
- `docs/V4_HARDENING_IMPLEMENTATION.md` — **new**, plan + state definitions.
- `docs/V4_HARDENING_CHANGELOG.md` — this file.
- `requirements.txt` — added `pytest>=8` (test only).
- `README.md` — scheduled-ops section, exit codes, layout.
- `run_full_report.bat`, `run_automated.bat` — pass `--scheduled`.
- Runtime artifacts: `runtime/run.lock` (created on runs; OS-enforced, never deleted
  while held).

## Behavior changes

- One scheduled command: `--scheduled`; overlapping run exits `3` without touching
  reports/logs (OS-level lock, stale-safe by design).
- Exit codes 0/1/2/3/4/5 (`RunExit`); unknown `--group`/`--asset` → exit 2.
- Invalid config stops the run **before** any fetch/score/publish (exit 2).
- Every broker position carries a mapping audit (method/confidence/status/reason,
  currencies, price comparison, `is_actionable`).
- Price gate (defaults warn 15 %, block 35 %; `mapping_price_warn_pct/block_pct`):
  SYNL-class mismatches (e.g. €0.01 vs €89.98) ⇒ MAPPING_SUSPECT, non-actionable,
  broker value/P&L stay visible as broker-provided; no external-value math.
- Currency mismatch without explicit FX ⇒ CURRENCY_UNRESOLVED (blocked); explicit
  `fx_rates` conversion supported in the gate helper.
- Canonical actions ADD_CANDIDATE/HOLD/WAIT/REVIEW/REDUCE_CANDIDATE/DATA_UNAVAILABLE/
  REVIEW_MAPPING (+ legacy BUY/SELL/WATCH fields kept in JSON). Contradiction guards:
  RSI ≥ 70 blocks ADD; sell−buy ≥ margin blocks ADD (mirror for REDUCE); weak
  technical score blocks ADD on neutral defaults; neutral band ⇒ HOLD. WAIT/HOLD/REVIEW
  are reachable by construction (verified: full run 9 ADD / 7 REDUCE on TECH_PIE
  instead of 16/16 BUY).
- Missing RSI/MA stay null (never neutral defaults); missing news ⇒
  `sentiment_fallback: true` label; stale history (> `stale_days`, 7) and
  insufficient history (< `min_history_bars`, 14) gate ADD/REDUCE.
- TP/SL emitted only for SHORT_TERM_TRADING with valid mapping/price/currency,
  labeled informational with method/currency/timestamp; LONG_RUN_DCA and other
  groups get none (thesis note for DCA). Never for blocked mappings.
- Reports from one canonical result: concise `portfolio_report.md` (snapshot →
  attention ≤10 → action tables → reconciliation → provider notes + disclaimer),
  `data_quality.md` audit, canonical JSON (schema/run/timings/providers/mapping/AI
  trace), `run_manifest.json`; layout `reports/latest/` + `reports/archive/YYYY-MM-DD/<run_id>/`;
  legacy `reports/portfolio_analysis.{md,json}` kept as compat copies.
- Atomic publishes (tempfile + `os.replace`); latest published only when complete.
- `--no-ai` skips warmup and generation (AI DISABLED); AI failure is non-fatal and
  cannot change scores/actions; per-stage backend/model/latency recorded.
- Phase logs carry `run_id`/status/duration; secrets redacted in logs/exceptions.
- T212 reads retry transient failures only (429 honors Retry-After, max 3 attempts);
  401/403/404 never retried. Broker failure ⇒ exit 5 with explicit report labeling.
- `--validate-only` is fully offline (+ opt-in `--check-connectivity`);
  `--migrate-config` backs up first, validates output, exits non-zero if invalid;
  `--json-only` skips Markdown; `--debug` sets DEBUG level; `--no-interactive`
  retained as documented compat flag (no `input()` exists in the codebase).

## Dead flags implemented or removed

- Implemented: `--json-only`, `--debug`, `--no-ai` (warmup skip), `--dry-run`
  (verified: zero report writes), `--group`/`--asset` (unknown ⇒ exit 2),
  `--validate-only` (offline), `--migrate-config` (backup + output validation).
- Retained as compat: `--no-interactive` (no interactive paths exist).
- Added: `--scheduled`, `--check-connectivity`.

## Duplicate definitions resolved

AST-verified, behavior preserved (later/effective definition kept):

- `migrate_legacy_config` (2 identical copies → 1).
- `validate_config` (2 identical copies → 1; strict checks now live in
  `v4_hardening.validate_config_full`, used by all run paths).
- `get_working_model` (2 behaviorally identical copies via shim → 1).
- `check_ollama_available` (compat shim + general version → general version kept).
- Dead in main flow (kept, documented): `fetch_tradingview_technical_data`,
  `detect_symbol_mismatch` (superseded by the price gate), `resolve_yahoo_symbol`.

## Report-file layout

```text
reports/latest/{portfolio_report.md, portfolio_report.json, data_quality.md, run_manifest.json}
reports/archive/YYYY-MM-DD/<run_id>/{same four files, latest_run.log snapshot}
reports/portfolio_analysis.{md,json}   # legacy compat copies
```

Old `archive/*_portfolio_analysis.*` files are no longer written (left untouched).

## Tests added and commands used

- `py -3.12 -m pytest tests/test_v4_hardening.py -q` → **55 passed** (offline).
  Covers: config validation (8), migration incl. backup/invalid (5), mapping incl.
  weak/ambiguous/auto (6), price gate incl. SYNL case/currency/FX (8),
  action gates incl. QCOM/AVGO contradictions (9), levels (4), output/atomic/
  report-order/publish incl. json-only (5), CLI incl. dry-run/lock/validate/
  unknown-group/asset/debug (10).
- `py -3.12 -m py_compile` on both modules → OK (no linter config exists in repo).
- Smoke: `--dry-run --no-ai --group TECH_PIE` on live providers → exit 0,
  broker OK (72 positions), 12 global + 8 discovery news, zero report writes.

## Remaining limitations (deferred to V5)

- yfinance uses its own internal HTTP client (no explicit timeout/Retry-After control).
- No FX rate provider: cross-currency compare needs manually supplied `fx_rates`.
- Trump radar is a hardcoded empty placeholder (`trump_news = {}`).
- `trading212/` package and `fetch_tradingview_technical_data` are unused by main;
  kept for optional/external use, not wired in.
- Strict/soft BUY split removed from the main report (was tautological); raw
  probabilities remain in JSON for consumers.
- Free-tier cloud AI (OpenRouter/Gemini) can rate-limit; chain fails over to Mistral.
