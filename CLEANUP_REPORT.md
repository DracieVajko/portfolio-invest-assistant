# CLEANUP_REPORT — v0.6.0 Monolith Staging

**Date:** 2026-08-29  
**Source (read-only):** `Zalohy/portfolio_ai_assistant_V4_5`  
**Target (single write directory):** `portfolio-invest-assistant-v0.6-staging`  
**Tag (planned, not yet created):** `v0.6.0-monolith`

## 1. TARGET pripravený?

**Áno.** TARGET existoval a bol prázdny — práca pokračovala bez mazania. Výsledný staging obsahuje čistý root projektu (nie vnorený priečinok) so štruktúrou podľa zadania.

```
portfolio-invest-assistant-v0.6-staging/
├── portfolio_ai_assistant.py
├── trading212/
│   ├── __init__.py
│   ├── auth.py
│   ├── portfolio.py
│   └── integration.py
├── PIEs/
│   ├── README.md
│   ├── example_daily_dividend.csv
│   ├── example_renewable.csv
│   ├── example_savings.csv
│   └── example_technology.csv
├── docs/
│   └── releases/
│       └── v0.6.0-monolith.md
├── api.env.example
├── portfolio_config.example.json
├── portfolio_config.json   (gitignored, identický s example)
├── .gitignore
├── requirements.txt
├── run_automated.bat
├── run_full_report.bat
├── run_t212_analysis.bat
├── run_t212_export.bat
├── README.md
├── CHANGELOG.md
└── CLEANUP_REPORT.md
```

Žiadne `data/`, `logs/`, `reports/`, `__pycache__/`, `.venv/`, `*.db`, `*.log`, `.git/` neboli vytvorené.

## 2. Vytvorené / upravené súbory

| Súbor | Akcia |
|-------|-------|
| `portfolio_ai_assistant.py` | Skopírovaný zo SOURCE, patchnutý (load_dotenv fallback, odstranené duplicitné definície `migrate_legacy_config` a `validate_config`) |
| `trading212/__init__.py`, `auth.py`, `portfolio.py`, `integration.py` | Skopírované zo SOURCE bez zmien (read-only T212 balík) |
| `requirements.txt` | Skopírované, odstránená presná duplicita `ddgs>=0.2.0` (ponechaný jeden výskyt) |
| `run_automated.bat`, `run_full_report.bat` | Skopírované bez zmien (volajú `python portfolio_ai_assistant.py`) |
| `run_t212_analysis.bat`, `run_t212_export.bat` | Opravené: volajú `python trading212\integration.py` s `%~dp0`-relatívnou cestou namiesto `trading212_integration.py` |
| `api.env.example` | Nový — iba názvy premenných s prázdnymi/localhost hodnotami (`TRADING212_ENABLED=false`, `TRADING212_API_BASE=https://live.trading212.com`, `OLLAMA_URL=http://127.0.0.1:11434`, `LM_STUDIO_BASE_URL=http://127.0.0.1:1234/v1`, `ODYSSEUS_URL=http://127.0.0.1:7000`, `REVOLUT_MANUAL_RESEARCH_FILE=data/...`) |
| `portfolio_config.example.json` | Nový sanitizovaný — 3 demo assety `AAPL`, `MSFT`, `BTC-USD`, žiadne reálne holdings/quantity/price/P/L, sanitizované URLs na `127.0.0.1`, `ai_max_assets=10`, `max_news_per_asset=10`, deduplikované kľúče, zachovaná schéma |
| `portfolio_config.json` | Nový (gitignored) — identický s `portfolio_config.example.json` pre okamžité `python portfolio_ai_assistant.py --no-ai` |
| `PIEs/README.md` | Nový — dokumentuje gitignore politiku a header |
| `PIEs/example_*.csv` (4) | Nové — hlavička `Slice,Name,...` + max 2 fiktívne riadky (`AAPL`, `MSFT` atď.), nulové hodnoty |
| `.gitignore` | Nový — presne podľa zadania (secrets, runtime, cache, DB, `fix_*.py`, IDE) |
| `README.md` | Prepísaný — verejný formát, nadpis `Portfolio AI Assistant — v0.6.0 Monolith`, varovanie hneď pod nadpisom, účel, read-only T212, `--no-ai`, lokálne LLM, architektúra, safe setup, CLI, known limitations, privacy, link na `docs/releases/v0.6.0-monolith.md` |
| `CHANGELOG.md` | Nový — lineage `v0.5.0` → `v0.6.0-monolith` → `v1.0.0` (WIP), bez vymyslených výsledkov |
| `docs/releases/v0.6.0-monolith.md` | Nový — release note s jasným odlíšením línií, sanitizáciou a fixami |

## 3. Vylúčené / sanitizované

**Neprenesené (excluded):**
`api.env`, `.env*`, `portfolio_config.json` (reálny), `data/` (vrátane `data/cache/*.db`), `logs/` (vrátane `latest_run.log`), `reports/` (vrátane `archive/`), `PIEs/*.csv` (4 reálne PIE exporty), `__pycache__/`, `.pytest_cache/`, `.venv/`/`venv/`, `*.pyc`, `*.db`, `*.sqlite`, `*.log`, `fix_call_stage.py`, `fix_settings.py`, `investment_engine/memory/memory.json`, `*Conflict*`, `.git/`. Prázdny `reports/` adresár nebol vytvorený.

**Sanitizované:**
- `api.env.example`: žiadne reálne `TRADING212_API_KEY`, `TRADING212_API_SECRET`, `TRADING212_ACCOUNT_ID`, Tailscale IP ani osobné cesty — iba prázdne hodnoty alebo `http://127.0.0.1:*`.
- `portfolio_config.example.json` / `portfolio_config.json`: odstránené ~30 reálnych assetov, nahradené 3 demo assetmi; všetky `quantity`/`average_price` = `null`; odstránené duplicitné JSON kľúče (`VWSBD` ×2, `C7A0` ×2); `lm_studio_base_url` → `http://127.0.0.1:1234/v1`; `odysseus_url` → `http://127.0.0.1:7000`; `ai_max_assets` 20→10; `max_news_per_asset`/`max_news_per_asset_limit` 0→10; ponechané `ai_backends` a `model_presets` bez zmien okrem sanitizácie URL.
- `PIEs`: reálne CSV neprenesené, nahradené 4 `example_*.csv` s fiktívnymi nulovými hodnotami.
- Dokumentácia: všetky absolútne cesty a private IPs nahradené relatívnymi alebo maskovanými popismi.

Žiadne reálne hodnoty credentials, tokenov, cookies, account ID, holdings, množstiev, P/L, dividend, finančných hodnôt, osobných ciest ani súkromných IP neboli vypísané — uvádzané sú iba relatívne cesty, kategórie a vykonané ochranné akcie.

## 4. Opravené vs. zdokumentované statické bugs

**Opravené (4):**

1. **`load_dotenv` bez importu** — `portfolio_ai_assistant.py:350-368` volalo `load_dotenv` bez importu (NameError ak `api.env` chýba). Oprava: pridaný `try: from dotenv import load_dotenv` s `_HAS_DOTENV` flagom pred `import requests` blokom; vetva `elif _HAS_DOTENV and load_dotenv: try: load_dotenv(override=True) except Exception: pass`. Aplikácia už necrashne bez `api.env` ani bez `python-dotenv`.

2. **Batch cesty** — `run_t212_analysis.bat:13,22` a `run_t212_export.bat:8` volali neexistujúci `trading212_integration.py`. Oprava: `python trading212\integration.py` s `cd /d "%~dp0"` pre relatívnu cestu. Overené textovo, nespúšťané.

3. **`requirements.txt`** — duplicitný riadok `ddgs>=0.2.0` (riadky 4 a 6) odstránený, ponechaný prvý výskyt. Ostatné dependencies nezmenené (`requests`, `yfinance`, `pandas`, `python-dotenv`).

4. **Duplicitné definície `migrate_legacy_config` a `validate_config`** — obe sa vyskytovali 2× identicky (`migrate_legacy_config` 153/712, `validate_config` 192/751). Odstránené skoršie identické bloky, ponechané neskoršie efektívne definície. Overené `Counter` a byte-identické porovnanie.

**Dočasné patch artefakty (neprenesené, zdokumentované):**
`fix_call_stage.py` a `fix_settings.py` neboli prenesené — ide o jednorazové patch nástroje a ich zmeny (upravený `call_stage` bez single-backend fallbacku a merge `ai_backends` do `settings`) sú už prítomné v hlavnom `portfolio_ai_assistant.py` (riadky `if "ai_backends" in config: settings["ai_backends"] = config["ai_backends"]`). Uvedené v `CHANGELOG.md` a tu.

**Zostali iba zdokumentované (known technical debt):**

- **Duplicitné `check_ollama_available` (2×) a `get_working_model` (2×)** — skoršie definície (wrapper na `check_lmstudio_available` / `list_lmstudio_models`) sú prekrývané neskoršími detailnejšími verziami (`_detect_backend` + `_probe_backend`). Neskoršie definície sú efektívne pri runtime (Python prepisuje). Skoršie sú evidentne mŕtve, ale nie identické; pre minimal-risk maintenance ponechané a označené ako debt v `CHANGELOG.md`, `docs/releases/v0.6.0-monolith.md` a tu. Žiadny monolit→`trading212/` migrácia nebola vykonaná.

- **Dual T212 read-only implementácia** — `portfolio_ai_assistant.py` inline (`fetch_trading212_positions`, `normalize_t212_ticker`, `load_environment_variables`, `merge_broker_data`, `auto_discover_unmatched_positions`) a `trading212/` package (`auth.py`, `portfolio.py`, `integration.py`) duplikujú rovnakú doménu. Nevykonaná migrácia, iba zdokumentované ako debt.

## 5. Výsledok statickej validácie

Vykonané lokálne, bez sieťových volaní a bez spúšťania aplikácie/bat/testov.

- **JSON parse:** `portfolio_config.example.json` — OK, `portfolio_config.json` — OK, žiadne duplicitné kľúče (overené `object_pairs_hook`).
- **Python syntax `py_compile` (bez importu):** `portfolio_ai_assistant.py` — OK, `trading212/__init__.py` — OK, `trading212/auth.py` — OK, `trading212/portfolio.py` — OK, `trading212/integration.py` — OK.
- **Text scan (bez vypisovania reálnych hodnôt):**
  - `api.env.example`: `TRADING212_API_KEY` prázdne — OK, `TRADING212_API_SECRET` prázdne — OK, žiadny Tailscale/private LAN IP — OK, žiadna absolútna `C:\Users\...` cesta — OK.
  - `portfolio_config.example.json` / `portfolio_config.json`: žiadne numerické `quantity`/`average_price`/P/L — OK (všetko `null`), žiadny Tailscale IP — OK, žiadna osobná cesta — OK.
  - Full scan všetkých súborov v TARGET: žiadne `C:\Users\<username>` absolútne cesty, žiadna `100.101.*` private IP, žiadne `TRADING212_API_KEY=<real>` — OK (reportované iba kategórie, nie hodnoty).
  - `.db` / `.log` odkazy: iba v `.gitignore` a v `portfolio_ai_assistant.py` logike (`LOG_DIR = Path("logs")`, `latest_run.log`), nie prítomné ako súbory — OK.
- **`.gitignore` coverage:** manuálne `fnmatch` overenie — ignoruje `api.env` (áno), `portfolio_config.json` (áno), `reports/` (áno), `logs/` (áno), `data/` (áno), `PIEs/private.csv` (áno, via `PIEs/*.csv`), `*.db`/`*.db-shm` (áno), `fix_*.py` (áno); neignoruje `PIEs/example_*.csv` (správne, via `!`), `portfolio_config.example.json`, `api.env.example` (správne).
- **Strom výstupu (hĺbka 3, ascii):**
```
|-- docs/
|   +-- releases/
|       +-- v0.6.0-monolith.md
|-- PIEs/
|   |-- example_daily_dividend.csv
|   |-- example_renewable.csv
|   |-- example_savings.csv
|   |-- example_technology.csv
|   +-- README.md
|-- trading212/
|   |-- __init__.py
|   |-- auth.py
|   |-- integration.py
|   +-- portfolio.py
|-- .gitignore
|-- api.env.example
|-- CHANGELOG.md
|-- CLEANUP_REPORT.md
|-- portfolio_ai_assistant.py
|-- portfolio_config.example.json
|-- portfolio_config.json
|-- README.md
|-- requirements.txt
|-- run_automated.bat
|-- run_full_report.bat
|-- run_t212_analysis.bat
+-- run_t212_export.bat
```

## 6. Explicitné potvrdenie

- **Žiadny Git repozitár nebol vytvorený ani menený:** Nepoužité `git init/add/commit/tag/branch/checkout/reset/rebase/push/pull/fetch/remote`, ani GitHub CLI — overené absenciou `.git/` v TARGET a zákazom v pravidlách.
- **Žiadny commit, žiadny tag, žiadny push** nevykonaný.
- **Žiadna sieťová operácia** nevykonaná — nespuštené `requests`, `yfinance`, `DDGS`, `Ollama`, `LM Studio`, `OpenRouter`, Trading 212 API, ani `gh`.
- **Aplikácia ani `.bat` súbory neboli spustené** — iba `py_compile` bez importu, `json.loads`, textové scany a diffy.
- **SOURCE ani `Zalohy/` neboli menené** — iba čítané; zapisovalo sa výhradne do TARGET.

## 7. Zastavené — čaká na ďalšie pokyny

Staging snapshot `v0.6.0-monolith` je pripravený na manuálny import ako nový maintenance commit a tag `v0.6.0-monolith` v samostatnom klone cieľového repozitára. Žiadne automatické Git/GitHub kroky neboli vykonané.

---
*Generované lokálne bez sieťových volaní; všetky kontroly overené spustením kódu.*
