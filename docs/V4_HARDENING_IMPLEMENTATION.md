# V4 Hardening Release — Implementation Plan

## 1. Initial repository assessment

- Single-file entry point: `portfolio_ai_assistant.py` (~2960 lines, 59 top-level functions).
- Config: `portfolio_config.json` (settings, 32 manual assets, rules, aliases, `ai_backends`).
- Secrets: `api.env` (Trading212 key/secret, AI cloud keys), loaded into `os.environ`.
- Data providers: Trading212 REST (read-only), yfinance, DuckDuckGo news (`ddgs`), AI chain
  (LM Studio → remote Ollama → OpenRouter → Gemini → Mistral, implemented in V4 working state).
- Outputs: `reports/portfolio_analysis.{md,json}` + `reports/archive/*` + `logs/latest_run.log`.
- No test suite in the project root. No lint/format config. Toolchain: Python 3.12, pytest added for this release.
- Ignored for this release: `AsistantV5/`, `Zalohy/`, `portfolio-invest-assistant-clean/` (separate/legacy).

Confirmed defects (by code inspection + observed reports):

1. Wrong broker→Yahoo mapping produced absurd values (SYNL ≈ €44M on a €3.2k account;
   ENRD/LITMM/VWSBD/AIP price ratios 2x–8000x) and still fed value/P&L/signals/BUY.
2. Over-bullish scoring: `buy_probability` defaults to 50 while `buy_threshold` is 45, so the
   default state is already BUY; observed runs with 86–94 of 94 assets BUY and 0 WATCH.
3. Contradictions: QCOM (RSI 73.6, tech 39, sell 75%) → BUY; AVGO (tech 44, sell 67%) → BUY.
4. Duplicate top-level defs (AST-verified): `migrate_legacy_config` (identical),
   `validate_config` (identical), `get_working_model` (behaviorally identical via shim),
   `check_ollama_available` (different; later/general one is effective).
5. Dead CLI flags: `--json-only`, `--debug`, `--no-interactive` accepted but unused.
6. `--validate-only` performs a live network probe (not offline).
7. `fetch_tradingview_technical_data()` is never called from `main()`.
8. `--no-ai` still starts the AI warmup thread.
9. No run lock → overlapping scheduler runs possible (observed 3–50 min runtimes).
10. Report mixes raw data, audit tables and actions; no mapping-review section; no run manifest.

## 2. One-run pipeline architecture (kept)

```
CLI (portfolio_ai_assistant.py [--scheduled])
  → run lock (runtime/run.lock) → exit 3 if busy
  → config load + strict validation → exit 2 on errors
  → env load (api.env)
  → Trading212 portfolio + cash (read-only, retry transient only)
  → merge + mapping audit (every position gets status/reason)
  → AI warmup in background (skipped with --no-ai)
  → [optional --group/--asset filter, recorded in manifest]
  → yfinance market data + DDGS news (existing providers)
  → deterministic signals → data-quality states → action gates
  → canonical result object (ONE calculation)
  → AI summary of validated facts only (non-fatal, recorded)
  → render JSON + main MD + data-quality MD + manifest (atomic writes)
  → publish latest + archive + legacy compat copies
  → exit code 0/1/2/3/4/5
```

## 3. Planned changes per scope

- **A (lifecycle):** `RunExit` IntEnum (0/1/2/3/4/5); OS-level lock file in `runtime/`
  (msvcrt.locking / fcntl.flock, no stale locks by design, metadata content only);
  `--scheduled` flag as canonical Task Scheduler command; lock released in `finally`;
  `main(argv=None) -> int`, `sys.exit(main())`.
- **B (CLI):** implement every retained flag with observable behavior + tests:
  `--no-ai` (skip warmup + generation, AI status DISABLED), `--dry-run` (in-memory only,
  explicit message, same exit-code logic), `--group/--asset` (filter + manifest note +
  non-zero on unknown), `--validate-only` (fully offline) + new `--check-connectivity`,
  `--migrate-config` (timestamped backup, change report, output validation),
  `--json-only` (JSON artifacts only), `--debug` (DEBUG level + timings),
  `--no-interactive` (documented compat flag; repo contains no `input()` calls),
  `--scheduled` (full automatic run).
- **C (config):** `validate_config_full()` returning `(errors, warnings)`; errors stop the run
  before any fetch (exit 2): bad JSON, missing sections, duplicate/ambiguous asset IDs or
  aliases, malformed assets, thresholds/timeouts out of range, broken `ai_chain_*` /
  backend references, missing env-var names for enabled cloud backends. Dedupe the 4
  duplicate defs (keep effective behavior; identical pairs collapsed).
- **D (mapping):** `build_mapping_audit()` for every broker position; methods
  EXACT_CONFIGURED_SYMBOL / EXACT_NORMALIZED_SYMBOL / EXPLICIT_ALIAS / AUTO_DISCOVERED /
  WEAK_PREFIX / UNMATCHED / NO_BROKER_POSITION; confidence HIGH/MEDIUM/LOW/NONE; statuses
  VALID / VALID_WITH_WARNING / MAPPING_SUSPECT / UNRESOLVED / BROKER_ONLY /
  NO_MARKET_DATA / CURRENCY_UNRESOLVED (+ NO_BROKER_POSITION for watchlist assets).
  Price gate: both prices > 0 and comparable → warn ≥ 15 %, block ≥ 35 %
  (`mapping_price_warn_pct`, `mapping_price_block_pct`); differing known currencies with
  no FX → CURRENCY_UNRESOLVED (block); unknown currency + huge deviation still blocks.
  Weak/unresolved/currency/price-blocked ⇒ `is_actionable=false` ⇒ REVIEW_MAPPING,
  no BUY/SELL/TP/SL, no external-value math; broker value/P&L stays visible as
  broker-provided. Auto-discovered assets never written back to config.
- **E (data quality):** keep 0–100 score, add hard states OK / PARTIAL / STALE_MARKET_DATA /
  NO_PRICE_DATA / BROKER_ONLY / MAPPING_SUSPECT / UNRESOLVED_MAPPING /
  CURRENCY_UNRESOLVED / PROVIDER_FAILED. Hard blocks override any score. Missing RSI/MA
  stay null (never neutral defaults); missing news ⇒ `sentiment_fallback=true` label,
  not silent 50. Stale history (> `stale_days`, default 7) and insufficient history
  (< 14 bars ⇒ incomplete technicals) gate ADD/REDUCE.
- **F (actions):** canonical vocabulary ADD_CANDIDATE / HOLD / WAIT / REVIEW /
  REDUCE_CANDIDATE / DATA_UNAVAILABLE / REVIEW_MAPPING (+ legacy BUY/SELL/WATCH kept in
  JSON for compatibility). Guards: RSI ≥ overbought blocks ADD; sell−buy ≥ margin blocks
  ADD (and mirror for REDUCE); tech floor blocks ADD on weak/neutral-default signals;
  neutral band ⇒ HOLD; overbought-but-valid ⇒ WAIT; elevated-but-sub-threshold sell ⇒
  REVIEW. Strict/soft split removed from the main report (it was tautological); JSON keeps
  raw probabilities so any consumer can recompute.
- **G (levels):** TP/SL only when actionable + price/currency known; LONG_RUN_DCA ⇒ none
  (thesis note); SHORT_TERM_TRADING ⇒ existing MA/percent method labeled informational +
  method/currency/timestamp; other groups ⇒ none. Never for blocked mappings.
- **H (outputs):** one canonical result → `portfolio_report.json` (schema_version, run_id,
  timings, exit code, provider statuses, broker summary, mapping audit, DQ breakdown,
  actions, AI trace), concise `portfolio_report.md` (snapshot → attention ≤10 →
  action tables → reconciliation → provider notes + disclaimer), `data_quality.md`
  (audit), `run_manifest.json`. Layout `reports/latest/` + `reports/archive/YYYY-MM-DD/<ts>_<runid>/`;
  legacy `reports/portfolio_analysis.{md,json}` kept as compat copies. Atomic writes
  (tempfile + `os.replace`); latest published only after all artifacts ready.
- **I (AI):** unchanged chain + warmup; added: `--no-ai` skips warmup; per-stage trace
  (backend/model/latency/ok) into manifest; hallucinated-ticker rejection kept; AI never
  mutates scores/actions; AI failure ⇒ deterministic fallback text, run continues.
- **J (logging):** `run_id` on every phase line:
  `INFO run_id=… phase=… status=… duration_s=… …`; WARNING for suspect mapping/provider
  gaps; DEBUG for traces; secret redaction helper for exceptions/URLs/headers.
- **K (HTTP):** explicit timeouts everywhere in our code; transient-only retry helper
  (connection/timeout/408/429/5xx + Retry-After) applied to Trading212 reads; no retry on
  401/403/404/config errors; provider outcomes in manifest. yfinance keeps its internal
  client (documented limitation).
- **L (tests):** `tests/test_v4_hardening.py` (pytest, no network/secrets) covering config,
  migration, mapping, price gate, action gates, output/atomic/dry-run, CLI incl. lock
  conflict and offline validate-only.

## 4. Compatibility notes

- Entry point unchanged: `portfolio_ai_assistant.py`; bare run behaves as before (full run).
- Legacy `reports/portfolio_analysis.{md,json}` still published (copies of new outputs).
- JSON keeps legacy `ai_recommendation` (BUY/SELL/WATCH) + `assets` fields; canonical
  `action` + `blockers` are additive.
- Scheduled command becomes `py -3.12 portfolio_ai_assistant.py --scheduled`
  (`run_full_report.bat` updated accordingly); Task Scheduler should also be set to
  “Do not start a new instance” as a second barrier.

## 5. State definitions

- Mapping methods: EXACT_CONFIGURED_SYMBOL, EXACT_NORMALIZED_SYMBOL, EXPLICIT_ALIAS,
  AUTO_DISCOVERED, WEAK_PREFIX, UNMATCHED, NO_BROKER_POSITION.
- Confidence: HIGH / MEDIUM / LOW / NONE.
- Mapping statuses: VALID, VALID_WITH_WARNING, MAPPING_SUSPECT, UNRESOLVED, BROKER_ONLY,
  NO_MARKET_DATA, CURRENCY_UNRESOLVED, NO_BROKER_POSITION.
- DQ states: OK, PARTIAL, STALE_MARKET_DATA, NO_PRICE_DATA, BROKER_ONLY, MAPPING_SUSPECT,
  UNRESOLVED_MAPPING, CURRENCY_UNRESOLVED, PROVIDER_FAILED.
- Actions: ADD_CANDIDATE, HOLD, WAIT, REVIEW, REDUCE_CANDIDATE, DATA_UNAVAILABLE,
  REVIEW_MAPPING (legacy BUY/SELL/WATCH retained as derived display fields).
- Run statuses: SUCCESS, SUCCESS_WITH_WARNINGS, PARTIAL_SUCCESS, FAILED,
  SKIPPED_ALREADY_RUNNING.

## 6. Exit codes

| Code | Name | Meaning |
|---|---|---|
| 0 | SUCCESS | full success |
| 1 | FATAL | runtime failure, no usable final report |
| 2 | CONFIG_ERROR | invalid config/env/CLI selection |
| 3 | ALREADY_RUNNING | skipped, another run holds the lock |
| 4 | PARTIAL | usable report with provider/data warnings |
| 5 | BROKER_FAILED | broker sync failed; report states data unavailable/stale |

Implemented as `RunExit` IntEnum in `v4_hardening.py` (new module for lock, exit codes,
gates, audit, atomic writes, redaction — no heavy third-party imports, unit-testable).
