# Portfolio AI Assistant (V5 modular engine)

Read-only investment monitoring: Trading 212 sync, Yahoo Finance technicals,
48h news with relevance ranking, deterministic scoring with fail-closed gates,
and local-LLM commentary (reasoning model for decisions, fast model for summaries).

## Quick start

```cmd
install_requirements.bat
copy api.env.example api.env              ^| fill in T212 + provider keys
copy portfolio_config.example.json portfolio_config.json   ^| your assets
copy data\ticker_aliases.example.json data\ticker_aliases.json   ^| optional symbol fixes
py -3.12 portfolio_ai_assistant.py --config portfolio_config.json --investment-engine --generate-full-report
```

Scheduled runs: Windows Task Scheduler job `Portfolio AI Assistant`, daily 09:00 + 21:00,
runs `run_automated.bat`. Overlapping runs are not started twice
(`MultipleInstances IgnoreNew`).

## How it works

`portfolio_ai_assistant.py` (thin wrapper) → `investment_engine.main.run_engine()`:

1. Broker sync (read-only T212 API) + reconciliation gate (FAIL blocks BUY/ADD/REDUCE)
2. Ticker mapping (`portfolio/symbols.py`: display strip, alias overrides incl.
   `data/ticker_aliases.json`, allowlist gate) + live retry for UNRESOLVED
3. Market data (yfinance technicals), earnings calendar, news (Google RSS +
   Trump/macro watchlist, relevance + source tiers)
4. Canonical signals (one BUY/SELL/HOLD mapping shared by all sections)
5. LLM stages via provider chain (LM Studio → llama.cpp → Ollama → Gemini →
   Mistral → deterministic fallback); reasoning model for decisions,
   writer model for the summary; single model switch per run
6. Atomic publish to `reports/` + `run_manifest.json`

AI never moves money and never overrides broker valuation; unverified AI cash
numbers are discarded against broker truth; off-portfolio AI targets are dropped.

## Outputs (all under `reports/`, regenerated every run)

- `portfolio_decision_brief.md` — decision brief (read this)
- `t212_portfolio_snapshot.md` — account snapshot
- `portfolio_analysis.json` — full machine-readable result
- `summary/portfolio_brief.md` — human brief
- `failed_tickers.md` — unresolvable tickers + copy-paste `symbol_aliases` JSON
- `run_manifest.json` — run_id, file sha256, counts, context estimates
- `ai_context/`, `debug/<run_id>/`, `archive/` — machine context, diagnostics, history

## Configuration

- `api.env` (gitignored): T212 pair, `LM_STUDIO_BASE_URL` (`.../v1`),
  `DECISION_MODEL` (reasoning), `WRITER_MODEL` (summary), cloud keys.
- `portfolio_config.json` (gitignored): `settings` (thresholds, news, folders,
  provider), `assets[]`, `symbol_aliases`, `portfolio_rules`.
- `data/ticker_aliases.json` (gitignored): extra T212→Yahoo fixes.
- Model sampling presets: `investment_engine/providers/presets.py`
  (reasoning temp 0.6 / summary temp 0.2; verified against LM Studio).
- Loaded-model context length is an LM Studio app setting (the API cannot set
  it); per-run maxima are logged and stored in the manifest — size app ctx
  at or above the reported number.

## Tests

```cmd
py -3.12 -m pytest tests/ experimental/tests/ -q
```

Offline unit tests (mocked network) live in `tests/`; backtest research tests
in `experimental/tests/` with fixture `sample_ohlcv.csv`.

## Project structure

```text
portfolio_ai_assistant.py   # CLI wrapper (report layers + atomic publish)
investment_engine/          # main.py (run_engine), research/, providers/,
                            # reporting/, portfolio/, accounting/, schemas/,
                            # scoring/, discovery/, memory/, prompts/, config/
trading212_*.py             # flat T212 client used by the engine (canonical)
tests/                      # unit tests (offline)
experimental/               # isolated research backtest (not part of reports)
data/                       # caches (gitignored) + *.example.json templates
PIEs/                       # pie CSV exports (reference) + config/*.json
check_report.py             # inspect reports/portfolio_analysis.json
                            #   (positions|summary|missing|raw)
run_automated.bat           # scheduled full run (no pause)
cleanup.bat                 # retention pruning (logs/reports/debug/ai_context)
Zalohy/                     # local backups, never committed
portfolio-invest-assistant-clean/  # version snapshots, never committed
```

## Security

Never commit `api.env`, `portfolio_config.json`, `data/ticker_aliases.json`,
`logs/`, `reports/`, `runtime/`, `data/cache/`, real `PIEs/*.csv`.
No orders are ever placed (read-only). Not financial advice.

## License

MIT
