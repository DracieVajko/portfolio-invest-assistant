"""Phase 2 T212 consolidation: package is canonical, shims transparent, dead removed.

Offline only. No live API.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_package_is_canonical_home():
    import trading212.integration as pi
    import trading212.auth as pa
    import trading212.portfolio as pp

    assert pi.Trading212Integration.__module__ == "trading212.integration"
    assert pi.create_integration.__module__ == "trading212.integration"
    assert pa.Trade212Client.__module__ == "trading212.auth"
    assert pp.PortfolioMonitor.__module__ == "trading212.portfolio"
    assert pp.parse_position.__module__ == "trading212.portfolio"
    assert pp.get_live_fx_rates.__module__ == "trading212.portfolio"


def test_root_shims_reexport_identical_objects():
    import trading212_auth as ra
    import trading212.auth as pa
    import trading212_integration as ri
    import trading212.integration as pi
    import trading212_portfolio as rp
    import trading212.portfolio as pp

    assert ra.Trade212Client is pa.Trade212Client
    assert ra.dump_raw_t212_diagnostics is pa.dump_raw_t212_diagnostics
    assert ri.Trading212Integration is pi.Trading212Integration
    assert ri.create_integration is pi.create_integration
    assert rp.PortfolioMonitor is pp.PortfolioMonitor
    assert rp.parse_position is pp.parse_position
    assert rp.get_live_fx_rates is pp.get_live_fx_rates
    assert rp.INSTRUMENT_CURRENCY_OVERRIDES is pp.INSTRUMENT_CURRENCY_OVERRIDES


def test_engine_never_imports_root_modules():
    for rel in ("investment_engine/main.py", "investment_engine/portfolio/broker_first.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "from trading212_auth import" not in src, rel
        assert "from trading212_portfolio import" not in src, rel
        assert "from trading212_integration import" not in src, rel
        assert "import trading212_auth" not in src, rel
        assert "import trading212_portfolio" not in src, rel
        assert "import trading212_integration" not in src, rel


def test_dead_placeholder_removed():
    assert not (ROOT / "investment_engine" / "trading212_integration.py").exists()
    for name in (
        "investment_engine/portfolio/broker_first_V2415_Sep-24-1106-2026_1.py",
        "investment_engine/reporting/regime_report_V2415_Sep-24-1106-2026_1.py",
        "investment_engine/research/news_engine_V2415_Sep-24-1106-2026_1.py",
        "investment_engine/reporting/failed_tickers_V2415_Sep-24-1106-2026_1.py",
    ):
        assert not (ROOT / name).exists(), name


def test_run_engine_path_never_touches_legacy_ai_bridge():
    src = (ROOT / "investment_engine" / "main.py").read_text(encoding="utf-8")
    assert "OllamaClient" not in src
    assert "analyze_portfolio" not in src


def test_tools_cli_importable():
    from tools.t212_cli import create_integration, main

    assert callable(create_integration)
    assert callable(main)
