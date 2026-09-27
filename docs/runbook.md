# Runbook

Requires Python 3.12 (`py -3.12`) and `api.env` (copied from `api.env.example`).

1. Install: `py -3.12 -m pip install -r requirements.txt`
   - Playwright browser (once per machine, for the automatic research sweep):
     `py -3.12 -m playwright install chromium`. Without it the sweep degrades
     gracefully (Yahoo/RSS data only) — the verifier reports the pip package,
     not the browser.
2. Verify (offline): `py -3.12 scripts/verify_environment.py`
   plus `py -3.12 scripts/verify_indicators.py` (compare the printed
   `snapshot_sha256` across PCs — must match).
3. Smoke: `py -3.12 scripts/verify_environment.py --smoke`
4. Full run: `py -3.12 portfolio_ai_assistant.py --config portfolio_config.json --investment-engine --generate-full-report`
   (or double-click `run_full_report.bat`). Needs live T212 + market network;
   without local LLMs all stages fall back to deterministic output (LM Studio
   is disabled by default: `LMSTUDIO_ENABLED=1` to enable).
5. Read `reports/current/portfolio_intelligence_brief.md` first (≤60 lines),
   then `portfolio_decision_brief.md`, `t212_portfolio_snapshot.md`.
6. Machine readers: `reports/current/portfolio_analysis.json` (+ `run_manifest.json`
   for sha256, recon, providers-per-stage, indicator engine, skill/policy versions).
7. Full evidence: `reports/ai_context/research_corpus_<run_id>.md`.
8. Debug: `reports/debug/<run_id>/` (raw dump, recon diagnostics, run log).
9. Manual T212 CLI: `py -3.12 tools/t212_cli.py --health --config api.env`
   (also `--analyze`, `--export <path>`). Read-only.
10. Cleanup old artifacts: `cleanup.bat` (respects 30-day retention; never touches sources).

Exit codes: `0` ok · `1` report/LLM-stage failure · `2` config/flag error.
`scripts/verify_environment.py`: `0` ok · `1` missing core · `2` secrets exposed.
