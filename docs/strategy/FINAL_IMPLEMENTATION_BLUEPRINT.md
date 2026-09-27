# Final Implementation Blueprint

> Status note (build session): user policy defaults from the follow-up direction are
> recorded below under Preliminary User Policy Defaults as PLANNED / NOT YET BUILT
> until each phase lands them. Nothing in this document claims policies are implemented.

## Executive Decision

### Recommended target architecture

Keep the current pipeline shape (`portfolio_ai_assistant.py` → `investment_engine/main.py:run_engine` → broker-first snapshot → unified rows → canonical signals → LLM sections → layered reports) and harden it layer by layer instead of rewriting:

- **Canonical financial core:** `investment_engine/portfolio/broker_first.py` (`build_account_snapshot:504`, `deduplicate_positions:700`, `reconcile_snapshot:723`) + `investment_engine/portfolio/symbols.py` (sole display/Yahoo/support mapping) become the only authority for money math and identity.
- **One deterministic risk engine:** extend the existing `apply_trade_safety` / `apply_fail_guards` pattern in `investment_engine/reporting/regime_report.py:202-360` into a standalone, LLM-independent eligibility gate with a user-supplied investment-policy config.
- **One indicator policy:** manual engine canonical, pandas-ta reference-only (Option A, §Phase 3).
- **One research corpus:** new normalized `ResearchItem` pipeline fed by adapter classes; existing `StrictNewsFetcher` (`research/news_engine.py:225`) + `EnhancedNewsFetcher` + `NewsContextBuilder` (`research/news_sources.py:108,453`) migrate behind adapter interfaces.
- **Skills system:** new `investment_engine/skills/` registry with versioned prompts, JSON schemas, and contract tests; first four skills in Phase 6, six later.
- **Report topology:** `current/` + `ai_context/` + `debug/<run_id>/` + `archive/<run_id>/` (see §Phase 4).
- **Backtest stays isolated** in `experimental/backtest/`; gains walk-forward OOS harness in Phase 8, never wires into live paths.

### Why this order

1. **Money correctness first (Phases 1–2).** Every downstream layer (rows, signals, briefs, order plans) inherits reconciliation/dedupe/identity. Fixing reports or skills before the core would bake divergent numbers into contracts and tests.
2. **Determinism before intelligence (Phases 3–4).** Cross-PC indicator parity and single-schema reports/manifests make LLM outputs comparable and testable; otherwise skill evaluation is noise.
3. **Evidence before judgment (Phase 5).** Skills with strict evidence requirements need the corpus (IDs, URLs, timestamps, tiers) to exist first — otherwise "evidence enforcement" is unenforceable.
4. **Judgment before proposals (Phases 6–7).** Risk-manager and analyst skills must exist before advisory order plans; order plans need the policy config and eligibility gates.
5. **Validation last (Phase 8).** Backtest evolution informs parameter choices only after live architecture is stable, preventing overfit-then-build.

### What not to build yet

- No order execution path, no broker write calls, no "confirm-and-send" UI — advisory text only, permanently until a separate, explicitly approved project.
- No Task Scheduler wiring (see user policy: not until runtime lock and lightweight/full run modes exist).
- No `pandas-ta` mandate, no TA-Lib, no FinBERT/`transformers`, no new scraper capabilities beyond currently used RSS/endpoints.
- No pie-weight optimizer, no auto-rebalancing, no price prediction claims.
- No migration of the CSV-ledger stack into production (stays tests-only until a dedicated `--ledger` project).

## Current-State Constraints

Non-negotiable constraints discovered by audit:

1. **Broker truth.** Trading 212 owns: total equity, free/blocked/pie cash, quantity, average entry price, current broker price, EUR market value, broker P&L + FX P&L. External data (yfinance, FinViz, TradingView scrape) is analytics-only and gated (`VERIFIED` + same-currency + native price) before any technical comparison.
2. **No auto execution.** No order endpoints exist in the repo; nothing may add them in this program. All future BUY/SELL/limit/stop text is advisory and requires explicit human confirmation outside the program.
3. **Experimental isolation.** `experimental/` has zero imports of `investment_engine`/`trading212`/`yfinance`/`requests` and production has zero imports of `experimental` (verified by grep). Any phase that breaks this fails closed.
4. **Secrets/Git restrictions.** `.gitignore` + `api.env.example` + verifier exist (Phase 0). `api.env`, `reports/`, `logs/`, `data/cache/`, `runtime/` must never be committed (verified via `git check-ignore`). User-specific config: commit templates only.
5. **Multi-PC environment.** Python 3.12 pinned by launchers + `check_python_version()` gate in `portfolio_ai_assistant.py`; deps unpinned (`>=` only); `pandas_ta` PRESENT on one PC but commented in `requirements.txt` (other PC = manual path); `talib` absent; `tzdata` now core; Tailscale IPs for local models assume VPN.
6. **Report drift.** README documents only real flags (`--config`, `--investment-engine`, `--generate-full-report`); `reports/latest/` V4 layout is stale and must be retired or reimplemented (Phase 4).
7. **Reconciliation divergence (proven).** Legacy R1 (`trading212_portfolio.py:488-495`: `max(€1,0.1%)`, abs, no dedupe, empty→PASS) vs canonical R2 (`broker_first.py:723-805`: `min(0.1%,€2)`, signed, ISIN dedupe, empty→UNKNOWN) vs report-local R3 (`regime_report.py:145-199`, inherits summary status) vs ledger R4 (`accounting/reconciliation.py`, deposits €10 / positions 5%). Both R1 and R2 statuses are currently exposed.
8. **Dedupe divergence (proven).** Canonical key `(account,ISIN)` else `(account,broker_id)`; unified rows key `symbol.upper()`. Same ISIN under two tickers counts once canonically, twice in rows.
9. **Provider attribution gap.** `manifest["provider"]` is actually `brief_metadata.stance`; `ChainedFallbackProvider` (`providers/fallback.py:119-183`) strips `context["model"]`; only `LMStudioProvider` honors presets/stage sampling. Per user policy, LM Studio is temporarily disabled for testing until the user enables it; the actual winning provider/model per stage must be recorded.
10. **God modules.** `main.py` (2722L), `regime_report.py` (1818L) — split only after behavior is pinned by contract tests, never before.

## Target Architecture

### Proposed directory tree

```text
portfolio_ai_assistant.py              # thin CLI: gate → settings → run_engine → publish
investment_engine/
  config/            settings.py + strategy.json          # env loading isolated
  portfolio/         broker_first.py                      # CANONICAL money math
                     symbols.py                           # CANONICAL identity/mapping
                     pie_metadata.py + sidecar.py + exposure.py
  research/          market_data.py                       # Yahoo gate + light technicals
                     indicators.py                        # canonical manual engine (Phase 3)
                     technical_analysis.py                # pandas-ta reference path + FinViz
                     peak_valley.py + market_regime.py
                     news_engine.py                       # Strict fetcher
                     news_sources.py                      # Enhanced fetchers + corpus builder
                     web_researcher.py                    # opt-in Playwright
                     corpus/                              # Phase 5: item.py + adapters/ + tiers.py + freshness.py + dedupe.py
                     pies.py + sector_templates.py (+ healthcare sector set, Phase 5)
  providers/         factory.py + fallback.py + presets.py + transports/*
                     attribution.py                       # Phase 4: per-stage winner/model recorder
  accounting/        cashflows.py                         # sole prod accounting module
  reporting/         regime_report.py                     # rows/recon/safety/priority
                     documents.py                         # render_brief / render_snapshot
                     report_structure.py                  # current/ai_context/debug/archive layers
                     failed_tickers.py
  risk/              # Phase 7: policy.py + eligibility.py + sizing.py + order_plan.py
  skills/            # Phase 6: registry.py + contracts.py + */ per-skill dirs
                     prompts/                             # versioned *.md per skill, registry-loaded only
  schemas/           ai_recommendations.py (typed) + research_item.py + order_plan.py
  scoring/ + prioritization/  deterministic pre-LLM rank
tools/               ledger/ (moved CSV stack) + t212_cli.py (moved OllamaClient) + diagnostics/
scripts/             verify_environment.py + verify_indicators.py (Phase 3)
experimental/        backtest/ (unchanged isolation) + walkforward.py (Phase 8)
reports/ current/ + ai_context/ + debug/<run_id>/ + archive/<run_id>/
investment_policy.example.json         # Phase 7 template (real file gitignored)
```

### Domain boundaries

| Domain | Owner module | May import | Must never import |
|---|---|---|---|
| Money math / recon | `portfolio/broker_first.py` | `symbols.py`, catalog, root FX (until Phase 2) | `regime_report`, `main`, LLM, news |
| Identity / mapping | `portfolio/symbols.py` | stdlib only | everything broker/LLM (pure) |
| Report rows / safety | `reporting/regime_report.py` | `broker_first` (result types), `symbols`, research DTOs | providers, `main` |
| Presentation | `reporting/documents.py`, `report_structure.py` | `regime_report` outputs | providers, T212 clients |
| Research fetch | `research/*`, `research/corpus/` | network libs, cache | `portfolio`, providers, `main` |
| LLM transport | `providers/*` | `requests`, settings | research, portfolio, reporting |
| Skills | `skills/*` | schemas, corpus DTOs, risk eligibility | T212 clients, network directly (via adapters only) |
| Risk / order plans | `risk/*` | policy config, rows, technicals | providers, network |
| Backtest | `experimental/*` | stdlib/pandas/numpy internally | everything above |

### Canonical data contracts

- `AccountSnapshot → ReconciliationResult` (`broker_first.py`): only money-math producer.
- `UnifiedRow`: `(display, company, qty, value_eur, pnl, weight, signal, technicals, earnings, notes, identity_provenance)`; identity = ISIN-first after Phase 1.
- `ResearchItem`: the 18-field schema (Phase 5).
- `CanonicalSignals`: `{display: signal}` + provenance; single map shared by brief/snapshot/priority/PIE sections.
- `OrderPlan` (Phase 7): advisory-only schema with `no_execution` guard fields + `human_confirmation_required: true`.
- `RunManifest`: `{run_id, generated_at, recon, files{sha256}, counts, providers_per_stage{winner,model}, indicator_engine, versions, skill_versions, policy_version}`.

### Source-of-truth table

| Fact | Source of truth | Consumers must not use |
|---|---|---|
| Equity/cash/qty/avg/price/value/P&L | T212 via `broker_first` snapshot | yfinance/FinViz/TV prices |
| Recon status/delta | `reconcile_snapshot` | R1 fields, R3 recompute, ledger status |
| Position identity | ISIN-first (`symbols` + catalog) | raw broker ticker, Yahoo symbol |
| Technical values | canonical manual engine (Phase 3) | pandas-ta numbers in prod |
| News evidence | `ResearchItem` corpus | raw feed dicts, undated items |
| Signals | canonical map | per-section LLM restatements |
| Risk decisions | `risk/eligibility` | LLM prose, social sentiment |
| Attribution | `providers/attribution` | configured model names |

### Import direction rules

`CLI → main → {reporting → {portfolio → symbols}, research, providers, risk, skills} → schemas/config`. Never upward; never sideways between portfolio↔research↔providers. Deferred (function-level) imports allowed only for the T212 bridge during Phase 2 and removed after.

### Forbidden dependency rules

1. No `experimental` import outside `experimental/` (AST-checked in CI).
2. No T212 client import in `skills/`, `risk/`, `research/`, `providers/`.
3. No network import in `portfolio/`, `risk/`, `schemas/`, `reporting/`.
4. No `pandas_ta`/`talib` import outside `technical_analysis.py` + `scripts/verify_indicators.py`.
5. No `os.environ` value reads in reporting/render paths (presence checks only, in config/factory).
6. No order-placement capability anywhere — grep-guarded (`place_order|submit_order|execute_order` outside tests).

## Phase 0 Validation

Phase 0 delivered: `.gitignore`, `api.env.example`, logger fix + 3.12 gate, `scripts/verify_environment.py`, `tzdata` + comments, truthful README, 10 new tests, `docs/migration/PHASE0_FOUNDATION.md`. 271 + 52 tests pass; verifier exit 0 on this PC.

Acceptance criteria before Phase 1 starts:

1. First commit exists and contains `.gitignore` (verify: `git log --oneline`, `git check-ignore -v api.env reports logs data/cache` all match). Per user policy: first commit approved after Phase 0 verification.
2. `scripts/verify_environment.py` exit 0 on **both** PCs; second PC confirms manual-indicator path (`pandas_ta: MISSING` expected there).
3. `pip check` clean on both PCs; `tzdata` installed.
4. `pytest tests/ + experimental/tests/` green on both PCs (323 collected baseline).

Stop conditions: any secret in `git status`; verifier exit 2 (exposed); verifier exit 1 unresolved (missing core); single-PC-only verification (Phase 3 needs both engines observed).

## Phase 1: Canonical Financial Core

Goal: one reconciliation, one identity, one audit policy.

- **Reconciliation unification.** `reconcile_snapshot` becomes the sole producer of status/delta/tolerance. `regime_report.compute_reconciliation:145` becomes a thin view (recompute from unified rows for display, but status yielded from canonical result; never inherits legacy R1). Ledger `reconcile_ledger` is documented as *historical ledger audit* and guarded from influencing live status (add explicit guard + test).
- **Dedupe identity unification.** `build_unified_portfolio_rows:389` switches ticker-key dedupe to ISIN-first (`dedupe_key` from `broker_first`), with broker-id fallback and alias collapse via `symbols`. Rows carry `identity_provenance: ISIN/BROKER_ID/ALIAS`.
- **Legacy R1 disposition.** Keep `trading212_portfolio.py:488-495` fields for one release as deprecated aliases (`reconciliation_status_legacy`, `reconciliation_threshold_legacy`), computed but unconsumed; log a deprecation warning; remove in Phase 2 after report-parity tests pass on both threshold regimes.
- **Broker audit policy.** Every run persists: `expected/open-positions/dedup/raw/excluded` sums, delta, tolerance, `data_quality`, per-position `FxAudit`, mapping status/confidence. Empty snapshot → `UNKNOWN`, never PASS. Mismatch never adjusts broker values — only gates downstream.
- **Report consistency.** Brief, snapshot, JSON, manifest, human brief, AI context all read the same `ReconciliationResult` object passed through `run_engine` result dict; add a cross-artifact consistency test.
- Migration steps: (1) add Phase-1 tests against current code — new ISIN-dedupe + thin-view tests fail, rest pass; (2) implement thin view + row dedupe + R1 aliases; (3) run both suites + new tests on both PCs; (4) remove R1 consumption (keep computed aliases); (5) update audit docs.
- Test plan: `test_reconciliation_unification` (single producer: all artifacts equal), `test_isin_dedupe_rows` (alias fixture → one row), `test_r1_alias_deprecated` (aliases present, unconsumed), `test_empty_never_pass`, `test_mismatch_never_adjusts_broker`, `test_ledger_cannot_flip_live_status`.
- Rollback: revert `regime_report.py` + `broker_first.py` hunks (behavior pinned by pre-existing `test_broker_first`, `test_reconciliation_consistency`, `test_unified_report`, `test_fail_safety`); R1 aliases keep old consumers alive during transition.
- Acceptance: new tests green both PCs; `grep` shows zero live consumers of R1 fields; cross-artifact recon equality holds on fixtures and last live-shaped snapshot.

## Phase 2: T212 Layer Consolidation

Three homes exist: root trio (used by `main.py` and `broker_first.py:677`), `trading212/` package (shadow, unused by prod), dead `investment_engine/trading212_integration.py` (zero importers).

- **Recommended canonical home:** `trading212/` package (already the documented layout in README). Move root logic into it file-by-file, preserving behavior; root files become thin re-export shims for one release, then are removed.
- **Compatibility wrappers.** `trading212_portfolio.py` keeps `parse_position`/`PortfolioMonitor`/`get_live_fx_rates` names re-exported; `broker_first.py:677` deferred import retargeted to `trading212.portfolio`; `main.py` imports retargeted in the same change.
- **Exact migration sequence:** (1) delete dead `investment_engine/trading212_integration.py` + test; (2) move auth extras into `trading212/auth.py` (retry 5/20s + 429 backoff + history cursor + raw dump), unify retry policy, re-export shim, tests; (3) move integration sync into `trading212/integration.py`, shim, tests; (4) move portfolio parse/FX/catalog into `trading212/portfolio.py`, R1 as deprecated alias, shim, full suites + live-shaped fixture run; (5) move `OllamaClient` twins to `tools/t212_cli.py` (manual CLI only), remove from package; (6) delete root shims after one green release.
- **Risk assessment:** HIGH for step 4 (FX/catalog/recon touch money math) — mitigated by Phase-1 pinning tests + byte-identical snapshot comparison on cached fixtures before/after; LOW for steps 1–2, 5.
- **Rollback:** restore root files from git (committed Phase 0/1 states); shims guarantee old import paths keep working until step 6.

## Phase 3: Indicator and Environment Consistency

### Compare technical-engine options

- **A. Manual canonical + pandas-ta parity/reference (RECOMMENDED).** Production always uses `_apply_manual_indicators` (extracted to `research/indicators.py`); pandas-ta path retained behind an explicit opt-in flag for validation only; `market_data.py` EMA aligned to `adjust=False`; experimental Wilder/stateful as parity reference with documented tolerances.
- **B. pandas-ta mandatory/pinned.** Rejected: numba build chain on Windows, version-sensitivity, install weight, no accuracy need for regime use.
- **C. Vendor-neutral formula lock.** Folded into A: parity spec cites formulas AND the experimental reference implementation.

### Chosen policy (A)

- Extract manual engine to `investment_engine/research/indicators.py` with frozen defaults, documented formulas (`adjust=False`, SMA-RSI variant, simplified ADX, static Supertrend, rolling-20 VWAP, BB `ddof=1`).
- `TechnicalAnalyzer` gains `engine: "manual" | "reference"` (default `"manual"`); `"reference"` requires installed pandas-ta else raises a clear error.
- Requirement changes: none mandatory; add `scripts/verify_indicators.py` (frozen OHLCV fixture → indicator snapshot hash; identical output on both PCs); manifest gains `indicator_engine`, `pandas_ta_present`, `talib_present`, `pandas/numpy/scipy` versions.
- Parity test spec: manual vs experimental-Wilder on frozen fixture with per-indicator tolerances (RSI ±2.0, ATR ±1%, ADX direction-agreement, Supertrend last-bar state-agreement, SMA/EMA/BB exact to 1e-9).
- Multi-PC verification: `verify_indicators.py` + both suites on each PC; manual outputs byte-identical.
- Rollback: revert extraction (re-export shim keeps imports working); engine flag defaults preserve current per-PC behavior.

## Phase 4: Report and Artifact Architecture

- Migrate to target topology (`current/` + `ai_context/` + `debug/<run_id>/` + `archive/<run_id>/`): add `REPORT_LAYOUT=target|legacy` env (default `legacy` during migration), implement writers for `current/portfolio_intelligence_brief.md` (NEW), move existing four + manifest under `current/`, corpus files under `ai_context/`, per-run debug under `debug/<run_id>/`, full-run copies under `archive/<run_id>/`; retire `reports/latest/` V4 files (archive outside git first); flip default after parity tests.
- **Human brief exact content rules:** max ~60 lines; sections Portfolio state → EXI2 regime → Top risks/opportunities (≤5 bullets) → Priority actions (deterministic table only) → Watchlist; account-level FAIL/DEGRADED warning ONCE at top, never per position; no sized orders, no invented prices, no per-ticker prose duplication; fixed stance vocabulary (`HEALTHY/DEGRADED/FAIL`), merging the two stance functions. Language proposal: English internal data/contracts + Slovak human brief (pending explicit confirmation — see Deferred).
- **Provider attribution schema** (`providers/attribution.py`): per stage `{provider, model_requested, model_served, fallback_depth, latency_ms, error_class}`; manifest `providers_per_stage` + methodology block show winner models (per user policy: LM Studio temporarily disabled for testing until user enables it — recorder must represent the disabled state explicitly); fix `manifest["provider"]` mislabel to `stance`; deterministic-fallback sections labeled `deterministic(profile)`.
- **Report consistency contract** (test-enforced): all artifacts share recon status/delta, canonical signals, row count, `run_id`; atomic writes + sha256; merge duplicated `_collect_data_quality_flags`.
- Migration and tests: `test_report_topology`, `test_brief_brevity_rules`, `test_attribution_truth`, `test_manifest_schema`.

## Phase 5: Research Corpus

- **Normalized ResearchItem schema** (18 fields: `id`, `title`, `canonical_url`, `publisher`, `published_at_utc`, `age_hours`, `source_tier`, `source_category`, `ticker_tags`, `sector_tags`, `region_tags`, `clean_extract`, `raw_extract_if_available`, `credibility`, `relevance_score`, `sentiment_if_available`, `fetch_status`, `failure_reason_if_any`) as `schemas/research_item.py` (Pydantic, `extra=forbid`). Undated items are `fetch_status=UNDATED`, never decision-relevant.
- **Source adapter architecture:** `research/corpus/adapters/*.py` each implementing `fetch(query) → list[ResearchItem]` with per-adapter timeout, error→`FAILED` item (never raise), frozen-fixture mode for tests. Existing fetchers migrate behind adapters (fix Enhanced HTML-`now()` stamping to `UNDATED`).
- **Source tiers:** T0 broker/T212 + official IR/exchange; T1 major wire + exchange notices + regulator (SEC/FDA/Fed/ECB); T2 established financial press + TradingView/Finviz data pages; T3 social/forums/ideas (LOW_CONFIDENCE only). Decision table: T0–T2 only; T3 → sentiment appendix.
- **Freshness rules:** decision-relevant = `age_hours ≤ 48` timezone-aware; older = `BACKGROUND`-labeled only; align `filter_decision_news` 72h looseness to 48h; earnings/analyst items carry event dates separately.
- **Deduplication:** canonical-URL + fuzzy title (≥0.85 keeps first-seen); cross-adapter shared seen-store per run.
- **Caching/rate limits:** per-adapter file cache with TTL, thread-pool caps preserved, per-host 429 backoff, `BLOCKED` items recorded.
- **Failure isolation:** every adapter failure yields a `FAILED` item; corpus builder never aborts the run; degraded-coverage note.
- **Full corpus vs human curation:** `research_corpus_<run_id>.{md,json}` (everything, tier-labeled) vs curated decision inputs (≤3/symbol tier-first) vs human brief (headlines only). Three files, documented thresholds.
- **Required research domains:** company/portfolio news; earnings dates/results (wire `WebResearcher.fetch_all_earnings` into an adapter; keep Yahoo fast path); analyst revisions (keyword adapter now, structured parsing P1); regime/macro; Trump/policy + White House/tariffs/China/Taiwan/chips (extend keyword map + 6-category tracking); rare earths/minerals/uranium/nuclear; oil/gas/electricity; renewables/wind/solar/EV/batteries/lithium/copper; healthcare/biotech/FDA/pricing (NEW template set; full adapters planned per user policy); AI/semis/export controls; crypto/regulation/ETF flows (full adapters planned per user policy); social (sentiment appendix only, never actionable alone).
- **Source compliance caveats:** Google News RSS (headlines+links only, rate limits); Reddit JSON (user-agent + limits, no auth-circumvention); Slovak press RSS (HTML full-text only where ToS allow); Finviz/TradingView/EarningsHub HTML (ToS-sensitive — Playwright opt-in, low frequency, data-facts only); X/StockTwits (no scraping — official channels or drop flags; priority order pending user confirmation). Every adapter gets a `COMPLIANCE` header + blocked fallback.
- **Tests and acceptance:** schema, 47.9/48.1h boundaries, cross-adapter dedupe, failure isolation, tier gating, background labeling, per-domain coverage; frozen-fixture corpus byte-stable; live run shows per-domain counts with zero undated decision items.

## Phase 6: Skills System

- **Skill registry architecture:** `investment_engine/skills/` with `registry.py` (`get_skill(name, version)`), `contracts.py` (shared validation, evidence checker, confidence aggregator), per-skill dirs `{manifest.yaml, prompt_vN.md, input.schema.json, output.schema.json, tests/}`. Prompts load ONLY via registry; migrate inline prompts into registry versions one stage per phase. Skill versions pinned in config; manifest records versions per run.
- **Contracts and schemas (all skills):** `purpose`, `input`, `evidence` (min items, max age, min tiers, URL+timestamp required), `allowed`/`forbidden`, `output` (JSON schema + `confidence: {value, basis[]}` + `unverified[]`), `fallback` (deterministic template, labeled).
- **First four skills — full specifications:**
  1. *PortfolioRiskManager.* Risk verdict (concentration, cash adequacy, FAIL posture). Input: rows, recon, exposure, regime. Evidence: broker snapshot + regime only. Allowed: HOLD/TRIM/WATCH + cash-target text. Forbidden: BUY sizing, price targets, per-ticker stories. Output: `{posture, breaches[], cash_action, confidence}`. Tests: cap-breach fixtures, FAIL posture, zero-cash (zero cash blocks BUY/ADD per user policy).
  2. *NewsEventAnalyst.* Per-ticker event classification. Input: ResearchItems (≤48h, T0–T2) + price context. Evidence: ≥1 URL+timestamp item per event; ≥2 independent for `CONFIRMED`. Allowed: event labels + watch guidance. Forbidden: price prediction, BUY/SELL calls. Output: `{events[{ticker, type, items[id], severity, confidence}]}`.
  3. *EquityResearchAnalyst.* Per-holding note. Input: rows + technicals + earnings + analyst items. Evidence: earnings/analyst items + VERIFIED technicals. Allowed: HOLD/ACCUMULATE/TRIM thesis text (no sizes). Forbidden: uncited targets, averaging-down endorsement (except under user-policy averaging conditions), unverified-mapping technicals.
  4. *InvestmentCommittee.* Reconcile the three outputs into decisions. Input: three skill outputs + canonical signals + risk eligibility. Evidence: must cite skill outputs + corpus IDs. Allowed: final action words (closed vocabulary) + priority rows. Forbidden: new tickers, contradicting canonical signals, sized orders (indicative sizes come only from Phase 7 plans). Tests: contradiction→blocked, unknown ticker→dropped, FAIL→watchlist-only.
- **Later skills road map:** 5 PolicyTrumpWatch, 6 EnergyCommoditiesAnalyst, 7 HealthcareAnalyst, 8 CryptoAnalyst (domain adapter subsets + classification schemas), 9 WatchlistDiscovery (validation-gated funnel, default max 3 ideas/run per user policy, WATCH-only), 10 TechnicalSetupPlanner (canonical indicators only, mapping-gated, no execution fields).
- **Prompt storage policy:** versioned `prompt_vN.md` per skill, changelog in manifest, loader validates pin, A/B only via explicit config flag with manifest record.
- **Provider model routing per skill:** RiskManager/Committee → `decision_model`; NewsEvent/Equity → `decision_model`, brief-facing summary → `writer_model`; all via `ChainedFallbackProvider` with attribution; deterministic fallback templates per skill (labeled).
- **Evidence enforcement:** `contracts.require_evidence()` runs before prompt render (missing → `INSUFFICIENT_EVIDENCE` output, never prose); post-parse validator rejects outputs citing unknown item IDs.
- **Structured outputs:** Pydantic schemas with `extra=forbid`, typed lists (fix current `List[Any]` here), single sanitizer (activate dead `sanitize_ai_output` or delete it).
- **Fallback behavior:** model unavailable → deterministic template + provider note; source unavailable → `INSUFFICIENT_EVIDENCE` + degraded note; both in manifest.
- **Tests:** per-skill contract tests (valid/invalid inputs, evidence thresholds, forbidden-output rejection, fallback paths) + golden fixture runs.
- Policy-schema readiness: skill contracts and the InvestmentCommittee schema reference a future user-local configuration schema carrying the Preliminary User Policy Defaults (sleeve limits, blackout windows, discovery cap, POTENTIAL_ACCUMULATION_ZONE vocabulary, provider recording requirement); any deferred value is represented as an explicit placeholder, never a guess.

## Phase 7: Deterministic Risk and Advisory Order Planning

- **Investment policy config required from user** (`investment_policy.example.json` template + gitignored real file, validated at startup — missing = order-plan section renders `POLICY NOT SET`). Carries the Preliminary User Policy Defaults: 2% tactical test cap, 5/2 blackout with conservative estimates, averaging-down conditions (long-term/core/thematic only, thesis intact, no red flags, within caps, no blackout, cash available, explicit max-add count), pie/standalone overlap accounting (standalone priority; overlap computed across both), zero-cash BUY/ADD block. Missing policy renders `POLICY NOT SET`; deferred values block the dependent plan type instead of guessing.
- **Sizing math:** fixed-fractional on free cash: `size = min(max_position_room, sector_room, risk_budget/risk_pct, free_cash_reserve_adjusted)`; whole shares where applicable, else 2dp EUR notionals marked *indicative*; no fractional-order assumptions without broker-support confirmation.
- **Caps:** single-name, sector/theme, sleeve (tactical cap pending exact value), crypto cap (pending exact value), pie-vs-standalone overlap (computed, never double-counted), turnover-per-run.
- **Cash floor:** no mandatory floor per user policy; `free_cash ≤ 0` blocks all BUY/ADD; recon FAIL withholds deployment (existing behavior, now policy-driven).
- **Earnings blackout:** 5 business days before / 2 trading days after; estimated dates = blackout (conservative) per user policy.
- **Technical mapping gate:** no stop/limit plan unless `VERIFIED` + same-currency + native price; UNRESOLVED/MAPPING_SUSPECT → `RESEARCH` text. Stop-loss scope (tactical-only vs thematic too) pending user confirmation — default to tactical-only until confirmed.
- **Trade eligibility:** recon PASS + cash > 0 + caps + blackout + mapping + non-social evidence (≥1 T0–T2 item ≤48h for event-driven plans; regime/rebalance plans cite RiskManager). Social alone never actionable. Trim/sell of long-term core only if user permits (pending confirmation — default to HOLD+REVIEW text).
- **Order-plan schema** (`schemas/order_plan.py`): `{ticker, direction(BUY/TRIM only), indicative_size, notional_eur_approx, stop_hint?, limit_hint?, expires, rationale_ids[], policy_checks[{rule, pass}], confidence, human_confirmation_required: true}`. No venue/order-type/execution fields. Whole-pie sell/rebalance as advisory whole-pie plan only if user confirms (pending).
- **Explicit no-execution guard:** `risk/order_plan.py` has no network/client imports (import test); every plan carries `> Advisory only — not executed. Confirm manually outside this program.`; grep test forbids execution verbs outside tests.
- **Tests and audit logs:** eligibility matrix (each gate flipped), sizing properties (never exceeds caps/cash), blackout boundaries, mapping gate, social-alone-never-actionable, manifest `policy_version` + per-plan check log.

## Phase 8: Experimental Backtest Evolution

- **Preserve isolation:** no new imports between `experimental/` and production (extend forbidden-string test to all backtest files with AST check); reference parity flows one way (spec ← reference, never code sharing).
- **Walk-forward OOS plan:** `experimental/backtest/walkforward.py` (expanding window: train `t0..tn`, test `tn+1..tm`, roll; metrics on concatenated OOS folds only); `walkforward.json` (folds, earnings embargo); single-period comparison labeled in-sample forever.
- **Strategy naming: MUST_BUY → POTENTIAL_ACCUMULATION_ZONE** (user-approved, never imperative). Rename across regime actions + strategy docs with byte-stable enum aliases for one release + deprecation notes.
- **Research data requirements:** adjusted-close mandatory for comparative evaluation (user policy) — validator flag `adjusted: true/false`; unadjusted → warning + total-return comparison disabled; corporate-action log support P2; history/coverage gates unchanged (252 bars / 5 symbols / 20% rule).
- **How results inform but never drive live decisions:** backtest outputs may tune indicator/threshold *defaults* via reviewed parameter-change proposal (OOS evidence attached); never auto-change live config; live `strategy.json` changes require human edit + commit + manifest `config_hash` record.

## Preliminary User Policy Defaults

> Status: PLANNED / NOT YET BUILT. Preliminary, configurable defaults from user
> direction. No code implements them yet. They MUST be represented in a future
> user-local configuration schema (see Phase 6/7 acceptance criteria) before any
> skill or order-plan logic may depend on them.

- First commit: approved after Phase 0 verification.
- Concentration limits: user delegates defaults to the system; propose editable default limits by sleeve.
- Cash floor: no mandatory cash floor; however zero free cash must block BUY/ADD proposals.
- Risk per trade: configurable; temporary test default maximum 2% of total equity for tactical trades only.
- Earnings blackout: keep 5 business days before and 2 trading days after earnings; estimated earnings dates use conservative blackout.
- Averaging down: allowed only for long-term/core/thematic sleeves when thesis intact, no insolvency/dilution/delisting red flags, not above concentration limits, no event blackout, cash available and explicit max-add count.
- Pie versus standalone: standalone ideas have priority; pies remain separately analyzed and may be operated/rebalanced as whole pies; overlap must be calculated across pie and standalone exposures.
- Discovery: enabled; default max 3 new research ideas per run.
- MUST_BUY rename: approved; use POTENTIAL_ACCUMULATION_ZONE, never imperative wording.
- Provider policy: local-first; cloud fallback allowed; API secrets must never enter Git; LM Studio temporarily disabled for testing until user enables it; actual winning provider/model per stage must be recorded.
- Scheduling: target 09:00, 15:30, 21:00 Europe/Bratislava; do not implement Task Scheduler until runtime lock and lightweight/full run modes exist.
- Research coverage: full adapters planned for healthcare/FDA and crypto/ETF-flow coverage; social sources low-confidence; official/public channels preferred.
- Backtests: adjusted prices mandatory for comparative strategy evaluation.
- User-specific config: commit templates only; real portfolio_config, PIE CSVs, performance TOML and api.env remain local and ignored.

## Decisions Deferred Pending User Confirmation

> Status: PLANNED / NOT YET BUILT. Do not guess these values in code or schemas;
> use explicit `REQUIRES_USER_VALUE` placeholders until the user confirms.

- Final report language: propose English internal data/contracts + Slovak human brief, pending explicit confirmation.
- Exact concentration defaults by sleeve.
- Exact sector and theme caps.
- Exact crypto allocation cap.
- Exact tactical sleeve allocation cap.
- Whether standalone tactical trades may overlap core/pie holdings.
- Whether a pie can be sold/rebalanced as a whole automatically in advisory plans.
- Whether stop-loss proposals are allowed only for tactical positions or also thematic positions.
- Whether user permits advisory trim/sell plans for long-term core positions.
- Preferred research source priority among TradingView, Finviz, Earnings Hub, Reuters/official IR, Reddit, X, Stocktwits.

## Cleanup Matrix

| Candidate | Verdict | Prerequisites | Phase | Rollback |
|---|---|---|---|---|
| 4× `*_V2415_*.py` snapshots (zero importers, current newer) | REMOVE | Phase 0 commit; hash-archive outside repo | 2 | restore from archive/git |
| 2× cache `*_V2415_*.json` | REMOVE | — | 2 | caches rebuild |
| `investment_engine/trading212_integration.py` (dead, zero importers) | REMOVE | Phase 0 commit | 2 | `git revert` |
| `news/engine.py`, `pipeline/engine.py`, `memory/store.py` (dead/test-only) | REMOVE / MOVE to `tools/` | zero prod callers confirmed | 2 | `git revert` |
| `trading212/` shadow vs root trio | CONSOLIDATE into `trading212/`; root → shims → remove | Phase 1 pinning green | 2 | restore shims |
| Root R1 recon block | DEPRECATE (alias) → REMOVE | Phase 1 acceptance | 1→2 | revert hunk |
| `OllamaClient` twins | MOVE to `tools/t212_cli.py` | Phase 2 | 2 | `git revert` |
| CSV-ledger stack (8 modules, tests-only) | MOVE to `tools/ledger/` | Phase 0 commit | 2 | `git revert` (+ test import fix revert) |
| `integrated_report.py` + legacy `AIContextBuilder` | MOVE to experimental/tools | Phase 2 | 2 | `git revert` |
| `discovery/engine.py` (test-only) | MOVE to experimental OR wire intentionally | Phase 4/5 decision | 4 | `git revert` |
| `prompts/*.md` bundle vs inline drift | INVESTIGATE → registry migration | Phase 6 design | 6 | keep both until cutover |
| `schemas` `List[Any]` + dead sanitizer | REFACTOR (type + activate) | Phase 6 | 6 | `git revert` |
| `test_ticker_mapping.py` BOM | FIX (save UTF-8) | — | 1 | trivial |
| Stale `reports/latest/` V4 | ARCHIVE-OUT + REMOVE (or reimplement) | user confirms no consumers | 4 | restore from outside-git archive |
| Duplicated helpers (dual `_is_us_symbol`/`_symbol`, double `_collect_data_quality_flags`, dual stance fns) | REFACTOR (merge) | pinning tests | 1/4 | `git revert` |

## Test Strategy

- **Unit tests:** money math, mapping, indicators (frozen fixture), adapters (frozen feeds), sizing (property caps).
- **Contract tests:** skill I/O schemas + evidence thresholds + forbidden-output rejection; corpus item schema; order-plan schema; manifest schema.
- **Report parity tests:** cross-artifact recon/signal/row/run_id equality; brief brevity rules; topology layout.
- **Provider attribution tests:** forced-failure chain matrix asserting recorded winner/model per stage (incl. LM-Studio-disabled state); deterministic-fallback labeling.
- **Environment parity tests:** verifier both PCs; `verify_indicators.py` byte-identical manual outputs; presence/absence matrix.
- **News freshness tests:** 47.9/48.1h boundaries, timezone-aware; undated exclusion; background labeling.
- **Evidence/source tests:** unknown-ID citation rejection; single-source→UNVERIFIED; tier gating.
- **Social-media safety tests:** social-only input never yields actionable plan.
- **No-order-execution tests:** import-graph + grep guards.
- **Regression fixture policy:** frozen fixtures with SHA; sanitized live-shaped snapshots versioned per phase; no live API in tests; encoding check for test files.

## Documentation Plan

- **README:** keep truthful; per-phase appends (topology 4, corpus 5, skills 6, policy 7).
- **Architecture:** `docs/architecture/` from this blueprint in Phase 1.
- **Environment:** extend `ENVIRONMENT_DIAGNOSTIC.md` with two-PC matrix in Phase 3.
- **Runbook:** `docs/runbook.md` (install → verify → run → read `current/` → retention) in Phase 4.
- **Reports:** `docs/reports.md` (layers, brief rules, manifest schema) in Phase 4.
- **Security/secrets:** `docs/security.md` (api.env handling, sanitization, retention, no-commit policy) in Phase 1.
- **Skills:** `docs/skills/` (registry, contracts, versioning, fallback) in Phase 6.
- **Research sources:** `docs/sources.md` (adapters, tiers, freshness, compliance) in Phase 5.

## Implementation Backlog

- **P0-1 First commit + two-PC verification.** Deps: Phase 0 files. Risk: LOW. Complexity: S. DoD: commit exists, `check-ignore` green, verifier exit 0 both PCs. (Commit approved per user policy; user runs it.)
- **P0-2 Reconciliation unification.** Modules: `broker_first.py`, `regime_report.py:145-199`, tests. Deps: P0-1. Risk: HIGH. Complexity: M. DoD: Phase-1 acceptance.
- **P0-3 Dedupe identity unification.** Modules: `broker_first.py:692-720`, `regime_report.py:389-582`, `symbols.py`. Deps: P0-2. Risk: HIGH. Complexity: M. DoD: alias fixture → one row; parity green.
- **P0-4 T212 home consolidation.** Modules: root trio, `trading212/`. Deps: P0-2/3. Risk: HIGH (step 4). Complexity: L. DoD: zero root-prod imports; suites green; snapshot byte-identical.
- **P0-5 Indicator policy A.** Modules: `research/indicators.py` (new), `technical_analysis.py`, `market_data.py`, manifest. Deps: P0-1. Risk: MED. Complexity: M. DoD: byte-identical manual outputs both PCs; parity tests green.
- **P0-6 ResearchItem substrate.** Modules: `schemas/research_item.py`, `corpus/*`. Deps: P0-5. Risk: MED. Complexity: L. DoD: Phase-5 acceptance.
- **P0-7 Skill registry + first four skills.** Modules: `skills/*`. Deps: P0-6. Risk: MED. Complexity: L. DoD: contract tests + golden runs green; attribution recorded.
- **P0-8 No-execution guards + eligibility matrix.** Modules: `risk/*`, grep/import tests. Deps: P0-7 + policy file. Risk: LOW. Complexity: M. DoD: 100% gate coverage; zero execution imports.
- **P0-9 Report topology + attribution + brief rules.** Modules: `report_structure.py`, `documents.py`, `providers/attribution.py`. Deps: P0-2/3. Risk: MED. Complexity: M. DoD: topology + parity + brevity + attribution tests green.
- **P0-10 Investment policy config (user-local).** Modules: `investment_policy.example.json` + validator. Deps: Preliminary User Policy Defaults (recorded above). Risk: LOW. Complexity: S. DoD: validator + `POLICY NOT SET` fallback; deferred values block dependent plans.
- **P1-1 Walk-forward OOS harness.** Deps: Phase 8. Complexity: M.
- **P1-2 Analyst-revision structured parsing.** Deps: P0-6. Complexity: M.
- **P1-3 Regime weight/52w-low fixes + POTENTIAL_ACCUMULATION_ZONE rename.** Deps: user approval (granted above). Complexity: S.
- **P1-4 God-module splits post-pinning.** Deps: contract tests. Complexity: L.
- **P2-1 FinBERT/sentiment upgrade (optional).** Deps: P0-6. Complexity: M.
- **P2-2 Corporate-action log + adjusted-feed enforcement.** Deps: Phase 8. Complexity: M.
- **P3-1 Scheduler/lock/run-modes (only after lock + lightweight/full modes design).** Targets 09:00/15:30/21:00 Europe/Bratislava per user policy. Deps: Phase 4 decision. Complexity: S.

## Explicit Decisions Required From User

Most policy items are now preliminarily decided (see Preliminary User Policy Defaults). Still open — confirmation required before the Phase 6/7 build tasks that depend on them:

1. Final report language (proposal: English internals + Slovak human brief).
2. Exact sleeve/sector/theme/crypto/tactical caps and concentration defaults.
3. Standalone-vs-core/pie overlap permission.
4. Whole-pie sell/rebalance in advisory plans (allowed?).
5. Stop-loss scope (tactical-only vs thematic too).
6. Trim/sell of long-term core positions (permitted?).
7. Research source priority order (TradingView, Finviz, Earnings Hub, Reuters/official IR, Reddit, X, Stocktwits).
8. Provider/model pins: LM Studio re-enable action; cloud spend beyond free tiers; decision/writer model pins.
9. `portfolio_config.json` + PIE CSVs + performance TOML template-ization approach.
