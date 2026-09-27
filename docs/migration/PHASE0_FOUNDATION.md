# Phase 0 — Foundation (observation + safety only)

> No architecture refactor, no module moves, no legacy deletions, no valuation /
> reconciliation changes, no strategy changes, no LLM skills. No commit created.

## Exact files changed

| File | Change |
|---|---|
| `.gitignore` | CREATED — strict ignore list (secrets, reports, logs, data/cache, runtime, caches, venvs, IDE, backtest outputs, raw dumps, cookies, temp/model files) |
| `api.env.example` | CREATED — placeholder names + fake values only; real `api.env` never read or copied |
| `portfolio_ai_assistant.py` | Module-level `logger = logging.getLogger(__name__)` (fixes latent `NameError` on archive-copy failure in `write_reports`); testable `check_python_version()` gate (3.12.x, patch versions allowed) called at start of `main()` |
| `scripts/verify_environment.py` | CREATED — offline verifier per audit spec (exit 0/1/2, `--smoke`, never prints secrets, writes no files by default) |
| `requirements.txt` | Added `tzdata` core requirement + section comments (core / optional technical / optional research); `pandas-ta` stays optional; no pins added |
| `README.md` | Run section now documents only real flags (`--config`, `--investment-engine`, `--generate-full-report`); `--no-ai/--asset/--scheduled/--validate-only/--dry-run` marked planned-not-implemented; `py -3.12` pinned; verifier commands + pandas-ta fallback documented; scheduled/lock/exit-code claims corrected |
| `tests/test_phase0_foundation.py` | CREATED — 10 tests (gate accept/reject/message, module logger, archive-failure survival, example placeholder-only + key allowlist, verifier secret silence, engine reporter, interpreter 3.12) |

## Test results (this PC, Python 3.12.10)

- `py -3.12 -m pytest tests/test_phase0_foundation.py -q` → **10 passed**
- `py -3.12 -m pytest tests/ -q` → **271 passed** (261 existing + 10 new)
- `py -3.12 -m pytest experimental/tests/ -q` → **52 passed**
- `py -3.12 scripts/verify_environment.py` → **exit 0, RESULT OK**
  (core 11/11 incl. `tzdata`; optional: `pandas_ta` PRESENT, `talib` MISSING, `playwright` PRESENT;
  engine here = pandas-ta path; `pip check` OK; syntax 7/7 OK)

## Gitignore verification result

```text
git check-ignore -v api.env reports logs data/cache
  .gitignore:7:*.env        api.env
  .gitignore:12:reports/    reports
  .gitignore:16:logs/       logs
  .gitignore:20:data/cache/ data/cache
git check-ignore -v runtime api.env.example requirements.txt
  .gitignore:34:runtime/    runtime          (ignored)
  .gitignore:8:!api.env.example  api.env.example  (negation match = TRACKED, correct)
  requirements.txt -> no match (TRACKED, correct)
git status --short
  api.env, reports/, logs/, runtime/ absent (ignored) ✓
  .gitignore, api.env.example, scripts/, docs/, tests/, sources present as untracked candidates ✓
  data/ listed only for non-cache inputs (e.g. data/portfolio_performance.toml); data/cache/ itself ignored ✓
```

## Manual commands the user should run before the first commit

On EACH PC (second PC first, to confirm the manual-indicator path):

```cmd
py -3.12 --version
py -3.12 scripts/verify_environment.py
py -3.12 -m pip install -r requirements.txt
py -3.12 scripts/verify_environment.py --smoke
git status --short
git check-ignore -v api.env reports logs data/cache
git diff --stat
```

Then review the untracked list and confirm nothing sensitive appears
(`api.env`, `reports/`, `logs/`, `data/cache/`, `runtime/` must stay absent).
Suggested first commit (review filenames, then run):

```cmd
git add .gitignore api.env.example requirements.txt README.md portfolio_ai_assistant.py scripts/verify_environment.py tests/test_phase0_foundation.py
git status --short
git commit -m "Phase 0 foundation: gitignore, env example, 3.12 gate, verifier, tzdata, README"
```

## Recommended first commit file list

- `.gitignore` — must be in the very first commit, otherwise secrets leak on `git add .`
- `api.env.example` — safe template (placeholders verified by test)
- `requirements.txt` — policy comments + `tzdata`
- `README.md` — truthful flags/runtime/verifier docs
- `portfolio_ai_assistant.py` — logger + 3.12 gate only
- `scripts/verify_environment.py` — offline verifier
- `tests/test_phase0_foundation.py` — 10 regression tests

Deliberately NOT in the first commit: source tree remainder, `portfolio_config.json`
(user-specific), `PIEs/`, docs/audit, this file — stage those in a reviewed second commit.

## Confirmation: secrets / generated files excluded

- Real `api.env` was never opened, read, or copied; `api.env.example` contains only
  `paste-your-*` / `placeholder-*` / empty / localhost / `auto` values (asserted by
  `test_api_env_example_has_only_placeholders` + key allowlist test).
- `verify_environment.py` prints only PRESENT/MISSING/OK/FAIL labels and version
  numbers (asserted by `test_verify_environment_never_prints_secret_values` with a sentinel).
- Ignored and absent from `git status`: `api.env`, `reports/`, `logs/`, `runtime/`,
  `data/cache/`, bytecode/caches. No commit was created by this task.
- No valuation, reconciliation, strategy, provider, or report logic was altered;
  the only production-code delta is the logger safety fix + version gate.
