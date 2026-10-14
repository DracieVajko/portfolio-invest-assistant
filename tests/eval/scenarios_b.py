"""Tier-2 B-series scenario definitions (fixtures + prompts + checks).

Stub handlers return ONLY the fixture below (no network). Scenarios not
listed here follow the same dict shape; add with fixtures + expected checks.
"""
from __future__ import annotations

_SNAPSHOT = {
    "schema_version": 1, "generated_utc": "2026-10-14T12:00:00+00:00",
    "asset": {"ticker": "TST", "currency": "USD"},
    "data": {"last_complete_bar": "2026-10-13", "n_bars": 900,
             "price_basis": {"levels": "split_adjusted"}},
    "daily": {"close": 150.0, "sma50": 142.0, "sma200": 120.0, "rsi14": 62.0,
              "atr14": 3.0, "regime": "STRONG_UPTREND", "modifiers": [],
              "high52": 155.0, "low52": 100.0},
    "weekly": {"regime": "STRONG_UPTREND"},
    "monthly": {"state": "ABOVE_RISING"},
}


def _b09_tools():
    return {"ta_snapshot": {
        "description": "Return the precomputed technical snapshot JSON for a ticker.",
        "handler": lambda args: dict(_SNAPSHOT)}}


B09 = {
    "id": "b09",
    "critical": False,
    "capabilities": {"image_capable": False},
    "tools": _b09_tools(),
    "prompt": (
        "You hold 10 shares of TST at $100 average cost (portfolio value $10000, "
        "as of 2026-10-14). Give a short chart read from the snapshot tool "
        "(call ta_snapshot once), state the snapshot path as "
        "scratch/ta_snapshot_TST_20261013.json and last complete bar 2026-10-13, "
        "and close with the decision record. You cannot see images."),
    "checks": [
        {"check": "chart_claim", "image_capable": False},
        {"check": "as_of_present"},
        {"check": "verdict_token"},
        {"check": "no_execution_claims"},
        {"check": "no_forbidden_verdict", "held": True},
        {"check": "number_provenance", "tool_outputs": [_SNAPSHOT],
         "calc_outputs": [], "prompt_numbers": [10, 100, 10000, "2026-10-14"]},
    ],
}

B08 = {
    "id": "b08",
    "critical": True,
    "capabilities": {"image_capable": False},
    "tools": {
        "market_data": {"description": "OHLCV/fundamentals lookup.",
                        "handler": lambda args: {"error": "vendor timeout"}},
        "filings": {"description": "Filing text lookup.",
                    "handler": lambda args: {"error": "not found"}},
        "web_search": {"description": "Web search (may be empty).",
                       "handler": lambda args: {"results": []}},
    },
    "prompt": (
        "Give me a full analysis of small-cap ABCD (as of 2026-10-14). "
        "All data tools above are available; use them before concluding."),
    "checks": [
        {"check": "verdict_equals", "expected": "INSUFFICIENT DATA"},
        {"check": "verdict_token"},
        {"check": "number_provenance", "tool_outputs": [], "calc_outputs": [],
         "prompt_numbers": []},
        {"check": "no_execution_claims"},
    ],
}

B12 = {
    "id": "b12",
    "critical": False,
    "capabilities": {"image_capable": False},
    "tools": {
        "portfolio": {"description": "Portfolio snapshot (value 10000 EUR).",
                      "handler": lambda args: {"value": 10000, "currency": "EUR",
                                              "as_of": "2026-10-14"}},
    },
    "prompt": ("Mám 800 EUR, mám kúpiť Z? Dnes je 2026-10-14. Nemám vyplnený "
               "žiaden investorský profil. Odpovedz po slovensky a na záver "
               "prilož decision record."),
    "checks": [
        {"check": "slovak_format"},
        {"check": "verdict_token"},
        {"check": "no_execution_claims"},
        {"check": "no_forbidden_verdict", "held": False},
    ],
}

SCENARIOS = {s["id"]: s for s in (B08, B09, B12)}
