"""Phase 5 deduplication: canonical URLs + fuzzy titles, first-seen wins."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

_TRACKER_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "u",
})


def normalize_url(url: str) -> str:
    """Lowercase host, drop query/fragment/trackers and trailing slash."""
    text = (url or "").strip()
    try:
        parts = urlparse(text)
    except Exception:
        return text.lower()
    host = (parts.hostname or "").lower()
    path = (parts.path or "").rstrip("/") or "/"
    query = "&".join(
        p for p in (parts.query or "").split("&")
        if p and p.split("=")[0].lower() not in _TRACKER_PARAMS)
    norm = f"{parts.scheme.lower()}://{host}{path}"
    if query:
        norm += f"?{query}"
    return norm


def item_id(url: str) -> str:
    return hashlib.md5(normalize_url(url).encode("utf-8")).hexdigest()[:16]


def _tokens(title: str) -> set[str]:
    return set(t for t in re.findall(r"[a-z0-9]{3,}", (title or "").lower()))


def titles_similar(a: str, b: str, threshold: float = 0.85) -> bool:
    """Token-Jaccard similarity on 3+ char tokens."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


def dedupe(items: list[dict]) -> list[dict]:
    """First-seen wins on canonical URL; fuzzy-title collapse across URLs."""
    seen_ids: set[str] = set()
    kept_titles: list[str] = []
    out: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        uid = item.get("id") or item_id(str(item.get("canonical_url", "")))
        if uid in seen_ids:
            continue
        title = str(item.get("title", ""))
        if any(titles_similar(title, kept) for kept in kept_titles):
            continue
        seen_ids.add(uid)
        kept_titles.append(title)
        out.append(item)
    return out
