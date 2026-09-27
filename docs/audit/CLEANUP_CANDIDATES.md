# Cleanup Candidates

> Proposal only. No files modified. Generated 2026-09-24.
> Confidence: high = proven zero prod importers + tests green without it; medium = needs caller/contract check; low = needs design decision.
> Suggested actions: KEEP / REFACTOR_LATER / DEPRECATE / MOVE_TO_EXPERIMENTAL / REMOVE_AFTER_VERIFICATION / INVESTIGATE.

## 1. Dead snapshots (high confidence — remove after hash verification)

### `investment_engine/portfolio/broker_first_V2415_Sep-24-1106-2026_1.py` (1070L)
- Why: versioned copy of `broker_first.py` (1109L); current adds `to/from_reconciliation_dict:337-375`.
- Evidence: `grep _V2415` → zero prod/test importers; line-count diff +39.
- Callers: none. Tests: none.
- Replacement: keep `broker_first.py` (newer).
- Migration: `sha256` both → archive snapshot outside repo → delete file → `pytest tests/test_broker_first.py tests/test_reconciliation_consistency.py -q`.
- Rollback: restore from archive.
- Confidence: **high**

### `investment_engine/reporting/regime_report_V2415_Sep-24-1106-2026_1.py` (1809L)
- Why: copy of `regime_report.py` (1818L); current adds `news_context_path:1242` + `## News Context:1281-1286`.
- Evidence: zero importers; diff −9 lines.
- Migration: hash → archive → delete → `pytest tests/test_unified_report.py tests/test_fail_safety.py tests/test_documents.py -q`.
- Rollback: restore.
- Confidence: **high**

### `investment_engine/research/news_engine_V2415_Sep-24-1106-2026_1.py` (1076L)
- Why: copy of `news_engine.py` (1195L); current adds `fetch_slovak_news` + Reddit block (~119 lines).
- Evidence: zero importers.
- Migration: hash → archive → delete → `pytest tests/test_news_hardening.py tests/test_market_regime.py -q`.
- Rollback: restore.
- Confidence: **high**

### `investment_engine/reporting/failed_tickers_V2415_Sep-24-1106-2026_1.py` (111L)
- Why: copy of `failed_tickers.py` (135L); current adds `HELD_UNRESOLVED/WATCHLIST/STALE_ALIAS` filter.
- Evidence: zero importers; `test_reconciliation_consistency.py` pins new behavior.
- Migration: hash → archive → delete → `pytest tests/test_reconciliation_consistency.py -q`.
- Rollback: restore.
- Confidence: **high**

### `data/cache/news_google_rss_V2415_*.json`, `t212_cashflow_history_V2415_*.json`
- Why: cache snapshots; regenerable.
- Migration: delete (caches rebuild) — keep one if needed for forensics.
- Confidence: **high**

## 2. Dead placeholder / pipes (high confidence)

### `investment_engine/trading212_integration.py` (86L)
- Why: wrong auth model (`Bearer api.trading212.com`), `get_price_stats/get_candles` never called.
- Evidence: `grep trading212_integration` (engine path) → zero importers; production uses root `trading212_integration.py`.
- Migration: delete → `pytest tests/ -q` → `grep -r investment_engine.trading212_integration` must stay empty.
- Rollback: restore file.
- Confidence: **high**

### `investment_engine/news/engine.py` (81L, `normalize_news`)
- Why: lossy post-filter (score 0, drops undated, all-`UNKNOWN` ticker); main uses `research.news_engine`.
- Evidence: zero prod importers.
- Migration: delete or move to `tools/` → full suite.
- Rollback: restore.
- Confidence: **high**

### `investment_engine/pipeline/engine.py` (`InvestmentPipeline`)
- Why: sole consumer of prompt bundle; zero importers; ctor builds provider (heavy).
- Evidence: `grep InvestmentPipeline` → only definition.
- Migration: move to `tools/` or delete → full suite.
- Rollback: restore.
- Confidence: **high**

### `investment_engine/memory/store.py` (`MemoryStore`)
- Why: zero importers; ctor mkdir+writes `memory.json` (side effect).
- Evidence: `grep MemoryStore` → only definition + `memory.json` data file.
- Migration: move to `tools/` or delete (keep `memory.json` if needed).
- Rollback: restore.
- Confidence: **high**

## 3. Shadow / legacy T212 (medium — accounting-sensitive)

### `trading212/` package (`auth 237L / integration 448L / portfolio 286L / __init__`)
- Why: diverged copies of root trio; `portfolio.py` lacks recon/FX/catalog; `integration.OllamaClient` byte-identical twin; main uses root, not package.
- Evidence: `main.py:96,461,934,1055,2069` → root; zero prod imports of `trading212.*` package.
- Tests: none import package directly.
- Replacement: one home (recommend root names → `trading212/` OR `investment_engine/broker/`); root R1 kept as thin shim during transition.
- Migration: (1) add canonical-import aliases, (2) switch `main.py` + `broker_first.py:677` to package, (3) update 4 test files, (4) delete emptied twins. Per-file, with suite green at each step.
- Rollback: restore shim (keep both until Phase-3 green).
- Confidence: **medium** (touches FX/recon paths)

### Root `trading212_portfolio.py` R1 block (`:488-495`, `:586-591`)
- Why: `max(€1,0.1%)` abs no-dedupe PASS can contradict canonical `min(0.1%,€2)` FAIL; empty→PASS vs UNKNOWN.
- Evidence: formula diff + `test_unified_report.py:61-76` legacy-threshold fixture; both statuses exposed.
- Replacement: delegate to `broker_first.reconcile_snapshot` (v2 block already does `:537-554`).
- Migration: keep fields as deprecated aliases for one release → flip → remove after report-parity tests.
- Rollback: revert block (aliases preserved).
- Confidence: **medium**

### `OllamaClient` twins (`trading212_integration.py:37-112` + `trading212/integration.py:37-112`)
- Why: orphaned from `run_engine` (only manual `analyze_portfolio()` CLI); duplicates factory chain.
- Evidence: no `main.py` caller; `__main__` CLI only.
- Migration: move one copy to `tools/t212_cli.py`, delete the other → verify CLI still works manually.
- Rollback: restore.
- Confidence: **medium**

## 4. Tests-only ledger stack (medium — move, don't delete)

### `investment_engine/accounting/{models,ledger,lot_matching,fees,performance,reconciliation,reporting,importers}` (8 modules)
- Why: zero production importers (`cashflows` is separate and stays); prepared for unwired `--ledger` flow.
- Evidence: `grep` prod importers → none; 5 test files cover them.
- Replacement: `tools/ledger/` or `experimental/ledger/` (keep `accounting/cashflows.py` + `accounting/README.md` in place).
- Migration: move + fix imports in 5 test files → both suites green → confirm `run_engine` untouched.
- Rollback: move back.
- Confidence: **medium** (import churn, no behavior change)

### `investment_engine/reporting/integrated_report.py` + legacy `AIContextBuilder`
- Why: `generate_full_integrated_report` has no prod caller; legacy builder superseded by `AIContextReportBuilder`.
- Evidence: callers = tests + re-export only.
- Migration: move to experimental/tools with its tests → full suite.
- Rollback: restore.
- Confidence: **medium**

### `investment_engine/discovery/engine.py`
- Why: sole importer is `test_news_hardening.py:229`; production discovery is inline prompt.
- Evidence: prod grep → none; unsafe `except→None:5-8`.
- Migration: move to experimental with its test → or wire into `run_engine` intentionally (Phase 4 decision).
- Rollback: restore.
- Confidence: **medium**

## 5. Prompt / schema / report hardening (low-medium — design + tests first)

### `investment_engine/prompts/` bundle vs inline prompts — INVESTIGATE
- Why: 6 MDs + loader used only by dead pipeline; main builds inline prompts; double system roles.
- Migration: decide single registry (Phase 5) → migrate one stage at a time with golden-prompt tests.
- Confidence: **low** (LLM behavior change)

### `schemas/ai_recommendations.py` `List[Any]` + dead `sanitize_ai_output` — REFACTOR_LATER
- Why: nested trade models unwired; `validate_cash_deployment` checks `isinstance dict` only.
- Migration: type lists, activate sanitizer, add schema tests (Phase 5).
- Confidence: **low-medium**

### `report_structure.py` duplicated `_collect_data_quality_flags` (`:347` + `:455`) — REFACTOR_LATER
- Migration: merge + brief-parity test.
- Confidence: **medium**

### `main.py` duplicated `_is_us_symbol` (`:84`+`:383`), `_symbol` (`:80`+`:389`) — REFACTOR_LATER
- Migration: merge + suite green.
- Confidence: **high** (mechanical)

### `portfolio_ai_assistant.py:100,123` undefined `logger` — fix before any cleanup
- Why: `NameError` on archive-copy failure path.
- Migration: `logger = logging.getLogger(__name__)` at module level or pass logger in.
- Confidence: **high**

### Stale `reports/latest/*` V4 layout — INVESTIGATE
- Why: schema/flags/exits mismatch current `write_reports`; confuses consumers.
- Migration: delete + document as historical, OR reimplement `latest/` intentionally (Phase 3).
- Confidence: **low** (consumer impact unknown — check Task Scheduler + readers first)

### `test_ticker_mapping.py` BOM (`U+FEFF` line 1) — fix
- Migration: save as UTF-8 without BOM → collect clean.
- Confidence: **high**

## 6. Do-NOT-touch list (until Phase 1-2 exit criteria)

`broker_first.py`, `symbols.py`, `regime_report.py` (rows/recon/safety/priority), `documents.py`, `cashflows.py`, `factory/fallback/presets/lmstudio.py`, `news_engine.Strict`, `market_data` gate, `market_regime`, `peak_valley`, `portfolio_config.json` thresholds, `PIEs/*.csv`, `strategy.json`, `experimental/*` internals.
