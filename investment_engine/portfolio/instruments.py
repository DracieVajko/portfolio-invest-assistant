"""Instrument identity resolution (no silent substitution).

Master entries are caller-supplied (fixture or inventory file):
{"name": str, "isin": str, "tickers": {exchange: ticker},
 "broker_ids": [...], "instrument_type": "common"|"etf"|"etp-leveraged"|...,
 "leverage_factor": float|None, "keywords": [...], "currency": str}

resolve_asset() is pure and total: unknown queries return empty candidates,
never a fuzzy substitute.
"""

from __future__ import annotations

from typing import Any


def _norm(text: Any) -> str:
    return str(text or "").strip().upper()


def resolve_asset(query: str, master: list[dict],
                  *, exclude_leveraged: bool = False) -> dict:
    """Resolve a user reference to instrument candidates.

    Match order: exact ISIN -> exact broker id -> name/ticker/keyword
    substring. resolved=True only for a single unambiguous (non-excluded)
    hit. Leveraged ETPs are flagged leveraged=true.
    """
    out: dict = {"query": query, "resolved": False, "selected": None,
                 "candidates": [], "notes": []}
    try:
        q = _norm(query)
        if not q or not isinstance(master, list):
            return out
        entries = [e for e in master if isinstance(e, dict)]
        # Exact ISIN or broker id wins immediately.
        for entry in entries:
            ids = {_norm(entry.get("isin"))}
            ids.update(_norm(b) for b in (entry.get("broker_ids") or []))
            ids.discard("")
            if q in ids:
                cand = _candidate(entry)
                out.update(resolved=True, selected=cand, candidates=[cand])
                return out
        # Substring over name, tickers, keywords.
        hits = []
        for entry in entries:
            hay = {_norm(entry.get("name"))}
            hay.update(_norm(t) for t in (entry.get("tickers") or {}).values())
            hay.update(_norm(k) for k in (entry.get("keywords") or []))
            hay.discard("")
            if any(q in h or h in q for h in hay):
                hits.append(_candidate(entry))
        if exclude_leveraged:
            hits = [h for h in hits if not h["leveraged"]]
        if len(hits) == 1:
            out.update(resolved=True, selected=hits[0], candidates=hits)
        else:
            out["candidates"] = hits
            if len(hits) > 1:
                out["notes"].append("ambiguous: state share class / ISIN")
        return out
    except Exception:
        return out


def _candidate(entry: dict) -> dict:
    itype = str(entry.get("instrument_type", "common") or "common").lower()
    leveraged = itype == "etp-leveraged" or bool(entry.get("leverage_factor"))
    tickers = entry.get("tickers") if isinstance(entry.get("tickers"), dict) else {}
    return {"name": str(entry.get("name", "")),
            "isin": str(entry.get("isin", "")),
            "tickers": {str(k): str(v) for k, v in tickers.items()},
            "instrument_type": itype,
            "leveraged": leveraged,
            "currency": str(entry.get("currency", ""))}
