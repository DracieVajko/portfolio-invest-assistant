# Skills

Registry: `investment_engine/skills/registry.py` (`SKILL_VERSIONS`, `load_prompt`,
`load_manifest`, `get_skill`, `run_skill` — never raises). Prompts load ONLY via
the registry from per-skill `prompt_vN.md` (pinned versions in the manifest).
Contracts: `contracts.require_evidence()` runs BEFORE any prompt render
(insufficient → `INSUFFICIENT_EVIDENCE`, never prose);
`contracts.validate_citations()` rejects unknown corpus IDs;
`contracts.confidence()` is `{value 0..1, basis[]}`.

Live skills (v1): PortfolioRiskManager (broker-truth posture/breaches/cash;
no LLM needed) · NewsEventAnalyst (event classification, ≥1 fresh T0–T2 item
per event, ≥2 publishers for CONFIRMED; deterministic grouping fallback) ·
EquityResearchAnalyst (thesis templates; VERIFIED-only technicals; no targets,
no averaging-down endorsement, no sizes) · InvestmentCommittee (deterministic
reconciliation: canonical signals final, unknown tickers dropped with dissent,
FAIL → WATCH except risk-rule SELL).

Model routing: risk/committee → decision_model; news/equity → decision_model
(+ writer for brief prose); all via `ChainedFallbackProvider` with per-stage
attribution; deterministic fallbacks labelled. Later roadmap: PolicyTrumpWatch,
EnergyCommoditiesAnalyst, HealthcareAnalyst, CryptoAnalyst, WatchlistDiscovery
(validation-gated, default max 3/run), TechnicalSetupPlanner (canonical
indicators only, mapping-gated, no execution fields). Legacy inline prompts in
`main.py` remain for prose sections (prompt cutover is a follow-up workstream).
