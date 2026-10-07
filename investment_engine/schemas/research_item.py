"""Phase 5 normalized research item (18-field contract).

Every normalized source item carries these fields. Decision-relevant items
must be ≤48h old with a parseable UTC timestamp; older materials are
BACKGROUND-labelled; undated items can never be decision-relevant.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

FetchStatus = Literal["OK", "STALE", "UNDATED", "BLOCKED", "FAILED"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_utc(value: Any) -> Optional[datetime]:
    """Parse ISO/email date strings or datetimes to aware UTC. None when unparseable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text or text.lower() == "undated":
        return None
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def item_id_for_url(url: str) -> str:
    from investment_engine.research.corpus.dedupe import normalize_url

    return hashlib.md5(normalize_url(url).encode("utf-8")).hexdigest()[:16]


class ResearchItem(BaseModel, extra="forbid"):
    """Normalized evidence unit for skills and reports."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    canonical_url: str = Field(min_length=1)
    publisher: str = Field(default="unknown")
    published_at_utc: str = Field(default="")
    age_hours: Optional[float] = Field(default=None)
    source_tier: int = Field(default=3, ge=0, le=3)
    source_category: str = Field(default="general")
    ticker_tags: list[str] = Field(default_factory=list)
    sector_tags: list[str] = Field(default_factory=list)
    region_tags: list[str] = Field(default_factory=list)
    clean_extract: str = Field(default="")
    raw_extract_if_available: str = Field(default="")
    credibility: float = Field(default=0.0, ge=0.0, le=100.0)
    relevance_score: float = Field(default=0.0)
    sentiment_if_available: Optional[float] = Field(default=None)
    fetch_status: FetchStatus = Field(default="OK")
    failure_reason_if_any: str = Field(default="")
    # URL provenance: "article" when the canonical article URL is validated,
    # "aggregator_url" when only a feed/aggregator link (e.g. Google News)
    # could be preserved. Aggregator URLs stay traceable but are never
    # presented as canonical article links.
    url_kind: str = Field(default="article")

    @field_validator("canonical_url")
    @classmethod
    def _url_must_be_http(cls, value: str) -> str:
        low = value.strip().lower()
        if not (low.startswith("http://") or low.startswith("https://")):
            raise ValueError("canonical_url must be http(s)")
        return value.strip()

    @model_validator(mode="after")
    def _ok_requires_timestamp(self):
        if self.fetch_status == "OK" and parse_utc(self.published_at_utc) is None:
            raise ValueError("fetch_status OK requires a parseable published_at_utc")
        return self

    def decision_relevant(self, max_age_hours: float = 48.0) -> bool:
        """True only for fresh, decision-tier, retrievable evidence."""
        if self.fetch_status != "OK":
            return False
        if self.source_tier > 2:
            return False
        if self.age_hours is None:
            return False
        return 0.0 <= self.age_hours <= max_age_hours

    def background_label(self, max_age_hours: float = 48.0) -> str:
        if self.fetch_status != "OK":
            return self.fetch_status
        if self.age_hours is None or self.age_hours > max_age_hours:
            return "BACKGROUND"
        return "DECISION" if self.source_tier <= 2 else "BACKGROUND"
