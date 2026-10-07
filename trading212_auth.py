"""Compatibility shim (Phase 2): canonical home is trading212.auth.

Do not add code here. Import from trading212.auth instead.
Running this file as a script delegates to trading212.auth.__main__.
"""

from trading212.auth import *  # noqa: F401,F403
from trading212.auth import Trading212Auth, Trade212Client, dump_raw_t212_diagnostics  # noqa: F401

if __name__ == "__main__":
    import runpy as _runpy

    _runpy.run_module("trading212.auth", run_name="__main__")
