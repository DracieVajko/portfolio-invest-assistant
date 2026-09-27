# Architecture

Target data flow: `portfolio_ai_assistant.py` → `investment_engine/main.py:run_engine`
→ broker-first snapshot (`portfolio/broker_first.py`) → unified rows + canonical
signals (`reporting/regime_report.py`) → research corpus (`research/corpus/`) →
skills (`skills/`, first four wired) → risk/order plans (`risk/`, advisory only)
→ layered reports (`reporting/report_structure.py`, topology `current/`,
`ai_context/`, `debug/<run_id>/`, `archive/<run_id>/`).

Canonical contracts: `AccountSnapshot → ReconciliationResult` (sole money-math
producer: `broker_first.reconcile_snapshot`); `UnifiedRow` (ISIN-first identity,
`identity_provenance`); `ResearchItem` (18 fields, `schemas/research_item.py`);
`CanonicalSignals` (`{display: signal}` + HOLD defaults); `OrderPlan`
(advisory-only, `human_confirmation_required: true`); `RunManifest` (recon,
sha256 files, counts, `providers_per_stage`, `indicator_engine`, versions,
`skill_versions`, `policy_version`).

Import rules: CLI → main → {reporting → {portfolio → symbols}, research,
providers, risk, skills} → schemas/config. Never upward. Forbidden: any
`experimental` import outside `experimental/`; T212 clients in skills/risk/
research/providers; network in portfolio/risk/schemas/reporting; `pandas_ta` /
`talib` outside `technical_analysis.py` + `scripts/verify_indicators.py`;
order-placement capability anywhere (grep-guarded).

Source-of-truth table: broker values → T212 snapshot; recon → `reconcile_snapshot`;
identity → ISIN-first; technicals → manual engine (`research/indicators.py`,
`manual-v1`); evidence → corpus; signals → canonical map; risk → `risk/eligibility`;
attribution → `providers/attribution` (winners, never configured names).
External prices never overwrite broker values (VERIFIED-gated analytics only).
