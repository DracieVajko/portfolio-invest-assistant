"""
Trading 212 Integration Package

Read-only portfolio monitoring for Trading 212.
"""
from .auth import Trading212Auth, Trade212Client, dump_raw_t212_diagnostics
from .portfolio import (
    PortfolioMonitor,
    parse_position,
    get_live_fx_rates,
    INSTRUMENT_CURRENCY_OVERRIDES,
    R1_DEPRECATED_ALIASES,
    compute_legacy_r1,
)
from .integration import Trading212Integration, create_integration, OllamaClient

__all__ = [
    "Trading212Auth",
    "Trade212Client",
    "dump_raw_t212_diagnostics",
    "PortfolioMonitor",
    "parse_position",
    "get_live_fx_rates",
    "INSTRUMENT_CURRENCY_OVERRIDES",
    "R1_DEPRECATED_ALIASES",
    "compute_legacy_r1",
    "Trading212Integration",
    "create_integration",
    "OllamaClient",
]