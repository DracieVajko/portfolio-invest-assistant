"""Transaction costs - explicit, configurable, post-cost EUR."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostsConfig:
    commission_bps: float = 10.0  # 0.10%
    slippage_bps: float = 5.0  # 0.05%
    fx_spread_bps: float = 10.0  # 0.10% on FX legs (applied if currency != EUR, but we already in EUR)
    min_ticket_eur: float = 1.0


def apply_costs(trade_value_eur: float, costs: CostsConfig) -> dict:
    """
    Compute total cost for a trade value (EUR).
    Returns dict with commission, slippage, fx_spread, total_cost, net_value.

    trade_value_eur: absolute notional (quantity * price_eur)
    """
    commission = trade_value_eur * costs.commission_bps / 10000.0
    slippage = trade_value_eur * costs.slippage_bps / 10000.0
    # FX spread only relevant for non-EUR originally, but we apply to all EUR notional
    # as a conservative spread; configurable to 0 if needed.
    fx_spread = trade_value_eur * costs.fx_spread_bps / 10000.0
    # For Phase 1, we include commission + slippage in primary cost; fx_spread reported separately
    # Engine will use commission+slippage for cash deduction; fx_spread informational.
    total = commission + slippage
    # If you want to include fx_spread in total, add it here. We expose both.
    total_with_fx = total + fx_spread
    return {
        "commission": commission,
        "slippage": slippage,
        "fx_spread": fx_spread,
        "total_cost": total,
        "total_with_fx": total_with_fx,
        "net_value": trade_value_eur + total if False else trade_value_eur,  # net not used
    }


def fill_price_with_slippage(price_eur: float, side: str, costs: CostsConfig) -> float:
    """Adjust fill price for slippage: buy pays more, sell receives less."""
    if side.upper() == "BUY":
        return price_eur * (1 + costs.slippage_bps / 10000.0)
    else:
        return price_eur * (1 - costs.slippage_bps / 10000.0)
