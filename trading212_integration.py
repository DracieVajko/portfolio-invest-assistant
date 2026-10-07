"""Compatibility shim (Phase 2): canonical home is trading212.integration.

Do not add code here. Import from trading212.integration instead.
Running this file as a script (used by run_t212_analysis.bat and
run_t212_export.bat) delegates to trading212.integration.__main__.
"""

from trading212.integration import *  # noqa: F401,F403
from trading212.integration import Trading212Integration, create_integration, OllamaClient  # noqa: F401

if __name__ == "__main__":
    import runpy as _runpy

    _runpy.run_module("trading212.integration", run_name="__main__")
