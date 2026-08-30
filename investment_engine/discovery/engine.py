from __future__ import annotations

from typing import Any, Dict, List


class DiscoveryEngine:
    """Generate discovery ideas from portfolio context rather than random assets."""

    def __init__(self, portfolio_context: Dict[str, Any] | None = None) -> None:
        self.portfolio_context = portfolio_context or {}

    def discover(self, assets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        symbols = {str(item.get("broker_symbol") or item.get("name") or "").upper() for item in assets}
        suggestions: List[Dict[str, Any]] = []
        mapping = {
            "NVDA": ["AVGO", "MRVL", "MU", "SKHYNIX", "TSM", "ASML", "VRT", "DELL", "AMD", "PLTR"],
            "AAPL": ["AVGO", "QCOM", "AMD", "MSFT", "NFLX", "INTC"],
            "MSFT": ["NVDA", "AMD", "AVGO", "CRM", "ORCL"],
            "TSLA": ["F", "RIVN", "LCID", "NIO"],
        }

        for symbol in sorted(symbols):
            for suggestion in mapping.get(symbol, []):
                if suggestion not in symbols:
                    suggestions.append({"broker_symbol": suggestion, "reason": f"ecosystem for {symbol}", "discovery_score": 0.8})

        return suggestions
