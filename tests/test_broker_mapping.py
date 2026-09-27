"""Broker-ID mapping: every held instrument resolves to a verified Yahoo symbol.

Offline (table-driven, no network). Live Yahoo existence was verified
2026-09-26 during the mapping audit; these tests pin the results.
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_tricky_broker_ids_resolve_exactly():
    from investment_engine.portfolio.symbols import to_yahoo_symbol as ty

    assert ty("ENRd_EQ") == "ENR.DE"  # never US Energizer (bare ENR)
    assert ty("LAR0D_EQ") == "LOM.DE"  # Xetra venue, EUR currency match
    assert ty("C7A0D_EQ") == "300750.SZ"
    assert ty("SUP_EQ") == "SU.PA"
    assert ty("AIP_EQ") == "AI.PA"
    assert ty("INLD_EQ") == "INTC"
    assert ty("NVDD_EQ") == "NVDA"
    assert ty("APCD_EQ") == "AAPL"
    assert ty("DJGTEEXD_EQ") == "EXI2.DE"
    assert ty("IQQHD_EQ") == "IQQH.DE"


def test_every_table_value_is_supported():
    from investment_engine.portfolio.symbols import (
        BROKER_YAHOO_OVERRIDES,
        support_state,
    )

    bad = {b: y for b, y in BROKER_YAHOO_OVERRIDES.items()
           if support_state(y, "market_data") != "SUPPORTED"}
    assert not bad, bad


def test_broker_stems_never_validate():
    from investment_engine.portfolio.symbols import UNRESOLVED_YAHOO, support_state

    assert {"NVD", "INLD", "APCD", "LAR0D", "ENR"} <= set(UNRESOLVED_YAHOO)
    for stem in ("NVD", "INLD", "APCD", "LAR0D", "ENR"):
        assert support_state(stem, "market_data") == "UNRESOLVED"


def test_config_aliases_point_at_live_symbols():
    """Stale config aliases must not resurrect dead symbols (live EGT case)."""
    import json

    from investment_engine.portfolio.symbols import support_state, to_yahoo_symbol

    cfg = json.loads((ROOT / "portfolio_config.json").read_text(encoding="utf-8"))
    aliases = cfg.get("symbol_aliases", {}) or {}
    assert aliases.get("EGTL") == "EGT.L"
    assert aliases.get("LITMM") == "LITM.L"
    assert "IBEE" not in aliases
    assert to_yahoo_symbol("EGTl_EQ", "EGT", aliases) == "EGT.L"
    assert support_state("EGT.L", "market_data") == "SUPPORTED"


def test_table_matches_config_assets_no_drift():
    """Config assets must not drift from the verified mapping layer.

    Two asset shapes exist: broker-ID holdings (T212 internal ids like
    AAPL_US_EQ) must match BROKER_YAHOO_OVERRIDES exactly; plain-ticker
    research-universe entries (e.g. WDC, SLDP) resolve natively and must
    be SUPPORTED. Either violation fails loudly.
    """
    from investment_engine.portfolio.symbols import (
        BROKER_YAHOO_OVERRIDES,
        support_state,
    )

    cfg = json.loads((ROOT / "portfolio_config.json").read_text(encoding="utf-8"))
    crypto = {"BTCUSD", "ETHUSD", "SOLUSDT", "KASUSDT", "SUIUSDT", "RNDRUSDT", "TAOUSDT"}
    mismatches = []
    unsupported = []
    for asset in cfg["assets"]:
        broker = str(asset.get("broker_symbol", "") or "").strip().upper()
        yahoo = str(asset.get("yahoo_symbol", "") or "").strip()
        if not broker or broker in crypto:
            continue
        if broker in BROKER_YAHOO_OVERRIDES:
            if BROKER_YAHOO_OVERRIDES[broker] != yahoo:
                mismatches.append((broker, yahoo, BROKER_YAHOO_OVERRIDES[broker]))
        elif support_state(yahoo, "market_data") != "SUPPORTED":
            unsupported.append((broker, yahoo))
    assert not mismatches, mismatches
    assert not unsupported, unsupported
    assert len(BROKER_YAHOO_OVERRIDES) == 96


# Frozen 2026-09-26 broker snapshot (96 equity IDs). If holdings change, extend
# BROKER_YAHOO_OVERRIDES first — this test fails loudly otherwise.
HELD_BROKER_IDS = frozenset(
    "1YDD_EQ AAPL_US_EQ ABT_US_EQ ADC_US_EQ ADP_US_EQ AFL_US_EQ AGNC_US_EQ "
    "AINFL_EQ AIP_EQ APCD_EQ APD_US_EQ BLK_US_EQ BMO_US_EQ BMY_US_EQ BNS_US_EQ "
    "C7A0D_EQ CAH_US_EQ CB_US_EQ CF_US_EQ CNQ_US_EQ COCOL_EQ COFFL_EQ COPGL_EQ "
    "CSCO_US_EQ CVX_US_EQ C_US_EQ DJGTEEXD_EQ DR4ML_EQ DRDRL_EQ DUK_US_EQ "
    "ECL_US_EQ EGTL_EQ EMR_US_EQ ENRD_EQ ERNXD_EQ ESIFL_EQ ETN_US_EQ FB2AD_EQ "
    "FWRGL_EQ GDGBL_EQ GD_US_EQ GEV_US_EQ GOOD_US_EQ GWW_US_EQ HTHIY_US_EQ "
    "IBEE_EQ IBM_US_EQ IISUL_EQ INLD_EQ IQQHD_EQ ITW_US_EQ IUVFL_EQ JEDGL_EQ "
    "JNJ_US_EQ JPM_US_EQ KMB_US_EQ KO_US_EQ LAR0D_EQ LIN_US_EQ LITMM_EQ "
    "LOW_US_EQ LTC_US_EQ MAIN_US_EQ MA_US_EQ MCD_US_EQ MSFT_US_EQ NATPL_EQ "
    "NCLRL_EQ NEE_US_EQ NUE_US_EQ NVDD_EQ O_US_EQ PEP_US_EQ PG_US_EQ PNR_US_EQ "
    "PPG_US_EQ QWTML_EQ RGLD_US_EQ ROP_US_EQ RWED_EQ RY_US_EQ SGLNL_EQ "
    "SHW_US_EQ SILGL_EQ SLB_US_EQ SSLNL_EQ SUP_EQ SYY_US_EQ TD_US_EQ "
    "TROW_US_EQ TTWO_US_EQ TUYA_US_EQ UEC_US_EQ VWSBD_EQ WBIOL_EQ WMT_US_EQ".split()
)


def test_all_held_ids_covered():
    from investment_engine.portfolio.symbols import BROKER_YAHOO_OVERRIDES

    assert len(HELD_BROKER_IDS) == 96
    assert HELD_BROKER_IDS <= set(BROKER_YAHOO_OVERRIDES), \
        sorted(HELD_BROKER_IDS - set(BROKER_YAHOO_OVERRIDES))
