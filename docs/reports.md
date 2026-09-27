# Reports

Topology: `reports/current/` (this run) · `reports/ai_context/` (machine archive:
`ai_context_<run_id>.md`, `portfolio_state_<run_id>.json` via `portfolio_analysis_<run_id>.json`,
`research_corpus_<run_id>.md/.json`) · `reports/debug/<run_id>/` (raw endpoint dump,
reconciliation CSV/MD, run.log; 30d/14-run retention) · `reports/archive/<run_id>/`
(full copy of `current/`). Legacy `reports/latest/` V4 layout and `reports/summary/`
writes are retired (stale files archived outside git, not rewritten).

`portfolio_intelligence_brief.md`: deterministic, ≤60 lines — stance/regime/recon
line, ONE account-warning line on FAIL/DEGRADED, account line, ≤5 priority items,
news+earnings, ≤3 watchlist ideas, one models line. No sized orders, no invented
prices, no per-position warning repeats.

`portfolio_decision_brief.md` (`documents.render_brief`): Executive → Monitoring →
Earnings 7d → News 48h → Watchlist; FAIL renders research-only disclaimer.
`t212_portfolio_snapshot.md` (`documents.render_snapshot`): the single holdings
table from unified rows. `portfolio_analysis.json`: canonical result dict
(sanitized settings/T212 data). `run_manifest.json`: run_id, generated_at,
recon_status, files+sha256, counts (incl. corpus_items, skill_outputs),
`provider` (per-stage winners summary), `stance`, `providers_per_stage`,
`indicator_engine` + library versions, `skill_versions`, `policy_version`.

Consistency contract (test-enforced): all artifacts share recon status/delta,
canonical signals, row count, and run_id. Writes are atomic (tmp + fsync +
os.replace). Stance vocabulary: HEALTHY/DEGRADED/FAIL via single
`documents.portfolio_stance` (fail-safe: non-PASS → DEGRADED).
