# Research Sources

Normalized `ResearchItem` (18 fields, `schemas/research_item.py`): id (md5 of
canonical URL), title, canonical_url (tracker-stripped), publisher,
published_at_utc, age_hours, source_tier 0–3, source_category, ticker/sector/
region tags, clean_extract (≤600 chars, de-clickbaited), raw extract, credibility,
relevance, optional sentiment, fetch_status (OK/STALE/UNDATED/BLOCKED/FAILED),
failure reason. Decision-relevant = OK + tier 0–2 + age ≤48h; older = BACKGROUND;
undated = never decision-relevant (HTML fallbacks no longer stamp `now()`).

Adapters (`research/corpus/adapters.py`, each with a COMPLIANCE header): Google
News RSS (headlines+links, modest rate) · Slovak press RSS (RSS extracts; HTML
full-text only where ToS allow) · Reddit public JSON (rate-limited,
LOW_CONFIDENCE sentiment only) · macro/Trump keyword tracking · commodity/
analyst keyword fetchers · Finviz/EarningsHub/TradingView HTML stays opt-in
Playwright, low frequency, data-facts only. X/StockTwits: NO scraping in this
codebase (priority order pending user confirmation). Every adapter failure
yields FAILED items; the corpus builder never aborts the run.

Tiers: T0 broker/IR/exchange · T1 wire/regulator · T2 press/data pages ·
T3 social (forced T3 even with explicit override; never decision-grade).
Dedupe: canonical-URL md5 + fuzzy titles (Jaccard ≥0.85), first-seen wins.
Per-adapter file cache + thread caps + 429 backoff. Sector templates cover
AI/semis/cloud/data-centers/battery/lithium/renewables/utilities/grid/defense/
nuclear/hydrogen/dividend/ETF/crypto/healthcare/biotech. Full corpus +
decision subset persist to `ai_context/research_corpus_<run_id>.md/.json`.
