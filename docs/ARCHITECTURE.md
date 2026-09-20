# Architecture & Dataflow — V4-hardened monolith (final)

Single-process pipeline: `portfolio_ai_assistant.py` (~3.7k lines) imports
`v4_hardening.py` (exit codes, lock, atomic writes, mapping audit, gates).
No database, no server, no scheduler inside the repo — scheduling is an external
Windows Task Scheduler job; the standalone `trading212/` package is a separate CLI.

## 1. System context

```mermaid
flowchart LR
    subgraph Local["This repo (read-only tool)"]
        PIPE["portfolio_ai_assistant.py<br/>_run_pipeline"]
        V4["v4_hardening.py<br/>gates · lock · audit"]
        PKG["trading212/ package<br/>standalone CLI"]
        PIPE <--> V4
    end
    T212[("Trading 212 API v0<br/>GET only")]
    YF[("Yahoo Finance<br/>yfinance")]
    DDG[("DuckDuckGo<br/>ddgs")]
    LLM["AI backends<br/>LM Studio · Ollama<br/>OpenRouter · Gemini · Mistral"]
    CFG[("portfolio_config.json<br/>+ api.env (local, gitignored)")]
    OUT[("reports/<br/>latest · archive · legacy")]
    REV[("Revolut manual file<br/>.json / .csv / .txt")]
    PIPE --> T212
    PIPE --> YF
    PIPE --> DDG
    PIPE --> LLM
    CFG --> PIPE
    REV --> PIPE
    PIPE --> OUT
    PKG --> T212
    PKG --> LLM
```

External reads only. Nothing in the pipeline creates, modifies or cancels broker orders.

## 2. Pipeline (STEP 1–8, exact console order)

```mermaid
flowchart TD
    S1["STEP 1 · config load → migrate_legacy_config (in-memory)<br/>→ load_environment_variables(api.env) → validate_config_full<br/>errors stop the run, warnings pass"]
    S1 --> BR{"--migrate-config / --validate-only?"}
    BR -- yes --> EXITM["file-only branch, exit"]
    BR -- no --> LOCK["OS-level single-run lock (runtime/run.lock)<br/>held → exit 3, reports untouched"]
    LOCK --> SB["Broker sync (read-only)<br/>fetch_trading212_with_retry → /equity/portfolio<br/>fetch_trading212_cash → totals (total/result/free)<br/>load_revolut_data (manual file)"]
    SB --> MM["merge_broker_data → MappingAudit/asset<br/>auto_discover_unmatched_positions (temp assets, never written back)"]
    MM --> S2["STEP 2 · AI warmup (background thread)<br/>probe chain, preload first reachable local model"]
    S2 --> S3["STEP 3 · filter --group / --asset"]
    S3 --> S4["STEP 4 · per-asset collection (§3)"]
    S4 --> S5["STEP 5 · scores & recommendations (counters only)"]
    S5 --> S6["STEP 6 · global + discovery news"]
    S6 --> S7["STEP 7 · canonical result + AI alpha→summary"]
    S7 --> S8["STEP 8 · atomic publish (§6)"]
```

## 3. Per-asset flow

```mermaid
flowchart TD
    A["asset (config or auto-discovered)"] --> C["collect_asset_data<br/>yf.Ticker.info + history<br/>price/MAs/RSI/trend + per-ticker news"]
    C --> S["calculate_signals → buy/sell %<br/>technical + sentiment scores"]
    S --> Q["calculate_data_quality → 0–100 + dq_status"]
    Q --> M["mapping audit + broker-vs-market price gate<br/>warn ≥15% · block ≥35% · currency-mismatch block"]
    M --> R{"price missing<br/>+ NO_PRICE_DATA/BROKER_ONLY?"}
    R -- yes --> RT["resolve_ticker_with_fallback → retry collect<br/>success → continue below"]
    R -- no --> G
    RT -- still no data --> SKIP["T212_INTERNAL_TICKER branch<br/>action=NO_DATA · record_failed_ticker"]
    RT -- data found --> G
    G["staleness gate (last_bar age > stale_days → STALE)<br/>build_asset_summary → calculate_recommendation<br/>→ V4.finalize_action (hard gates)<br/>→ levels_allowed → compute_stop_levels (info only)"]
    G --> OUTA["enriched asset → collected_data"]
```

Gate order inside `finalize_action`: mapping block → data block → RSI contradiction
guards → threshold logic with margins — yielding
`ADD_CANDIDATE / HOLD / WAIT / REVIEW / REDUCE_CANDIDATE / DATA_UNAVAILABLE / REVIEW_MAPPING`.

## 4. Ticker resolution (4 tiers) + failure report

```mermaid
flowchart TD
    T["broker_symbol (e.g. SGLNL)"] --> T1["1 · normalize_t212_ticker<br/>built-in T212→Yahoo table (~90 lines, UK/EU/US)<br/>+ 23 exchange suffixes (_L_EQ→.L, _KS_EQ→.KS, …)"]
    T1 -- miss --> T2["2 · config symbol_aliases<br/>(explicit user overrides)"]
    T2 -- miss --> T3["3 · DDGS web search<br/>'SYMBOL Yahoo Finance ticker'<br/>Yahoo quote-URL extraction + validation"]
    T3 -- miss --> T4["4 · direct Yahoo lookup<br/>(maybe it works as-is)"]
    T4 -- fail --> F["record_failed_ticker → failed_tickers.md<br/>ready-to-copy assets[] + symbol_aliases JSON"]
```

`failed_tickers.md` is published only when non-empty; its entries also appear
in `data_quality.md` ("Failed ticker resolution"). Config fixes from the report
take effect on the next run.

## 5. AI backend chain

```mermaid
flowchart LR
    W["start_ai_warmup (thread)<br/>probe ai_chain_alpha"] --> L1["LM Studio (local)"]
    L1 -- unreachable --> L2["Ollama (local/remote)"]
    L2 -- unreachable --> L3["OpenRouter"]
    L3 -- unreachable --> L4["Gemini"]
    L4 -- unreachable --> L5["Mistral"]
    L5 --> ST["call_stage per stage<br/>(alpha, summary, fallback)<br/>warmed backend first, dead skipped"]
    ST --> P["run_ai_pipeline: alpha → summary<br/>appends '## Executive Brief'"]
```

Rules: `api_key` supports `${ENV_VAR}` (values live in `api.env`, redacted everywhere);
reasoning-model output falls back to the `reasoning` field when `content` is empty;
every attempt is traced (`stage/backend/model/ok/latency_s/error`) into the manifest.
Full AI text is stored **only** in `portfolio_report.json → ai_analysis`;
the `.md` dashboard shows AI provenance (backend/model/latency), never scores.

## 6. Publish layout (atomic temp-file + replace)

```text
reports/latest/portfolio_report.md     # dashboard: snapshot, attention, action tables, appendix
reports/latest/portfolio_report.json   # canonical result (assets, mapping, providers, ai_analysis)
reports/latest/data_quality.md         # provider/mapping audit incl. failed tickers
reports/latest/failed_tickers.md       # unresolvable tickers + copy-paste config (if any)
reports/latest/run_manifest.json       # run_id, exit code, timings, files, backends (no secrets)
reports/archive/YYYY-MM-DD/<run_id>/   # same files + log snapshot
reports/portfolio_analysis.md/json     # legacy compatibility copies
```

`--dry-run` runs everything in memory and publishes nothing; `--json-only` skips Markdown.

## 7. Config reference (see `portfolio_config.example.json`)

| Block | Keys (selection) |
|---|---|
| `settings` | `days_price_history` (3mo), `min_history_bars` (14), `stale_days` (7), `buy/sell_probability_threshold` (45/55), `action_*_margin/floor/band`, `rsi_overbought/oversold` (70/30), `mapping_price_warn/block_pct` (15/35), news limits + `max_news_age_hours` (48), `auto_discover_*`, `output_folder`, `runtime_dir`, `ai_warmup_wait` (30), `ai_probe_timeout` (5), load timeouts, `ai_chain_alpha/summary`, `global_news_queries`, `discovery_queries` |
| `assets[]` | `broker_symbol`, `yahoo_symbol`, `name`, `group`, `search_query`, `aliases`, `enabled` |
| `symbol_aliases` | explicit T212 → Yahoo overrides (beat the built-in table) |
| `ai_backends` | `alpha` (lmstudio/fin-r1), `summary` (lmstudio/gemma-4-12b), `fallback` (ollama/gpt-oss-20b); each `provider/base_url/model/timeout/num_ctx/temperature/enabled/api_key` |
| `portfolio_rules` | free-form strategy text injected into the AI prompt |

Secrets (`api.env`, from `api.env.example`): `TRADING212_API_KEY/SECRET`
(read-only pair), `OLLAMA_*`, `LM_STUDIO_*`, `OPENROUTER_API_KEY*`, `ODYSSEUS_URL`.

## 8. Module map

| File | Role |
|---|---|
| `portfolio_ai_assistant.py` | pipeline, providers, scoring, reports, CLI (`--config --scheduled --no-ai --no-interactive --validate-only --check-connectivity --migrate-config --debug --dry-run --group --asset --json-only`) |
| `v4_hardening.py` | `RunExit` (0–5), `RunLock`, atomic writes, `MappingAudit`, action/levels gates, `validate_config_full`, phase timing, secret redaction |
| `trading212/{auth,portfolio,integration}.py` | standalone read-only T212 client + CLI (`--health --summary --positions --cash --orders --dividends --pies --analyze --export`) |
| `tests/test_v4_hardening.py` | 55 offline tests, network fully mocked |
| `PIEs/` | pie CSV exports (local reference) + `config/*.json` strategy metadata |
| `data/` | local caches/notes (`portfolio_performance.toml` tracked; `cache/` gitignored) |
