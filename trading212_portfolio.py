"""Compatibility shim (Phase 2): canonical home is trading212.portfolio.

Do not add code here. Import from trading212.portfolio instead.
Running this file as a script delegates to trading212.portfolio.__main__.
"""

from trading212.portfolio import *  # noqa: F401,F403
from trading212.portfolio import (  # noqa: F401
    PortfolioMonitor,
    parse_position,
    get_live_fx_rates,
    INSTRUMENT_CURRENCY_OVERRIDES,
    R1_DEPRECATED_ALIASES,
    compute_legacy_r1,
)

if __name__ == "__main__":
    import runpy as _runpy

    _runpy.run_module("trading212.portfolio", run_name="__main__")
