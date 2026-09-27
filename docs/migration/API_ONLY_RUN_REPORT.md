# API-Only Live Run Report — 2026-09-26 01:26–01:31 CEST

> Read-only broker access. No orders submitted/created/modified/cancelled/simulated.
> No LM Studio / llama.cpp / Ollama contact (no probes, calls, warmups, unloads).
> No secrets in terminal, reports, logs, or artifacts (scanned: 0 hits).
> No commits. No user-config/threshold/strategy changes. No history deleted.

## Sanitation changes made (all offline, tested before the run)

- **A1 provider:** `EngineSettings.api_only` (`API_ONLY=1`) → `ProviderFactory.api_only_chain`
  (Gemini → Mistral → deterministic; locals never constructed). Local probes skipped
  with a recorded exclusion event; LM Studio unload skipped with a log line;
  manifest gains `ai_execution_mode`, `disabled_providers`, truthful `provider`
  winners summary + `stance`; both briefs print `AI execution mode: API_ONLY` /
  `Disabled providers: LM Studio, llama.cpp, Ollama`. Per-stage attribution records
  actual winners incl. `ai_recommendations` + skill `news_events` (fresh-hint only)
  + deterministic skill stages. Tests: `tests/test_api_only_sanitation.py` (13).
- **A2 corpus:** `ResearchItem.url_kind` (`article`/`aggregator_url`/`unavailable`;
  Google News links labelled `aggregator_url`); adapter rejects title/URL-less
  records as FAILED; builder schema-validates every item (invalid → FAILED with
  reason); `build_corpus_files` requires non-blank run_id; corpus JSON wrapped
  with run_id/generated_at/counts; rejected ledger always written to
  `reports/debug/<run_id>/undated_or_rejected_research.md` ("none" statement when
  empty); `<3` decision items → both briefs print the exact coverage sentence.
- **A3 reconciliation:** canonical snapshot moved before the guard; guard logs
  canonical fields only (equity/reported/expected/dedup/excluded/delta/threshold/
  status) — the `T212 Reconciliation:` R1 line is gone repo-wide; legacy
  disagreement logs exactly `Legacy reconciliation differs; ignored for
  decisions. Canonical broker-first result: <status>.` Canonical status unchanged.
- **A4 market data:** verified already clean (no "delisted" claims anywhere;
  FETCH_FAILED registry; FinViz kill-switch logs once — tested). Added Slovak
  hard-failure disable (HTTP 403/404 → warning once per site, skipped rest of run)
  in both Strict and Enhanced fetchers.
- Console-encoding fix found live: canonical log line used `→` (cp1252 crash on
  stdout; file log unaffected, artifacts unaffected). Fixed to ASCII post-run.

## Preflight command results (all pass → run proceeded, no failure report needed)

- `scripts/verify_environment.py` → exit 0 (core 11/11, pip check OK, guards OK).
- `scripts/verify_indicators.py` → exit 0 (`98a3262c…`, unchanged).
- `pytest tests/` → 350 passed (one stale source-assertion fixed mid-preflight).
- `pytest experimental/tests/` → 56 passed.
- `git check-ignore -v api.env reports logs data/cache` → all ignored.

## Live run

- Command: `API_ONLY=1 py -3.12 portfolio_ai_assistant.py --config portfolio_config.json
  --investment-engine --generate-full-report` (normal generation, no mocks).
- Duration: 276.3 s, exit 0. T212 read-only fetch OK (96 positions, equity €3,264.99).
- Actual provider winners by stage: `ai_recommendations` Gemini/gemini-2.5-flash;
  `decision` ×4 Gemini; `discovery`/`summary`/`news_events` Mistral/open-mistral-nemo
  (Gemini 429 → breaker skipped it 40 s → Mistral served; deterministic fallback
  unused). Requested models recorded as `qwen3.8-9b`/`gemma-4-12b` (config intent).
- Disabled provider confirmation: manifest `disabled_providers` =
  [LM Studio, llama.cpp, Ollama]; log shows `API-only chain Gemini -> Mistral` ×2
  and `skipping LM Studio unload`; zero local-host/timeout/probe lines in the log.
- Research corpus: 1 total / 1 decision / 0 background / 0 undated / 0 rejected
  (Aktuality.sk item, tier 2, age 6.3 h). Rejected ledger file created with "none".
  Briefs print `Research coverage insufficient: 1 validated decision-quality items
  in the last 48 hours.` Slovak SME 403 + Pravda 404 each warned once and disabled.
- Reconciliation result: canonical FAIL (equity €3,264.99, reported €3.42,
  expected €3,261.57, dedup €3,241.30, excluded €20.63, delta €−20.27,
  threshold €2.00). Legacy R1 disagreed → single canonical line only.
- Report consistency: FAIL + delta −20.27 identical in decision brief, snapshot,
  portfolio_analysis.json, manifest, diagnostic. run_id `36e30a8d` identical in
  manifest, AI context ×2, corpus ×2, debug folder ×5.
- Market-data source failures: EXI2 intraday_15m/1h validation failed (accurate
  wording, no delisted claim); yfinance no-data for SUI-USD/TAO-USD (library's own
  stderr; ours: UNRESOLVED + FETCH_FAILED); FinViz 404 → disabled once; cash-flow
  history PARTIAL (window from 2026-08-04); news-context builder hit a pre-existing
  NewsItem/dict shape error (degraded to corpus path — see issues).
- Order-plan status: `investment_policy.json` absent → POLICY NOT SET → plans []
  and blocked []; advisory-only invariant holds (no execution capability in repo).
- Manifest records: per-stage provider/model ✓, disabled list ✓, indicator engine
  (reference, pandas 3.0.5/numpy 2.2.6/scipy 1.18.1) ✓, research counts
  (1/1/0 + rejected 0) ✓, policy `NOT SET` ✓, skill versions v1 ×4 ✓.

## Remaining known issues (not fixed — out of scope or pre-existing)

1. Pre-existing `News context file generation failed: 'NewsItem' object has no
   attribute 'get'` (news-context .md builder vs NewsItem shapes; corpus path
   covered the run). Needs a shape-normalization fix, no threshold impact.
2. yfinance library prints its own "may be delisted" stderr for dead crypto aliases;
   our messages stay UNRESOLVED/FETCH_FAILED (library logger, not ours).
3. Pre-existing non-ASCII console mojibake in one cash-flow log line (file log fine).
4. Single-period corpus was thin this run (1 decision item) — coverage, not a bug.

## Generated report paths

```text
reports/current/portfolio_intelligence_brief.md
reports/current/portfolio_decision_brief.md
reports/current/t212_portfolio_snapshot.md
reports/current/portfolio_analysis.json
reports/current/run_manifest.json
reports/current/failed_tickers.md
reports/ai_context/ai_context_36e30a8d.md
reports/ai_context/portfolio_analysis_36e30a8d.json
reports/ai_context/research_corpus_36e30a8d.md
reports/ai_context/research_corpus_36e30a8d.json
reports/debug/36e30a8d/reconciliation_diagnostic.md
reports/debug/36e30a8d/reconciliation_diagnostic.csv
reports/debug/36e30a8d/raw_endpoint_dump.json
reports/debug/36e30a8d/run.log
reports/debug/36e30a8d/undated_or_rejected_research.md
```
