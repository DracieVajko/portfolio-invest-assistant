"""Benchmark portfolios - equal-weight buy-and-hold and periodic rebalance.

Uses exactly same normalized EUR price data, start equity, and cost config as strategy.
No future data inspection, deterministic missing-data handling.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from experimental.backtest.costs import CostsConfig, apply_costs, fill_price_with_slippage
from experimental.backtest.metrics import compute_metrics, metrics_to_dict


def _get_rebalance_dates(all_dates: List[pd.Timestamp], frequency: str) -> List[pd.Timestamp]:
    """Return sorted list of rebalance dates based on frequency.
    frequency: monthly, quarterly, none, weekly (weekly for tests)
    Monthly = last trading day of each month.
    Quarterly = last trading day of Mar, Jun, Sep, Dec.
    """
    if frequency in (None, "", "none"):
        return []
    freq = frequency.lower()
    # all_dates is sorted list of unique trading dates (UTC)
    if not all_dates:
        return []
    s = pd.Series(1, index=pd.to_datetime(all_dates, utc=True))
    if freq == "monthly":
        # Group by year-month, take max date per group
        df = pd.DataFrame({"date": s.index})
        df["ym"] = df["date"].dt.to_period("M")
        reb = df.groupby("ym")["date"].max().tolist()
        return sorted(reb)
    if freq == "quarterly":
        df = pd.DataFrame({"date": s.index})
        df["yq"] = df["date"].dt.to_period("Q")
        reb = df.groupby("yq")["date"].max().tolist()
        return sorted(reb)
    if freq == "weekly":
        # last trading day of each week (Friday)
        df = pd.DataFrame({"date": s.index})
        # ISO year-week
        df["yw"] = df["date"].dt.to_period("W")
        reb = df.groupby("yw")["date"].max().tolist()
        return sorted(reb)
    if freq == "daily":
        return sorted(s.index.tolist())
    raise ValueError(f"Unsupported rebalance frequency: {frequency}")


def _coverage_by_symbol(df: pd.DataFrame, universe: List[str] | None, min_bars: int) -> Tuple[List[str], List[dict], Dict[str, int]]:
    """Return eligible symbols, excluded list with reasons, and coverage counts.
    Uses df which already sorted and normalized.
    """
    if universe is None:
        universe = sorted(df["symbol"].unique().tolist())
    # Count valid bars per symbol: close_eur notna and close_eur >0
    counts = {}
    eligible = []
    excluded = []
    for sym in universe:
        sub = df[df["symbol"] == sym]
        valid = sub["close_eur"].notna() & (sub["close_eur"] > 0)
        cnt = int(valid.sum())
        counts[sym] = cnt
        if cnt < min_bars:
            excluded.append({"symbol": sym, "reason": f"insufficient_history {cnt} < {min_bars}", "bars": cnt})
        else:
            eligible.append(sym)
    # Also check symbols in df but not in universe? Not needed
    # Check for requested universe symbols with zero bars (completely missing)
    # Already handled as cnt==0 < min_bars
    return eligible, excluded, counts


def _build_price_matrix(df: pd.DataFrame, eligible: List[str], all_dates: List[pd.Timestamp]) -> Tuple[Dict[str, pd.Series], Dict[str, pd.Series]]:
    """Build lookup for close_eur and open_eur per symbol indexed by date, forward-filled for market value."""
    close_lookup = {}
    open_lookup = {}
    for sym in eligible:
        sub = df[df["symbol"] == sym].set_index("date").sort_index()
        # Ensure we have close_eur series
        close_s = sub["close_eur"]
        open_s = sub["open_eur"]
        # For missing dates, forward fill using last available close for equity calculation
        # But we keep original for trade execution (only trade on dates where price exists)
        close_lookup[sym] = close_s
        open_lookup[sym] = open_s
    return close_lookup, open_lookup


def equal_weight_buy_and_hold(
    df: pd.DataFrame,
    start_equity: float,
    costs: CostsConfig,
    universe: List[str] | None = None,
    min_history: int = 252,
) -> dict:
    """
    Equal-weight buy-and-hold with entry costs once.
    Uses first available date's open for entry (next-open convention: buy at open of first date).
    Returns dict with equity_curve, trades, costs_paid, coverage, weights.
    """
    all_dates = sorted(df["date"].unique())
    if not all_dates:
        raise ValueError("No dates available")
    eligible, excluded, coverage = _coverage_by_symbol(df, universe, min_history)
    if not eligible:
        # No eligible symbols, return flat equity with zero trades but report
        equity_curve = pd.Series([start_equity] * len(all_dates), index=pd.to_datetime(all_dates, utc=True))
        equity_curve.index.name = "date"
        return {
            "equity_curve": equity_curve,
            "trades": [],
            "fills": [],
            "costs_paid": 0.0,
            "turnover": 0.0,
            "eligible": eligible,
            "excluded": excluded,
            "coverage": coverage,
            "weights": {},
            "num_rebalances": 0,
            "rebalance_turnover": 0.0,
        }

    n = len(eligible)
    weight = 1.0 / n
    weights = {sym: weight for sym in eligible}
    # Verify sum
    assert abs(sum(weights.values()) - 1.0) < 1e-9

    # Entry at first date's open
    entry_date = all_dates[0]
    # For each symbol, get open price at entry_date
    close_lookup, open_lookup = _build_price_matrix(df, eligible, all_dates)
    # Check missing price at entry
    missing_at_entry = []
    qty_by_symbol = {}
    costs_paid = 0.0
    trade_value_total = 0.0
    fills = []
    trades = []
    cash = float(start_equity)
    # For B&H, we allocate equally
    for sym in eligible:
        open_s = open_lookup[sym]
        if entry_date not in open_s.index or pd.isna(open_s.loc[entry_date]):
            missing_at_entry.append(sym)
            continue
        open_price = float(open_s.loc[entry_date])
        # Apply slippage to fill price
        fill_price = fill_price_with_slippage(open_price, "BUY", costs)
        target_value = start_equity * weight
        # Compute quantity: floor(target_value / fill_price), but ensure min_ticket
        # Use target_value before costs; costs will be extra
        qty = math.floor(target_value / fill_price) if fill_price > 0 else 0
        if qty <= 0:
            # If price > target_value, try 1 share if affordable
            if target_value >= fill_price and fill_price * (1 + costs.commission_bps/10000 + costs.slippage_bps/10000) <= cash:
                qty = 1
            else:
                continue
        trade_value = qty * fill_price
        cost_info = apply_costs(trade_value, costs)
        total = trade_value + cost_info["total_cost"]
        if total > cash:
            # Reduce quantity to fit cash
            qty = math.floor(cash / (fill_price * (1 + costs.commission_bps/10000 + costs.slippage_bps/10000))) if fill_price>0 else 0
            if qty <= 0:
                continue
            trade_value = qty * fill_price
            cost_info = apply_costs(trade_value, costs)
            total = trade_value + cost_info["total_cost"]
        if trade_value < costs.min_ticket_eur:
            # Still allow if it's the only allocation? But respect min_ticket
            continue
        cash -= total
        costs_paid += cost_info["total_cost"]
        trade_value_total += trade_value
        qty_by_symbol[sym] = qty
        fills.append({"date": entry_date, "symbol": sym, "side": "BUY", "price": fill_price, "quantity": qty, "costs": cost_info})
        trades.append({"symbol": sym, "entry_date": entry_date, "quantity": qty, "entry_price": fill_price, "entry_value": trade_value, "costs": cost_info["total_cost"]})

    # If some symbols missing at entry, they are excluded for this run (but not counted as insufficient_history)
    # Report them as warnings

    # Build equity curve
    equity_vals = []
    cash_vals = []
    mv_vals = []
    for d in all_dates:
        mv = 0.0
        for sym, qty in qty_by_symbol.items():
            close_s = close_lookup[sym]
            if d in close_s.index and not pd.isna(close_s.loc[d]):
                close_price = float(close_s.loc[d])
                mv += qty * close_price
            else:
                # Carry forward last available close
                # Find last available before d
                prev_idx = close_s.index[close_s.index < d]
                if len(prev_idx) > 0:
                    last_close = float(close_s.loc[prev_idx.max()])
                    mv += qty * last_close
                else:
                    # No price yet, treat as 0
                    mv += 0.0
        equity = cash + mv
        equity_vals.append(equity)
        cash_vals.append(cash)
        mv_vals.append(mv)

    equity_curve = pd.Series(equity_vals, index=pd.to_datetime(all_dates, utc=True))
    equity_curve.index.name = "date"

    # Calculate turnover and metrics will be done by caller; here turnover is entry only
    # For B&H, turnover = total entry trade value / avg equity (or just entry)
    # We report costs_paid separately

    # Concentration: max weight
    # For B&H equal weight, concentration is 1/n

    return {
        "equity_curve": equity_curve,
        "trades": trades,
        "fills": fills,
        "costs_paid": float(costs_paid),
        "turnover": 0.0,  # B&H has no rebalance turnover
        "eligible": eligible,
        "excluded": excluded,
        "coverage": coverage,
        "weights": weights,
        "num_rebalances": 0,
        "rebalance_turnover": 0.0,
        "missing_at_entry": missing_at_entry,
        "cash_series": pd.Series(cash_vals, index=pd.to_datetime(all_dates, utc=True)),
        "mv_series": pd.Series(mv_vals, index=pd.to_datetime(all_dates, utc=True)),
    }


def periodic_equal_weight_rebalance(
    df: pd.DataFrame,
    start_equity: float,
    costs: CostsConfig,
    frequency: str = "monthly",
    universe: List[str] | None = None,
    min_history: int = 252,
) -> dict:
    """
    Periodic equal-weight rebalance. Entry as B&H, then rebalance at each period end.
    Applies costs to every rebalance trade.
    """
    all_dates = sorted(df["date"].unique())
    if not all_dates:
        raise ValueError("No dates")
    eligible, excluded, coverage = _coverage_by_symbol(df, universe, min_history)
    if not eligible:
        equity_curve = pd.Series([start_equity] * len(all_dates), index=pd.to_datetime(all_dates, utc=True))
        equity_curve.index.name = "date"
        return {
            "equity_curve": equity_curve,
            "trades": [],
            "fills": [],
            "costs_paid": 0.0,
            "turnover": 0.0,
            "eligible": eligible,
            "excluded": excluded,
            "coverage": coverage,
            "weights": {},
            "num_rebalances": 0,
            "rebalance_turnover": 0.0,
        }

    n = len(eligible)
    weight = 1.0 / n
    close_lookup, open_lookup = _build_price_matrix(df, eligible, all_dates)

    # Determine rebalance dates (excluding first date)
    rebalance_dates = _get_rebalance_dates(all_dates, frequency)
    # Exclude first date as it's entry, not rebalance
    if rebalance_dates and rebalance_dates[0] == all_dates[0]:
        rebalance_dates = rebalance_dates[1:]
    # Also ensure rebalance dates are within all_dates and not last date's next open issue
    # For execution, rebalance signal at close of rebalance_date, fill at next open
    # For simplicity here, we execute at next open after rebalance_date's close.
    # But for benchmark simplicity, we'll execute at close price of rebalance_date's next trading day's open?
    # To keep deterministic and not introduce next-open shift complexity for benchmark,
    # we will execute at close of rebalance_date's next date's open? Let's instead execute at close of rebalance_date using close price (simpler, still not future).
    # However to test correctly, we need turnover >0 and costs >0, which will happen either way.

    # We'll implement bar-by-bar similar to B&H but with rebalance at close.

    qty_by_symbol: Dict[str, float] = {}
    cash = float(start_equity)
    costs_paid = 0.0
    fills = []
    trades = []  # for benchmark, trades are rebalancing trades
    # Initial entry at first date open
    entry_date = all_dates[0]
    for sym in eligible:
        open_s = open_lookup[sym]
        if entry_date not in open_s.index or pd.isna(open_s.loc[entry_date]):
            continue
        open_price = float(open_s.loc[entry_date])
        fill_price = fill_price_with_slippage(open_price, "BUY", costs)
        target_value = start_equity * weight
        qty = math.floor(target_value / fill_price) if fill_price>0 else 0
        if qty <=0:
            continue
        trade_value = qty * fill_price
        cost_info = apply_costs(trade_value, costs)
        total = trade_value + cost_info["total_cost"]
        if total > cash:
            qty = math.floor(cash / (fill_price * (1 + costs.commission_bps/10000 + costs.slippage_bps/10000))) if fill_price>0 else 0
            if qty<=0:
                continue
            trade_value = qty*fill_price
            cost_info = apply_costs(trade_value, costs)
            total = trade_value + cost_info["total_cost"]
        if trade_value < costs.min_ticket_eur:
            continue
        cash -= total
        costs_paid += cost_info["total_cost"]
        qty_by_symbol[sym] = qty
        fills.append({"date": entry_date, "symbol": sym, "side": "BUY", "price": fill_price, "quantity": qty, "costs": cost_info})

    equity_vals = []
    mv_vals = []
    cash_vals = []
    rebalance_count = 0
    rebalance_turnover_sum = 0.0

    # For tracking turnover: sum absolute trade value of rebalances
    # For each date, compute equity before rebalance
    for idx, d in enumerate(all_dates):
        # Compute market value at close of d
        mv = 0.0
        for sym, qty in qty_by_symbol.items():
            close_s = close_lookup[sym]
            if d in close_s.index and not pd.isna(close_s.loc[d]):
                close_price = float(close_s.loc[d])
                mv += qty * close_price
            else:
                prev_idx = close_s.index[close_s.index < d]
                if len(prev_idx)>0:
                    last_close = float(close_s.loc[prev_idx.max()])
                    mv += qty * last_close
        equity = cash + mv
        equity_vals.append(equity)
        mv_vals.append(mv)
        cash_vals.append(cash)

        # Check if this date is a rebalance date (signal at close of d, execution at next open)
        # For simplicity, we will generate rebalance trades to be executed at next open's open price
        # So we need to look ahead to next date's open for execution, but we can simulate now with close
        # To avoid future inspection, we will schedule rebalance for next date's open
        # Implementation: if d is rebalance date and not last date, compute target and schedule for next date
        if d in rebalance_dates and idx + 1 < len(all_dates):
            next_date = all_dates[idx+1]
            # At close of d, equity is known (cash+mv at close). Target allocation = equity * weight
            # We need to compute trades at next open. For determinism, we will compute qty delta based on close prices but execute at next open.
            # This still uses only current close and next open (which is future by one bar, but it's the execution price, not decision)
            # This is similar to strategy's next-open execution and is allowed as long as decision uses only current data.
            rebalance_count += 1
            # Collect next open prices for execution
            for sym in eligible:
                # Current qty
                cur_qty = qty_by_symbol.get(sym, 0)
                # Current price at close of d for valuation, but execution at next open
                close_s = close_lookup[sym]
                open_s_next = open_lookup[sym]
                if next_date not in open_s_next.index or pd.isna(open_s_next.loc[next_date]):
                    # Missing price at execution, skip this symbol rebalance
                    continue
                next_open = float(open_s_next.loc[next_date])
                # Current market price for target calculation: use close at d
                if d in close_s.index and not pd.isna(close_s.loc[d]):
                    cur_price = float(close_s.loc[d])
                else:
                    # Use last available
                    prev_idx = close_s.index[close_s.index < d]
                    if len(prev_idx)==0:
                        continue
                    cur_price = float(close_s.loc[prev_idx.max()])
                # Target qty based on equity at close of d
                target_value = equity * weight
                # Execution price with slippage
                # Determine side based on qty delta
                # We need to compute target qty at execution price, not cur_price, to be precise: target_qty = target_value / execution_fill_price
                exec_price_buy = fill_price_with_slippage(next_open, "BUY", costs)
                exec_price_sell = fill_price_with_slippage(next_open, "SELL", costs)
                # Decide target qty using execution price (use buy price as reference for both to avoid asymmetry)
                target_qty = math.floor(target_value / exec_price_buy) if exec_price_buy>0 else 0
                delta_qty = target_qty - cur_qty
                if delta_qty == 0:
                    continue
                # Determine trade value and costs
                if delta_qty > 0:
                    # Buy
                    fill_price = exec_price_buy
                    trade_value = delta_qty * fill_price
                    cost_info = apply_costs(trade_value, costs)
                    total = trade_value + cost_info["total_cost"]
                    if total > cash:
                        # Scale down buy to available cash
                        max_qty = math.floor(cash / (fill_price * (1 + costs.commission_bps/10000 + costs.slippage_bps/10000))) if fill_price>0 else 0
                        if max_qty <=0:
                            continue
                        # Limit to needed delta
                        delta_qty = min(delta_qty, max_qty)
                        trade_value = delta_qty * fill_price
                        cost_info = apply_costs(trade_value, costs)
                        total = trade_value + cost_info["total_cost"]
                    if trade_value < costs.min_ticket_eur:
                        continue
                    cash -= total
                    costs_paid += cost_info["total_cost"]
                    rebalance_turnover_sum += trade_value
                    qty_by_symbol[sym] = cur_qty + delta_qty
                    fills.append({"date": next_date, "symbol": sym, "side": "BUY", "price": fill_price, "quantity": delta_qty, "costs": cost_info, "reason": "rebalance"})
                else:
                    # Sell
                    sell_qty = -delta_qty
                    fill_price = exec_price_sell
                    trade_value = sell_qty * fill_price
                    cost_info = apply_costs(trade_value, costs)
                    # For sells, proceeds net of costs
                    net = trade_value - cost_info["total_cost"]
                    if trade_value < costs.min_ticket_eur:
                        continue
                    cash += net
                    costs_paid += cost_info["total_cost"]
                    rebalance_turnover_sum += trade_value
                    qty_by_symbol[sym] = cur_qty - sell_qty
                    fills.append({"date": next_date, "symbol": sym, "side": "SELL", "price": fill_price, "quantity": sell_qty, "costs": cost_info, "reason": "rebalance"})
            # After rebalancing, next iteration's equity will reflect new qty, but we already recorded equity for d before rebalance
            # The cash and qty updates will affect next date's equity

    equity_curve = pd.Series(equity_vals, index=pd.to_datetime(all_dates, utc=True))
    equity_curve.index.name = "date"
    weights = {sym: weight for sym in eligible}

    return {
        "equity_curve": equity_curve,
        "trades": trades,  # empty for periodic, but fills contain rebalances
        "fills": fills,
        "costs_paid": float(costs_paid),
        "turnover": float(rebalance_turnover_sum),  # raw turnover sum, annualized later by metrics
        "eligible": eligible,
        "excluded": excluded,
        "coverage": coverage,
        "weights": weights,
        "num_rebalances": rebalance_count,
        "rebalance_turnover": float(rebalance_turnover_sum),
        "cash_series": pd.Series(cash_vals, index=pd.to_datetime(all_dates, utc=True)),
        "mv_series": pd.Series(mv_vals, index=pd.to_datetime(all_dates, utc=True)),
    }
