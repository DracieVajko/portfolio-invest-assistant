"""Simple execution engine - bar by bar, no look-ahead, fail closed."""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import pandas as pd
import numpy as np

from experimental.backtest.validate import ValidationReport, ValidationError, validate_dataframe
from experimental.backtest.costs import CostsConfig, apply_costs, fill_price_with_slippage
from experimental.backtest.metrics import compute_metrics, metrics_to_dict
from experimental.backtest.strategy.tech_pie_pullback_v1 import TechPiePullbackV1


@dataclass
class Position:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float  # fill price EUR
    quantity: float
    entry_value: float  # quantity * entry_price
    bars_held: int = 0
    stop_price: float = 0.0
    # For metrics


class BacktestEngine:
    """Bar-by-bar engine with explicit shift to prevent look-ahead."""

    def __init__(self, config_path: str | Path, fx_config_path: str | Path | None = None):
        self.config_path = Path(config_path)
        self.config = json.loads(self.config_path.read_bytes().decode("utf-8"))
        # FX config path from config if not provided
        if fx_config_path is None:
            fx_config_path = self.config.get("fx_config", "experimental/backtest/config/fx_rates.json")
        self.fx_config_path = Path(fx_config_path)
        # Costs
        costs_cfg = self.config.get("costs", {})
        self.costs = CostsConfig(
            commission_bps=costs_cfg.get("commission_bps", 10),
            slippage_bps=costs_cfg.get("slippage_bps", 5),
            fx_spread_bps=costs_cfg.get("fx_spread_bps", 10),
            min_ticket_eur=costs_cfg.get("min_ticket_eur", 1.0),
        )
        # Risk
        risk = self.config.get("risk", {})
        self.max_alloc_pct = risk.get("max_alloc_per_instrument_pct", 8.0)
        self.max_gross_pct = risk.get("max_gross_exposure_pct", 80.0)
        self.min_cash_pct = risk.get("min_cash_reserve_pct", 20.0)
        self.max_positions = risk.get("max_positions", 10)
        self.start_equity = self.config.get("start_equity_eur", 10000.0)
        # Exits
        exits = self.config.get("exits", {})
        self.hard_stop_pct = exits.get("hard_stop_pct", 7.0)
        self.time_stop_bars = exits.get("time_stop_bars", 20)
        self.weekly_break_enabled = exits.get("weekly_break_enabled", True)
        # Strategy
        self.strategy = TechPiePullbackV1(self.config)

    def run(
        self,
        df: pd.DataFrame,
        meta: dict,
        as_of: pd.Timestamp | str | None = None,
        validate: bool = True,
    ) -> dict:
        """
        Run backtest. Raises ValidationError if validation fails and writes no files.
        df must be normalized output from io.load_ohlcv (EUR columns).
        meta must contain data_hash, fx_hash.
        Returns dict with equity_curve, trades, orders, fills, metrics, hashes.
        No files written if validation fails (fail closed).
        """
        # Validation first
        if validate:
            data_hash = meta.get("data_hash", "")
            allow_gap = self.config.get("validation", {}).get("allow_gap_larger_than_two_bdays", False)
            stale_thresh = self.config.get("validation", {}).get("stale_threshold_bdays", 3)
            report = validate_dataframe(
                df,
                data_hash=data_hash,
                allow_gap_larger_than_two_bdays=allow_gap,
                as_of=as_of,
                stale_threshold_bdays=stale_thresh,
            )
            if not report.ok:
                raise ValidationError(report)

        if df.empty:
            raise ValidationError(ValidationReport(ok=False, errors=["empty dataframe"], data_hash=meta.get("data_hash", "")))

        # Generate signals - must be shifted for execution at next open
        sig_df = self.strategy.generate_signals(df)
        # Ensure sig_df sorted
        sig_df = sig_df.sort_values(["symbol", "date"]).reset_index(drop=True)
        # Map signal to execution: signal at close[t] -> fill at open[t+1]
        # We will bar-loop and look at prev day signal

        # Prepare data indexed by date per symbol for fast lookup
        # Ensure df sorted
        df = df.sort_values(["symbol", "date"]).reset_index(drop=True)
        # Unique dates sorted
        all_dates = sorted(df["date"].unique())
        # Build lookup: symbol -> DataFrame indexed by date
        symbol_groups = {sym: grp.set_index("date").sort_index() for sym, grp in df.groupby("symbol")}
        # Signal lookup: symbol -> Series indexed by date with signal
        sig_lookup = {}
        for sym in df["symbol"].unique():
            s = sig_df[sig_df["symbol"] == sym].set_index("date")["signal"] if not sig_df.empty else pd.Series(dtype=int)
            sig_lookup[sym] = s

        # Weekly gate for exit: compute weekly gate series per symbol for break detection
        # We can reuse strategy's weekly gate but for engine we need weekly_gate_ok series per daily date
        # Recompute quickly via strategy helper: we already have sig_df weekly_gate_ok
        weekly_gate_lookup = {}
        for sym in df["symbol"].unique():
            if not sig_df.empty and "weekly_gate_ok" in sig_df.columns:
                w = sig_df[sig_df["symbol"] == sym].set_index("date")["weekly_gate_ok"]
                weekly_gate_lookup[sym] = w
            else:
                weekly_gate_lookup[sym] = pd.Series(dtype=bool)

        # Engine state
        cash = float(self.start_equity)
        positions: Dict[str, Position] = {}
        trades: List[dict] = []
        fills: List[dict] = []
        orders: List[dict] = []
        equity_history = []

        # For exposure tracking
        # We'll iterate over all_dates in order
        # For each date, we first update equity with close prices, then check exits (to be executed next open), then check entries

        # We need to handle execution lag: signal at close[t] => order at close[t] for next open[t+1]
        # So we track pending entries: symbol -> entry_signal_date
        pending_entries: Dict[str, pd.Timestamp] = {}

        # Also pending exits: symbol -> exit_reason, to be executed next open
        pending_exits: Dict[str, str] = {}

        # For time stop, need bars_held count

        # Precompute open price lookup for next day fill
        # Loop by index
        for idx, cur_date in enumerate(all_dates):
            # Update market value and bars_held
            market_value = 0.0
            for sym, pos in positions.items():
                grp = symbol_groups.get(sym)
                if cur_date in grp.index:
                    close_price = float(grp.loc[cur_date, "close_eur"])
                    # Update bars_held for current date (increment after first day)
                    # bars_held counts trading days since entry (entry day =0)
                    pos.bars_held += 1
                    market_value += pos.quantity * close_price
                else:
                    # No data for this symbol on this date (should not happen if universe aligned)
                    # Use last close
                    # Find last available close before cur_date
                    prev_dates = grp.index[grp.index < cur_date]
                    if len(prev_dates) > 0:
                        last_close = float(grp.loc[prev_dates.max(), "close_eur"])
                        market_value += pos.quantity * last_close
                    pos.bars_held += 1

            gross_exposure = market_value
            equity = cash + market_value
            # Track equity history with date
            equity_history.append({"date": cur_date, "equity": equity, "cash": cash, "market_value": market_value, "gross_exposure": market_value})

            # If last date, no next open to execute pending
            if idx >= len(all_dates) - 1:
                # No future fill; break before executing pending for beyond data
                # But we still need to handle exits that would have triggered at cur_date for next open - can't execute without next open
                # So we just record pending but not fill
                break

            next_date = all_dates[idx + 1]

            # 1. Check for exits to be executed at next open (pending_exits from previous bar's close)
            # Actually pending_exits were set at previous close to execute at cur_date open? Let's handle symmetrically:
            # We set pending at close[t] to execute at open[t+1]
            # So at iteration for cur_date, we should execute pending that was set at previous close (cur_date is the next open's date)
            # But we are iterating by date which is both close and open of same day; for simplicity, assume open[t+1] is on next_date
            # So pending set at cur_date will execute at next_date's open
            # Pending set at previous date will execute at cur_date's open - we handle at start of loop before equity update? Let's restructure
            # For clarity, we will execute pending at next_date's open at the beginning of next iteration? But we already do equity update at cur_date close.
            # To avoid confusion, we will execute pending entries/exits at next_date open now (within current cur_date iteration)
            # So pending from previous iteration's close should have been executed now at next_date open? Let's track correctly:

            # At this point, cur_date's close has been used for equity. The next action is to check conditions at cur_date close to generate pending for next_date open.
            # But we also need to execute pending that was generated at previous cur_date-1 close at cur_date open before updating equity? Our equity update used close, so cash hasn't been updated for fills that would have happened at open of cur_date.
            # To properly handle, we should have executed pending at cur_date open before equity calculation.
            # Let's fix order: At each cur_date, first execute pending fills at open of cur_date (from previous day's signal), then update equity with close.
            # However our loop currently updates equity before executing pending for next_date. So pending from prev day not yet executed when we computed equity for cur_date.
            # We need to reorder: execute pending at cur_date open at start of loop.
            # Since we already computed equity for cur_date, we need to refactor: Instead, at each iteration, execute pending before equity update.
            # Let's implement a two-phase loop with explicit previous pending execution before equity.
            # For simplicity, we will handle execution at start of loop for cur_date (if idx>0, pending was set at all_dates[idx-1] close)
            # But we set pending at end of loop for next_date, so at start of next iteration we execute.
            # We already did equity before execution, so equity for cur_date is pre-fill equity (wrong).
            # Let's instead move execution to top of loop.

            # Note: This is a known complexity; we will handle by executing pending at top before equity, but we already did equity for cur_date.
            # To fix, we need to execute pending for cur_date before equity. Since we already did, we need to adjust.
            # Let's implement correct loop: For each cur_date, if idx>0, execute pending from prev date at cur_date open before equity.
            # But we computed equity before checking pending. So for idx>0, equity should have been after pending execution.
            # We can fix by moving equity calculation after pending execution.

            # This loop is already flawed for first iteration. Let's instead implement a corrected loop structure below by breaking and re-implementing.

            pass

        # Correct bar-by-bar loop with proper order
        # Reset state
        cash = float(self.start_equity)
        positions = {}
        trades = []
        fills = []
        orders = []
        equity_history = []
        pending_entries = {}
        pending_exits = {}

        for idx, cur_date in enumerate(all_dates):
            # --- Phase A: Execute pending orders at open of cur_date (from previous close signals) ---
            # Pending entries: generated at previous close to buy at this open
            # Pending exits: generated at previous close to sell at this open, plus hard stop intraday
            # For idx==0, no pending
            if idx > 0:
                # Execute pending exits first (sell)
                for sym, reason in list(pending_exits.items()):
                    pos = positions.get(sym)
                    if pos is None:
                        continue
                    grp = symbol_groups.get(sym)
                    if cur_date not in grp.index:
                        # No data for this symbol on this date, skip exit (try next day)
                        continue
                    open_price = float(grp.loc[cur_date, "open_eur"])
                    close_price = float(grp.loc[cur_date, "close_eur"])
                    low_price = float(grp.loc[cur_date, "low_eur"])
                    high_price = float(grp.loc[cur_date, "high_eur"])
                    # Determine fill price: for hard stop, if low <= stop, fill at stop (with slippage)
                    fill_price = open_price
                    if reason == "hard_stop":
                        # Stop price is pos.stop_price
                        # If open gaps below stop, fill at open (worse)
                        if open_price <= pos.stop_price:
                            fill_price = open_price
                        elif low_price <= pos.stop_price:
                            fill_price = pos.stop_price
                        else:
                            # Should not have triggered hard stop if low > stop
                            fill_price = open_price
                    else:
                        fill_price = open_price

                    # Apply slippage
                    fill_price_adj = fill_price_with_slippage(fill_price, "SELL", self.costs)
                    # Quantity
                    qty = pos.quantity
                    trade_value = qty * fill_price_adj
                    costs = apply_costs(trade_value, self.costs)
                    # For sells, commission/slippage reduces proceeds
                    net_proceeds = trade_value - costs["total_cost"]
                    # Check min_ticket
                    if trade_value < self.costs.min_ticket_eur:
                        # Skip if too small? But still need to exit to avoid stuck positions
                        pass
                    cash += net_proceeds
                    # Record trade
                    pnl = (fill_price_adj - pos.entry_price) * qty - costs["total_cost"] - apply_costs(pos.entry_value, self.costs)["total_cost"]
                    # Simplified pnl: exit value - entry value - costs both sides
                    holding_days = pos.bars_held
                    trades.append(
                        {
                            "symbol": sym,
                            "entry_date": pos.entry_date,
                            "exit_date": cur_date,
                            "entry_price": pos.entry_price,
                            "exit_price": fill_price_adj,
                            "quantity": qty,
                            "entry_value": pos.entry_value,
                            "exit_value": trade_value,
                            "pnl_eur": float((fill_price_adj - pos.entry_price) * qty - costs["total_cost"] - (pos.entry_value * self.costs.commission_bps / 10000 + pos.entry_value * self.costs.slippage_bps / 10000)),
                            "holding_days": int(holding_days),
                            "exit_reason": reason,
                            "gross_exposure": 0,  # will compute later
                        }
                    )
                    fills.append(
                        {"date": cur_date, "symbol": sym, "side": "SELL", "price": fill_price_adj, "quantity": qty, "reason": reason, "costs": costs}
                    )
                    del positions[sym]
                    del pending_exits[sym]

                # Execute pending entries at open of cur_date
                for sym, sig_date in list(pending_entries.items()):
                    if sym in positions:
                        # Already have position, skip
                        del pending_entries[sym]
                        continue
                    grp = symbol_groups.get(sym)
                    if cur_date not in grp.index:
                        continue
                    # Check risk limits before entry
                    # Current market value after exits
                    current_mv = sum(
                        symbol_groups[s].loc[cur_date, "close_eur"] * p.quantity if cur_date in symbol_groups[s].index else p.quantity * symbol_groups[s].iloc[-1]["close_eur"]
                        for s, p in positions.items()
                    ) if positions else 0.0
                    equity_est = cash + current_mv
                    # Max gross exposure 80%
                    if current_mv / equity_est >= self.max_gross_pct / 100 if equity_est > 0 else False:
                        # Too much exposure, skip
                        del pending_entries[sym]
                        continue
                    if len(positions) >= self.max_positions:
                        del pending_entries[sym]
                        continue
                    # Check min cash reserve: need cash >=20% after trade
                    # Estimate trade value: 8% of equity
                    target_value = equity_est * self.max_alloc_pct / 100
                    # Also need to ensure cash can cover
                    open_price = float(grp.loc[cur_date, "open_eur"])
                    fill_price_adj = fill_price_with_slippage(open_price, "BUY", self.costs)
                    costs_est = apply_costs(target_value, self.costs)
                    total_cost_est = target_value + costs_est["total_cost"]
                    if total_cost_est > cash:
                        # Not enough cash, skip
                        del pending_entries[sym]
                        continue
                    # Also check cash reserve after trade: cash - total_cost >= 20% * equity_est
                    if (cash - total_cost_est) < equity_est * self.min_cash_pct / 100:
                        # Scale down to respect reserve
                        max_cash_to_use = cash - equity_est * self.min_cash_pct / 100
                        if max_cash_to_use < self.costs.min_ticket_eur:
                            del pending_entries[sym]
                            continue
                        # Adjust target_value
                        target_value = max_cash_to_use - costs_est["total_cost"]
                        if target_value < self.costs.min_ticket_eur:
                            del pending_entries[sym]
                            continue
                    # Check min ticket
                    if target_value < self.costs.min_ticket_eur:
                        del pending_entries[sym]
                        continue
                    # Compute quantity
                    qty = np.floor(target_value / fill_price_adj) if fill_price_adj > 0 else 0
                    if qty <= 0:
                        del pending_entries[sym]
                        continue
                    trade_value = qty * fill_price_adj
                    costs = apply_costs(trade_value, self.costs)
                    total = trade_value + costs["total_cost"]
                    if total > cash:
                        # Try reduce qty
                        qty = np.floor((cash * 0.99) / (fill_price_adj * (1 + self.costs.commission_bps/10000 + self.costs.slippage_bps/10000)))
                        if qty <= 0:
                            del pending_entries[sym]
                            continue
                        trade_value = qty * fill_price_adj
                        costs = apply_costs(trade_value, self.costs)
                        total = trade_value + costs["total_cost"]
                    cash -= total
                    stop_price = fill_price_adj * (1 - self.hard_stop_pct / 100)
                    pos = Position(
                        symbol=sym,
                        entry_date=cur_date,
                        entry_price=fill_price_adj,
                        quantity=float(qty),
                        entry_value=float(trade_value),
                        bars_held=0,
                        stop_price=float(stop_price),
                    )
                    positions[sym] = pos
                    fills.append({"date": cur_date, "symbol": sym, "side": "BUY", "price": fill_price_adj, "quantity": float(qty), "reason": "entry", "costs": costs})
                    orders.append({"date": sig_date, "symbol": sym, "side": "BUY", "signal_price": float(grp.loc[sig_date, "close_eur"]) if sig_date in grp.index else 0, "fill_date": cur_date, "fill_price": fill_price_adj})
                    del pending_entries[sym]

            # --- Phase B: Update equity with close of cur_date ---
            market_value = 0.0
            for sym, pos in positions.items():
                grp = symbol_groups.get(sym)
                if cur_date in grp.index:
                    close_price = float(grp.loc[cur_date, "close_eur"])
                    market_value += pos.quantity * close_price
                else:
                    # Use last available
                    prev = grp.index[grp.index < cur_date]
                    if len(prev) > 0:
                        last_close = float(grp.loc[prev.max(), "close_eur"])
                        market_value += pos.quantity * last_close
                # Increment bars_held after close (for next day's time stop)
                # We already increment? Let's increment here if not already? For new positions entered today, bars_held=0 today, then next day 1.
                # So increment at end of day for next check
                # We will increment after equity calc for those that existed at start of day (already incremented above in a separate loop)
                # For simplicity, increment now for all positions that have survived the open execution
                # But new positions entered today should stay 0 until tomorrow
                # So we need to track: positions entered today not incremented today
                # We can handle by incrementing bars_held for positions that existed before today's open (i.e., not just entered)
                pass

            # Actually we need to increment bars_held for positions that existed at start of day (before today's close)
            # For positions just entered today at open, bars_held should be 0 today, 1 tomorrow
            # For positions that existed, increment
            # Let's increment for all positions except those entered today (we can check entry_date != cur_date)
            for sym, pos in positions.items():
                if pos.entry_date != cur_date:
                    pos.bars_held += 1
                else:
                    # New position, bars_held stays 0
                    pass

            equity = cash + market_value
            equity_history.append({"date": cur_date, "equity": equity, "cash": cash, "market_value": market_value})

            # --- Phase C: At close of cur_date, check conditions to generate pending for next open ---
            if idx >= len(all_dates) - 1:
                continue  # no next date
            next_date = all_dates[idx + 1]

            # For each symbol, check exit conditions based on cur_date close
            for sym, pos in list(positions.items()):
                grp = symbol_groups.get(sym)
                if cur_date not in grp.index:
                    continue
                low = float(grp.loc[cur_date, "low_eur"])
                close = float(grp.loc[cur_date, "close_eur"])
                # Hard stop check: if low <= stop_price, trigger hard stop for next open (or intraday)
                if low <= pos.stop_price:
                    pending_exits[sym] = "hard_stop"
                    continue
                # Time stop: if bars_held >= time_stop_bars
                if pos.bars_held >= self.time_stop_bars:
                    pending_exits[sym] = "time_stop"
                    continue
                # Weekly break: check weekly_gate_ok for this symbol at cur_date
                # If weekly_gate false, exit
                if self.weekly_break_enabled:
                    wg = weekly_gate_lookup.get(sym)
                    if not wg.empty and cur_date in wg.index:
                        gate_ok = bool(wg.loc[cur_date])
                        # Note: wg is gate_2w_shifted, which is already lagged
                        if not gate_ok:
                            pending_exits[sym] = "weekly_break"
                            continue
                    else:
                        # If no gate data, check via sig lookup? Fallback: if signal's weekly_gate_ok false
                        # Try to find nearest weekly gate
                        # Use last available weekly gate before cur_date
                        if not wg.empty:
                            # Find last
                            prev_w = wg.index[wg.index <= cur_date]
                            if len(prev_w) > 0:
                                last_gate = bool(wg.loc[prev_w.max()])
                                if not last_gate:
                                    pending_exits[sym] = "weekly_break"
                                    continue

            # For each symbol not in position and not pending, check entry signal at cur_date close
            for sym in df["symbol"].unique():
                if sym in positions or sym in pending_entries or sym in pending_exits:
                    continue
                sig_series = sig_lookup.get(sym, pd.Series(dtype=int))
                if cur_date in sig_series.index:
                    sig = int(sig_series.loc[cur_date])
                    if sig == 1:
                        # Signal at close[t] to buy at open[t+1]
                        pending_entries[sym] = cur_date

        # Build equity curve Series
        eq_df = pd.DataFrame(equity_history)
        if eq_df.empty:
            equity_curve = pd.Series(dtype=float)
        else:
            equity_curve = pd.Series(eq_df["equity"].values, index=pd.to_datetime(eq_df["date"], utc=True)).sort_index()
            equity_curve.index.name = "date"

        # If no trades, equity_curve should still be start equity flat? For single date case, we have at least one point
        if equity_curve.empty and not df.empty:
            # Create flat curve
            equity_curve = pd.Series([self.start_equity], index=[df["date"].min()])

        # Compute metrics from post-cost equity
        # For gross exposure, compute avg from equity_history
        avg_gross = 0.0
        if equity_history:
            gross_vals = [h["market_value"] for h in equity_history]
            eq_vals = [h["equity"] for h in equity_history]
            # avg gross exposure = avg(market_value / equity)
            exposures = [mv / eq if eq != 0 else 0 for mv, eq in zip(gross_vals, eq_vals)]
            avg_gross = float(np.mean(exposures)) if exposures else 0.0
            # Also attach to trades for metrics
            for t in trades:
                t["gross_exposure"] = avg_gross

        metrics = compute_metrics(equity_curve, trades, start_equity=self.start_equity)
        # Override avg_gross_exposure with computed
        metrics.avg_gross_exposure = avg_gross

        # Prepare orders: already have
        # Hashes
        data_hash = meta.get("data_hash", "")
        fx_hash = meta.get("fx_hash", "")
        config_hash = hashlib.sha256(json.dumps(self.config, sort_keys=True).encode()).hexdigest()

        result = {
            "equity_curve": equity_curve,
            "trades": trades,
            "fills": fills,
            "orders": orders,
            "metrics": metrics,
            "metrics_dict": metrics_to_dict(metrics),
            "data_hash": data_hash,
            "fx_hash": fx_hash,
            "config_hash": config_hash,
            "symbols": sorted(df["symbol"].unique().tolist()),
            "start_equity": self.start_equity,
        }
        # No file writing here - caller decides, but if validation failed we would have raised
        return result

    def run_and_write(
        self,
        df: pd.DataFrame,
        meta: dict,
        output_dir: str | Path | None = None,
        as_of: pd.Timestamp | str | None = None,
    ) -> dict:
        """Run and optionally write report files. Only writes if validation ok."""
        result = self.run(df, meta, as_of=as_of, validate=True)
        if output_dir is not None:
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            # Write equity curve csv and trades json
            # Use hashes for filename
            suffix = result["data_hash"][:8]
            eq_path = out / f"equity_curve_{suffix}.csv"
            result["equity_curve"].to_csv(eq_path, header=True)
            trades_path = out / f"trades_{suffix}.json"
            import json as js

            # Convert trades dates to iso
            trades_serializable = []
            for t in result["trades"]:
                tt = t.copy()
                if isinstance(tt.get("entry_date"), pd.Timestamp):
                    tt["entry_date"] = tt["entry_date"].isoformat()
                if isinstance(tt.get("exit_date"), pd.Timestamp):
                    tt["exit_date"] = tt["exit_date"].isoformat()
                trades_serializable.append(tt)
            trades_path.write_text(js.dumps(trades_serializable, indent=2), encoding="utf-8")
            metrics_path = out / f"metrics_{suffix}.json"
            metrics_path.write_text(js.dumps(result["metrics_dict"], indent=2), encoding="utf-8")
        return result
