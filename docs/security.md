# Security & Secrets

Never committed (`.gitignore` + `git check-ignore` verified): `api.env`, `.env`,
`*.env` (except `*.example`), `investment_policy.json` (template
`investment_policy.example.json` IS committed), `reports/`, `logs/`,
`data/cache/`, `runtime/`, caches, venvs, model files.

Setup: copy `api.env.example` → `api.env` (or `.env.example` pattern) and fill
real keys locally. `investment_policy.json`: copy from the example template.
Neither file is ever printed: the verifier and tests assert only
PRESENT/MISSING labels (sentinel-tested). Logs redact `Authorization`/`Bearer`/
`key=` values; result JSON carries sanitized settings/T212 data only
(`_sanitized_settings`, `_sanitized_t212_data`); raw dumps stay in gitignored
`debug/` with 30-day retention (`cleanup.bat`).

Broker access is GET-only (`trading212/` clients); no order endpoints exist and
`investment_engine/risk/` is import-grep-guarded against network/execution
capability. All order plans are advisory text with `human_confirmation_required`
and an explicit not-executed guard. First commit MUST contain `.gitignore`
before any other file is staged.
