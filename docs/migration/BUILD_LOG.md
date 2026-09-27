# Build Log — Final Strategy Implementation (Phases 0–8)

> Build session, no questions asked per user direction. Tested WITHOUT LM Studio
> (LM Studio disabled by default policy; deterministic fallbacks cover all stages).
> No commits created (user runs the first commit). No live Trading 212 calls made
> (no api.env reads; all tests offline with fixtures/fakes).

## Final verification (this PC, Python 3.12.10)

- `pytest tests/` → **337 passed**
- `pytest experimental/tests/` → **56 passed** (52 existing + 4 walk-forward)
- `scripts/verify_environment.py` → exit 0 (core 11/11, pip check OK, guards OK, syntax 7/7)
- `scripts/verify_indicators.py` → `98a3262c610fbf957d8938f3c46d2fdc46cca277e1e7335512ef82ff6a84aad1`
  (identical before/after extraction — byte-for-byte preserved)
- `git check-ignore` → api.env, reports, logs, data/cache, runtime,
  investment_policy.json all IGNORED; no commit created by this session.

## Blueprint

- `docs/strategy/FINAL_IMPLEMENTATION_BLUEPRINT.md` — full blueprint + user-policy
  patch (Preliminary User Policy Defaults, Deferred Decisions, Phase 6/7 criteria).

## Phase 0 — validation

- Verifier + check-ignore green (this PC; second PC pending user).
- No commit (per standing instruction).

## Phase 1 — canonical financial core

- `regime_report.compute_reconciliation(t212_data, rows, canonical=None)`: canonical
  verdict wins when supplied (`verdict_source: canonical`); otherwise recomputed
  from rows (`verdict_source: recomputed`) — legacy R1 summary inheritance REMOVED.
- `build_unified_portfolio_rows(..., isin_map=None)`: ISIN-first dedupe (position
  ISIN → map → broker ID), `identity_provenance` per row, collapsed-dupe notes.
- `main.py`: builds `isin_map` (KNOWN_ISINS + broker ISINs), passes canonical
  `_recon` into `RegimeReportGenerator` + `_format_cash_deployment`; recon guard
  prefers `reconciliation_v2`; methodology/legacy-context prefer v2.
- `trading212_portfolio.py`: `compute_legacy_r1()` helper + `_legacy` mirrors +
  one-time deprecation warning (R1 computed, unconsumed; removal deferred past Phase 2
  to keep behavior pins stable).
- `accounting/reconciliation.py`: historical-only guard docstring.
- Tests: `tests/test_phase1_canonical_core.py` (13). Fixed along the way: dedupe-note
  gap for ISIN-collapsed rows (now named in notes); ledger-guard assertion narrowed
  to the ledger stack (`cashflows` import is legitimate).

## Phase 2 — T212 consolidation

- `trading212/{auth,portfolio,integration}.py` are now the canonical implementations
  (byte-exact move: 0/2/1 import rewrites; verified importing).
- Root `trading212_*.py` are thin shims (re-export identical objects — tested) with
  `runpy` `__main__` delegation (bats keep working).
- `main.py` (6 sites) + `broker_first.py` (1 site) retargeted to `trading212.*`;
  tests updated to package imports.
- Removed (SHA256-archived to `%TEMP%\opencode\phase2_archive`): dead
  `investment_engine/trading212_integration.py` (71dff4cf…) + 4 V2415 snapshots.
- `tools/t212_cli.py` (+ `tools/__init__.py`): manual read-only CLI home.
- Tests: `tests/test_phase2_consolidation.py` (7). R1 full removal deferred (see Phase 1).

## Phase 3 — indicator policy A

- NEW `research/indicators.py` (`manual-v1`, frozen formulas, `resolve_engine`,
  `MANUAL_COLUMNS` contract); `technical_analysis.py` delegates (IndicatorConfig
  re-exported; `engine="manual"|"reference"|"auto"` + `resolved_engine`).
- `market_data.py` MACD aligned to `adjust=False`.
- Manifest gains `indicator_engine`, `pandas_ta_present`, `talib_present`, `versions`.
- NEW `scripts/verify_indicators.py` (seeded fixture → snapshot hash).
- Parity spec (frozen fixture): SMA/EMA/MACD/BB exact vs reference; RSI ±15, ATR
  ±50% rel (documented SMA-vs-Wilder divergence); ADX direction agreement;
  Supertrend state agreement.
- Tests: `tests/test_phase3_indicators.py` (12).

## Phase 4 — reports + attribution

- NEW `providers/attribution.py`; `ChainedFallbackProvider.last_attribution`;
  `main._STAGE_ATTRIBUTION` per stage (winner or deterministic); `model_info` +
  result carry `providers_per_stage`; manifest `provider` = winners summary,
  `stance` separated (mislabel fixed).
- LM Studio disabled by default (`lmstudio_enabled`, `LMSTUDIO_ENABLED=1` to enable);
  factory skips it in chain + explicit requests degrade with warning; probe notes
  the disabled state.
- Topology: `current/` + `archive/<run_id>/` in `write_reports`; NEW concise
  `documents.build_intelligence_brief` (≤60 lines, single account warning, no sized
  orders); `generate_human_brief` delegates (signature frozen); `write_human_brief`
  → `current/`; stance unified on `documents.portfolio_stance` (dead dupes + legacy
  body + duplicated quality-flags collector removed); attribution section shows
  winners; stale `reports/latest/` V4 archived outside git and retired.
- README outputs/provider-policy updated.
- Tests: `tests/test_phase4_reports.py` (9). Fixed along the way: attribution.py
  import typo; circular self-entry dropped from manifest files map; Phase-0 archive
  test corrected to the new layout.

## Phase 5 — research corpus

- NEW `schemas/research_item.py` (18 fields, `extra=forbid`, OK-requires-timestamp,
  `decision_relevant(48h)`, background labels).
- NEW `research/corpus/` (tiers with social-always-T3, freshness 48h, URL+title
  dedupe, adapters with COMPLIANCE headers + never-raise isolation, builder writing
  `research_corpus_<run_id>.md/.json` to `ai_context/`).
- `filter_decision_news` default 72h → 48h (boundary test updated to assert it).
- HTML fallback no longer stamps `now()` (UNDATED); sentiment skips undated items.
- Healthcare/Biotech sector templates added.
- Wired into `run_engine` (additive; counts/paths in result + manifest `corpus_items`).
- Tests: `tests/test_phase5_corpus.py` (10). Fixed along the way: social tier-force
  ordering bug (real), fuzzy-threshold test pair, three `from typing` typos.

## Phase 6 — skills

- NEW `skills/` registry (versioned prompts load only via registry, manifests,
  never-raises dispatch) + `contracts.py` (evidence-first, citation validation,
  confidence model).
- Four v1 skills (manifest + prompt + skill.py each): PortfolioRiskManager
  (deterministic posture/breaches/cash), NewsEventAnalyst (CONFIRMED needs 2
  publishers; LLM enrichment with citation check; deterministic fallback),
  EquityResearchAnalyst (VERIFIED-only technicals; no targets/sizes/averaging),
  InvestmentCommittee (deterministic reconcile; canonical final; dissent notes;
  FAIL → WATCH).
- Wired after canonical snapshot (outputs + versions in result + manifest
  `skill_versions`); legacy prose sections unchanged (prompt cutover = follow-up).
- Tests: `tests/test_phase6_skills.py` (10). Fixed: registry never-raises for
  unknown skills; five `from typing` typos (a repo-wide guard test now prevents
  recurrence: `test_no_broken_typing_imports_repo_wide`).

## Phase 7 — risk + advisory plans

- NEW `investment_policy.example.json` (preliminary defaults + REQUIRES_USER_VALUE
  placeholders); real `investment_policy.json` gitignored (added to `.gitignore`).
- NEW `risk/` (`policy` loader/validator, `eligibility` gate matrix with business-day
  blackout, `sizing` fixed-fractional with explicit 25%-adverse assumption,
  `order_plan` builder + renderer with advisory guard; no network/execution imports,
  grep-guarded).
- Wired post-committee (plans only when policy status OK; else POLICY NOT SET);
  sleeves from config groups; unknown sleeve / deferred core-trims / whole-pie →
  blocked with reasons; mapping gate downgrades levels instead of blocking;
  manifest `policy_version`.
- Tests: `tests/test_phase7_risk.py` (7). Fixed along the way: social-only gate
  ordering (real bug), two blackout boundary expectations (test dates wrong,
  implementation verified correct), three `from typing` typos.

## Phase 8 — backtest evolution + rename

- `MUST_BUY` → `POTENTIAL_ACCUMULATION_ZONE` in `market_regime.py` (scores,
  REGIME_ACTIONS key, classifier, warnings) with `MUST_BUY` alias entry,
  `is_accumulation_regime()` helper, softened posture vocabulary; emoji map,
  AI-recs prompt text, settings comment, regime test updated; eligibility already
  accepted both spellings.
- NEW `experimental/backtest/walkforward.py` (expanding folds, warmup tail,
  embargo exclusion, OOS-only metrics, deterministic run_id, containment in
  `experimental/reports/`, fail-closed) + `config/walkforward.json` template.
- Tests: `experimental/tests/test_walkforward.py` (4: OOS+embargo, determinism,
  fail-closed/contained, no-forbidden-imports). Fixed: synthetic-day guard.

## Model swap (post-build, no model loaded)

- `deepseek-r1-finance-reasoning-14b` retired from decision stages: R1-style
  reasoning traces leak into the strict-JSON `ai_recommendations` stage
  (repo already strips `<think>` in OpenRouter + handles `reasoning_content`
  in LM Studio — evidence of the same failure mode).
- New decision model: `qwen3.8-9b` (`portfolio_config.json` `lm_studio_model` +
  `decision_model`; writer stays `google/gemma-4-12b`). Preset comment + batch
  comment updated. `gpt-oss-20b` kept as upgrade candidate (VRAM check pending);
  `fin-r1` stays fallback-only (Qwen2.5-based, early-2025 — nothing to download).
- Tests: routing/presets/phase0 (33) green.

## Finance-model research (web, nothing downloaded)

- Top download candidate: `TheFinAI/Fin-o1-14B` (Qwen3-14B, SFT+GRPO, Apache-2.0;
  GGUF via `mradermacher/Fin-o1-14B-GGUF`, Q4_K_M ~9.1 GB). Same 14B class as
  retired deepseek → VRAM fit proven. Benchmarks beat Fin-R1/GPT-o1/DeepSeek-R1.
- Second: `DragonLLM/Qwen-Open-Finance-R-8B` (LLM Pro Finance Suite, Nov 2025,
  Apache-2.0; GGUFs via pate2464/vividdream). 8B, fast.
- Skipped: xerus19573/Qwen3-30B-A3B-Finance (card warns against scheduled use),
  Agentar-Fin-R1 (no GGUF found), finance-Llama3-8B (2024), FinGPT (2023).
- Config unchanged: `qwen3.8-9b` stays decision model until a download is tested.

## Critical fixes pack (user-directed, architect-adjudicated)

- **Config assets replaced (verified, not blind):** 96/96 broker IDs covered both
  directions; settings/aliases/rules byte-identical. Corrections vs proposal:
  15 LSE/Xetra tickers fixed to verified forms (NCLR.L, WBIO.L, DRDR.L, QWTM.L,
  NATP.L, IISU.L, IUVF.L, ESIF.L, JEDG.L, SSLN.L, IQQH.DE, EXI2.DE, COFF.L,
  DR4M.L, ERNE.L), ENRd_EQ→ENR.DE (bare ENR is US Energizer — wrong company),
  LAR0d_EQ→LOM.DE+LMT fallback, NVDd_EQ enabled (sole NVDA coverage).
- **Reconciliation formula change REJECTED:** live arithmetic proves
  total=invested+free+pie+ppl, i.e. invested is cost-basis; expected=invested
  would FAIL permanently (≈−ppl). Instead found the real root cause: ISIN dedupe
  merged two distinct Apple lots (AAPL_US_EQ + APCd_EQ, €20.63 ≈ delta −20.27).
  Dedupe now merges same-ISIN rows only on identical lot (qty+avg) in both
  broker_first and unified rows; pinned tests still green; live replay counts
  96/96 with APCd included (delta should land ≈+0.36 → PASS on next live run).
- **Symbols layer:** static verified `BROKER_YAHOO_OVERRIDES` (96 pairs, no
  runtime globals, no suffix heuristics); precedence config > broker map >
  static (test-enforced); SUPPORTED_YAHOO extended with verified symbols;
  broker stems (NVD/INLD/APCD/LAR0D) blocked in UNRESOLVED_YAHOO.
- **News:** `_as_record` boundary kills the NewsItem.get crash (regression test);
  RSS fallbacks limited to verified feeds (Yahoo/CNBC/MarketWatch; Reuters dead,
  FT/Bloomberg excluded for paywall/ToS); wired as additive RSS_FALLBACK bucket.
- **AI-context dedupe:** `_save_ai_context` writes straight into ai_context/ with
  run_id name; layer skips identical re-copy; news_context moved into ai_context/.
- **P&L diagnosis:** NOT broken — snapshot P&L == broker ppl on all sampled
  tickers incl. GBX (EGT −18.77 == −18.77); raw==normalized is correct at factor 1.
  No live-price overwrite (would break broker agreement); regression test added.
  `positions_sum: 32298` in the raw dump is a native-units debug artifact (cosmetic).
- Final: tests/ 363 passed, experimental/ 56 passed, both verifiers exit 0.

## Automatic Playwright sweep (all unfound tickers)

- P1 hygiene: `playwright` documented as optional (+ browser install line in
  runbook); 5 bare `except:` in the TV technicals fallback narrowed to
  `except Exception`; new `fetch_tradingview_symbols_sync` (serial default).
- P2 earnings layer 2: `_merge_calendar_earnings` helper merges Finviz/
  EarningsHub calendar fills into failed Yahoo slots (ETFs skipped, cap 15).
- P3 TradingView corpus adapter (`adapters_tradingview.py`, COMPLIANCE header):
  exchange resolution (config first, verified suffix map, US NASDAQ-first,
  crypto/unknown skipped); gauge readings as TV_-prefixed technicals (never
  mistaken for Yahoo numerics); key-facts + analyst ratings as snapshot-dated
  tier-2 corpus items (page forecasts stay quoted estimates).
- P4 sweep in run_engine (default on, cap 8, serial, kill-switch): fills
  missing technicals + news buckets; corpus build moved after the sweep;
  manifest counts playwright_technicals/earnings/facts.
- Live probe findings: page loads fine (200) but CSS selectors are stale —
  added text-based extraction fallback (`extract_tv_text_signals`, pure,
  fixture-tested; fixed one cross-block false match found by live text).
  Verified on real AAPL page: price/technicals/analyst/facts all extract.
- Not built: chart screenshots (reports are markdown, no consumer); TradingView
  movers in briefs (AI-context only).
- Tests: `tests/test_playwright_sweep.py` (11 incl. real-shape text fixtures).
- Final: tests/ 388 passed, experimental/ 56 passed.

## Live run 948f4963 with Playwright sweep (2026-09-26 15:31–15:38, 410 s, exit 0)

- Tests before: 388 + 56 green. Chain Gemini→Mistral (429s absorbed by breaker),
  zero local models, unload skipped.
- Recon PASS (−0.86 €), corpus 9/9 decision, skills 4×OK, secrets 0 hits.
- Playwright sweep live: 1 page (AINF), 1 technicals, TV cache files written;
  TV gauge now renders in `_tech_summary_line` (TV_-keys never merge into Yahoo
  numerics or broker values).
- 0 failed tickers, 0 UNRESOLVED rows; order plans disabled (policy NOT SET).
- Known noise: yfinance curl resets (transient), FinViz sector API drift
  (debug, pre-existing), dead Xetra-code universe probes rejected as designed.

## Universal instrument resolver (ISIN-keyed, default ON)

- NEW `research/universe.py`: keyless OpenFIGI v3 client (25 req/min, batch 10,
  backoff, never raises), Bloomberg exchCode→Yahoo allowlist (unknown venues
  skipped, never guessed), home-currency research-listing selection, JSON cache
  `data/cache/instrument_universe.json` (gitignored), Yahoo existence probes
  with cached verdicts, `UNIVERSE_ENABLED=0` kill-switch, optional
  `OPENFIGI_APIKEY` env (never printed).
- NEW `scripts/build_universe.py` (--check/--dry-run/--refresh).
- Wiring (additive only): enrich fills missing/unresolvable `yahoo_by_display`
  entries (working mappings never overridden); universe-verified admission in
  `market_data._check_supported`, row `md_state`, failed-ticker ledger.
- Verified live: OpenFIGI shape confirmed; 3-ISIN probe (AAPL→AAPL,
  Vestas→VWSB.DE, Lockheed→LAR0.DE — note: Xetra Vestas/Lockheed codes differ
  from assumptions, static LOM.DE kept for stability).
- Tests: `tests/test_universe.py` (8). Full suite green.

## Completion fixes + live run 1ae3d286 (2026-09-26 12:15–12:20, 280 s, exit 0)

- `LITM→LITM.L` display entry; ETF earnings pre-skip (`is_etf_like` + ETF Yahoo
  set in NO_EARNINGS_YAHOO — ~20 useless lookups gone); FinViz quote pre-gate
  for non-US symbols (EXI2 404 eliminated); served_model+depth in chain log;
  conditional Advisory Order Plans section (renders only when gated plans exist
  and recon != FAIL); analyst coverage stays in the AI-context news layer.
- **Reconciliation PASS live** (delta −0.69 €, excluded €0.00, 96 rows).
  Corpus 12/12, skills 4×OK, secrets scan 0 hits, no local-endpoint contact.
- Follow-ups fixed from validation: stale aliases EGTL/LITMM/IBEE corrected or
  removed; `C7A0D→CATL.F` (dead) corrected to `300750.SZ` (SUPPORTED).
- Known benign: one T212 20 s timeout on the first attempt (Yahoo FX slowness);
  retry succeeded. yfinance library's own "delisted" stderr for dead aliases
  (SUI/TAO) is external noise; our states stay UNRESOLVED/FETCH_FAILED.
- Tests: 369 + 56 green (incl. test_completion_fixes.py, 5 tests).

## Live run 524fffa0, API-only (2026-09-26 18:03–18:08, 285 s, exit 0)

- Tests before: 399 + 56 green. Chain Gemini→Mistral (heavy 429s, breaker
  absorbed all); zero local models; unload skipped.
- Recon PASS, corpus 13/13, skills 4×OK, C7A0→300750.SZ + EGT→EGT.L live,
  0 UNRESOLVED, 0 failed tickers, deep_dive (15 sections) in manifest,
  secrets scan 0 hits, order plans correctly absent (policy NOT SET).
- Playwright sweep: 1 page / 1 technicals (AINF). ETF earnings spam gone
  (single legitimate EGT.L attempt). No FinViz 404.

## Deep-dive brief + ISIN-grouped holdings + live run f9e3c604

- NEW `portfolio_deep_dive.md` (separate artifact, 15 fixed sections,
  deterministic, `build_deep_dive()` + manifest sha + archive copy). Missing
  data renders "No coverage this run." Concise briefs + contract untouched.
- Holdings grouped by ISIN at render (canonical rows untouched): group row +
  `↳` lot sub-rows, exact-sum aggregates, weighted avg cost, same-ISIN note,
  company backfill within group; ISIN-less rows never merge. Columns unchanged.
- Live run f9e3c604 (2026-09-26 17:29–17:37, 482 s, exit 0): recon PASS,
  deep-dive 15 sections present, Apple group (0.5977 qty, weighted avg) +
  APCD lot live, secrets 0 hits, no local contact.
- Tests: 399 + 56 green (test_deep_dive.py, test_holdings_grouping.py).

## Live run e387e276 with universe (2026-09-26 13:13–13:23, 558 s, exit 0)

- Recon PASS (−0.69 €), corpus 12/12, skills 4×OK, secrets 0 hits, no local contact.
- Mapping: **0 UNRESOLVED rows** (EGT→EGT.L and C7A0→300750.SZ confirmed live;
  `failed_tickers.md` gone). Tests: 377 + 56 green, verifiers exit 0.
- Universe: 60/96 ISINs cached (cap/run), 101 verified listings, 0 resolved this
  run (tables cover all 96 — universe is the safety net, proven by probe).
  ~20 dead Xetra-code probes rejected by validation as designed.
- Notes: one transient T212 20 s timeout attempt before this run (Yahoo FX
  slowness, retry OK); yfinance curl resets transient; FinViz sector API drift
  is debug-level pre-existing noise.

## API-only verification run e9de2536 (2026-09-26 11:19–11:28, 570 s, exit 0)

- Tests before run: 363 + 56 green; verifiers exit 0. Chain Gemini→Mistral only.
- **Reconciliation PASS** (delta −0.69 €, threshold €2.00, excluded €0.00, 96 rows)
  — the lot-aware dedupe fix confirmed live. Corpus 12/12 decision (RSS
  fallbacks working). AI context written once into ai_context/.
- Follow-up fix from validation: stale config aliases `EGTL→EGTL.L`,
  `LITMM→LITH.L`, `IBEE→IBEE.L` pointed at dead symbols and overrode the broker
  table (live EGT HELD_UNRESOLVED). Corrected to EGT.L / LITM.L / removed;
  test-pinned (`test_config_aliases_point_at_live_symbols`).
- Artifacts: recon PASS consistent (brief/snapshot/JSON/manifest/diagnostic);
  run_id e9de2536 everywhere; secrets scan 0 hits; no local-endpoint contact;
  order plans disabled (policy NOT SET). Full tests re-run green after the fix.

## Docs

- `docs/architecture.md`, `docs/runbook.md`, `docs/reports.md`, `docs/security.md`,
  `docs/skills.md`, `docs/sources.md` (+ this log).

## Known follow-ups (not started)

- Second-PC verification (manual-indicator path) + first commit (user actions).
- Legacy inline-prompt → skill-registry cutover (P1 workstream).
- R1 computation removal (after one green release with deprecated aliases).
- Root T212 shim removal (after one green release).
- Deferred user confirmations (caps, overlap, whole-pie, stops scope, core trims,
  source priority, brief language) — placeholders block safely until answered.
