# Environment Diagnostic

> Read-only. Generated 2026-09-24. No repairs attempted. Secrets never printed (filenames + loader code only).

## Required Python version

- **3.12** everywhere: `install_requirements.bat:8,17,21,30,34`, `run_automated.bat:7`, `run_full_report.bat:6`, `run_t212_analysis.bat:13,22`, `run_t212_export.bat:8`, `README.md:102-103,106,132-134` (`py -3.12 ...`).
- No runtime gate: `grep sys.version|version_info|python_requires` in `investment_engine/` → 0; `requirements.txt` has no `python_requires`. Direct `python portfolio_ai_assistant.py` with a non-3.12 default is unchecked (bats catch it, bare `python` does not).
- Verified this PC: `py -3.12` works; `tests/` 261 passed (~65s), `experimental/tests/` 52 passed (~17s) on Python 3.12.

## Interpreter assumptions

| Launcher | Command | Assumption |
|---|---|---|
| `run_full_report.bat` | `py -3.12 portfolio_ai_assistant.py --config portfolio_config.json --investment-engine --generate-full-report` | `py` launcher + 3.12 installed |
| `run_automated.bat` | same via `py -3.12` | same |
| `run_t212_analysis.bat` / `run_t212_export.bat` | `py -3.12 ...` | same |
| `install_requirements.bat` | `py -3.12 -m pip install -r requirements.txt` + `find_spec` checks | same |
| `README.md` scheduled | `py -3.12 portfolio_ai_assistant.py --scheduled` | **flag does not exist** in current argparse — verify before scheduling |
| `README.md` pre-flight | `--validate-only`, `--dry-run --no-ai`, `pytest tests/ -q` | first two flags do not exist currently |
| `experimental/README.md` | `pytest tests/test_market_regime.py`, `pytest experimental/tests/ -v` | valid (paths only; root is repo dir, not `AsistantV5/`) |
| `portfolio_config.json:13-16` | `output_folder=reports`, `archive_folder=reports/archive`, `cache_folder=data/cache`, `log_folder=logs` | writable dirs; created on demand |
| `settings.py:183,224` + `factory.py:60-93` | LM Studio `100.101.20.64:1234`, Ollama `100.125.47.31:11434`, llama.cpp `100.125.47.31:11435` | Tailscale/VPN assumed |

## Pip package requirements (`requirements.txt:1-23`)

```text
requests>=2.31.0, yfinance>=1.6.0, pandas>=2.0.0, ddgs>=0.2.0, python-dotenv>=1.0.0, pydantic>=2.0.0
scipy>=1.10.0, numpy>=1.24.0
feedparser>=6.0.10, finvizfinance>=1.0.0
# pandas-ta>=0.4.71b0  (commented: Requires Python 3.12+ for numba)
# ta-lib>=0.4.24       (commented: needs VC++ Build Tools / brew / apt libta-lib-dev)
# transformers, torch  (commented: optional FinBERT)
```

No upper pins (`>=` without `<`). No `python_requires`. `install_requirements.bat` installs this file then `find_spec`-checks optionals (correct: no import).

## Optional dependencies

| Dep | Declared | Detected | Fallback | Status here |
|---|---|---|---|---|
| `pandas-ta` | commented `:10` | `technical_analysis.py:13-18` narrow `ImportError` → `HAS_PANDAS_TA` | manual indicators `:173-285` | **INSTALLED in this PC's 3.12 site-packages** (pytest `Pandas4Warning` proves import) → ta-path active here; manual on PCs without it |
| `TA-Lib` | commented `:19` | `:22-27` narrow → `HAS_TALIB` | skip candlesticks + debug | not installed (expected on Windows) |
| `finvizfinance` | declared | `:52-61` narrow + `_FINVIZ_DISABLED` kill-switch | `{}/[]/DataFrame()` | installed; US-only + API-change tolerant |
| `playwright` | NOT in requirements | `web_researcher.py` + `market_data` Playwright fallback (bare `except:`) | `{}` / manual | implicit optional — add to requirements or document |
| `tzdata` | NOT declared | `market_data.py:22,35` `ZoneInfo Europe/Bratislava` | none | Windows needs `tzdata` package otherwise `ZoneInfoNotFoundError` |

## pandas / numpy / scipy compatibility

- Floor versions only (`pandas>=2.0`, `numpy>=1.24`, `scipy>=1.10`); no pins → minor-version drift can shift `rolling/ewm/std` numerics and `find_peaks` behavior. Pin in Phase 1 (`pandas==`, `numpy==`, `scipy==` tested here) and record in manifest.
- `pandas` copy-on-write: installed `pandas_ta` triggers `Pandas4Warning: mode.copy_on_write deprecated (always enabled pandas>=3.0)` — harmless but confirms pandas 3.x-era behavior on this PC.
- `numpy`/`scipy` hard-used by `peak_valley` + indicators; `scipy` missing breaks regime (no flag) — keep required.
- `pydantic` 2.x: `schemas/ai_recommendations.py:106` class-based `config` emits `PydanticDeprecatedSince20` — migrate to `ConfigDict` in Phase 5.

## pandas-ta detection behavior (exact)

```python
# technical_analysis.py:13-18
try:
    import pandas_ta as ta
    HAS_PANDAS_TA = True
except ImportError:          # narrow — numba-level errors propagate (correct)
    HAS_PANDAS_TA = False
    logger.warning("pandas-ta not installed, using manual indicators")
# analyze:162-169 → ta-path (_apply_trend/momentum/volatility/volume/candlestick) else manual
# _validate_dependencies:127-131 logs warning/info only
```

- Broad-`except` hiding? **No** at this gate. Report logic change? **Yes** (columns + numerics + Keltner/session-VWAP/real ADX-Supertrend). Cross-PC risk: **confirmed** (installed here, commented in requirements → other PC likely manual).
- `talib` gate identical shape (`:22-27`); inner `ta.cdl_pattern` errors swallowed at `:441-447` (debug only).

## TA-Lib status

Not installed; commented out by design for Windows. `_apply_candlestick_patterns:436-448` returns `df` unchanged when `HAS_TALIB` false. No report dependency. Keep optional/validation-only.

## Windows setup implications

- Use `py -3.12` (not `python`) per bats; `cd /d "%~dp0"`, `chcp 65001` for UTF-8 logs/reports (SK strings in rules).
- `os.replace` atomic publish + `fsync` + `shutil.copy2` archive — Windows-safe.
- `latest_run.log` hardlink with copy fallback (`portfolio_ai_assistant.py:41-46,264-265`).
- `pathlib` throughout; no POSIX-only deps.
- Tailscale IPs for local models — both PCs need VPN or per-PC `portfolio_config.json` overrides (do not commit per-PC IPs; use env overrides).
- `zoneinfo` needs `tzdata` on Windows: `pip install tzdata` if `ZoneInfoNotFoundError`.
- `cleanup.bat` retention: archive `*.md|json` + `raw_endpoint_dump_*` >30d, `logs/*` >30d, `__pycache__/.pytest_cache` — safe to run (does not touch sources/config).

## Exact safe verification commands (both PCs)

```cmd
py -3.12 --version
py -3.12 -m pip check
py -3.12 -c "import sys; print(sys.version)"
py -3.12 -c "import importlib.util; print({m: bool(importlib.util.find_spec(m)) for m in ['yfinance','pandas','scipy','feedparser','finvizfinance','pandas_ta','talib','tzdata','playwright']})"
py -3.12 -c "import pandas,numpy,scipy,yfinance; print(pandas.__version__, numpy.__version__, scipy.__version__, yfinance.__version__)"
py -3.12 -m pytest tests/ -q --tb=no -p no:cacheprovider
py -3.12 -m pytest experimental/tests/ -q --tb=no -p no:cacheprovider
git status --short
git check-ignore -v api.env data/cache reports logs
```

Do NOT run the live assistant until `.gitignore` exists and `api.env` is ignored. Do NOT paste `api.env` contents, `Authorization/Bearer/key=` values, or raw dumps. Expected here: `tests/` 261 passed, `experimental/` 52 passed; `git status` all `??` (zero commits); `check-ignore` currently no-match (must become match after fix).

Before scheduling: verify `argparse` flags (`--scheduled/--validate-only/--dry-run` currently absent) and decide `runtime/run.lock` + exit-code implementation (README vs code).

## Proposed `verify_environment.py` specification (DO NOT CREATE YET)

Purpose: one offline command both PCs run before any cleanup; exit codes `0 OK / 1 missing-core / 2 secret-unignored`; prints `PRESENT/MISSING/OK/WARN/FAIL` only — never secret values.

1. `python_version`: `sys.version_info == (3,12,x)` else FAIL (warn on patch drift, fail on minor).
2. `pip_check`: `python -m pip check` returncode 0 else FAIL (print first lines only).
3. `core_matrix`: `find_spec` for `requests,yfinance,pandas,dotenv,pydantic,scipy,numpy,feedparser,finvizfinance` — all PRESENT else exit 1.
4. `optional_matrix`: `pandas_ta/talib/playwright/tzdata` — report PRESENT/MISSING as WARN (never fail); record which indicator path will activate.
5. `versions_pin`: print `pandas/numpy/scipy/yfinance` versions; WARN if outside tested set (to be pinned in Phase 1).
6. `requirements_parse`: parse `requirements.txt` (ignore comments) vs `find_spec` — missing core → exit 1.
7. `secret_guard`: `api.env` exists? (informational) + `git check-ignore api.env` must match else exit 2; same for `data/cache`, `reports`, `logs`; fail if `.gitignore` missing.
8. `config_dirs`: `portfolio_config.json` loads; `output/archive/cache/log` dirs exist or creatable (no writes beyond `tmp` probe file deleted immediately — or read-only check only).
9. `compile_gate`: `py_compile` on `portfolio_ai_assistant.py`, `investment_engine/main.py`, `portfolio/broker_first.py`, `portfolio/symbols.py`, `reporting/regime_report.py`, `research/technical_analysis.py`, `providers/factory.py` — syntax FAIL if any error.
10. `test_smoke` (optional flag `--smoke`): `pytest tests/test_result_contract.py -q` (fast, offline) — non-zero → FAIL.
11. Output: `verify_report.json` to stdout only (no file writes by default; `--write` opt-in writes `reports/debug/` which is gitignored after fix). Never print env values, keys, tokens, or file contents.
