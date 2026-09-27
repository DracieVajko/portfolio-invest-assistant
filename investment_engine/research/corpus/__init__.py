"""Phase 5 research corpus package.

Adapters convert fetched records into normalized ResearchItems with
failure isolation (an adapter never raises: it yields FAILED items).
Builder writes the full corpus + curated decision set to ai_context/.
"""

from investment_engine.research.corpus import builder, dedupe, freshness, tiers  # noqa: F401
