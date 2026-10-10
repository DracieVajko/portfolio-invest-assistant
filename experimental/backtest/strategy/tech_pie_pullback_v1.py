"""tech_pie_pullback_v1 - Phase 1

Weekly trend gate + daily pullback, long-only, advisory only.
No LLM, no intraday, no shorting, no leverage.
Exits Phase 1: hard stop 7%, weekly break, time stop 20d.
"""
from __future__ import annotations

import pandas as pd
import numpy as np

from experimental.backtest.indicators import add_daily_indicators, add_weekly_indicators
from experimental.backtest.strategy.base import StrategyBase


def _business_days_diff(d1: pd.Timestamp, d2: pd.Timestamp) -> int:
    """Business days from d1 to d2 inclusive? For earnings blackout we count trading days.
    If d1=earnings_date, d2=query_date, we compute bdays between.
    Simple: use bdate_range.
    """
    if pd.isna(d1) or pd.isna(d2):
        return 999
    if d1.tzinfo is None:
        d1 = d1.tz_localize("UTC")
    if d2.tzinfo is None:
        d2 = d2.tz_localize("UTC")
    # Count business days between
    # If query is before earnings: days until earnings
    # Use date normalized
    start = min(d1, d2).normalize()
    end = max(d1, d2).normalize()
    if start == end:
        return 0
    rng = pd.bdate_range(start, end, freq="B")
    # If d1 < d2, rng includes both endpoints if they are business days
    # Number of bdays between exclusive of one endpoint
    return len(rng) - 1


class TechPiePullbackV1(StrategyBase):
    """Implement config-driven rules exactly as spec."""

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Input df must have: date (UTC), symbol, open_eur, high_eur, low_eur, close_eur, volume, earnings_date
        Sorted by symbol, date.

        Output: DataFrame with columns date, symbol, signal (0/1), entry_reason, weekly_gate, daily_conditions
        Signal is generated at close[t] for execution at open[t+1].
        No future access; all indicators use only data <= t.
        """
        cfg = self.config
        weekly_cfg = cfg.get("weekly_trend_gate", {})
        daily_cfg = cfg.get("daily_entry", {})

        # Parameters
        w_sma50 = weekly_cfg.get("sma50_period", 50)
        w_sma200 = weekly_cfg.get("sma200_period", 200)
        w_rsi_min = weekly_cfg.get("rsi_min", 45)
        w_rsi_max = weekly_cfg.get("rsi_max", 70)
        w_adx_min = weekly_cfg.get("adx_min", 18)
        require_weeks = weekly_cfg.get("require_weeks", 2)

        d_rsi_min = daily_cfg.get("rsi_min", 35)
        d_rsi_max = daily_cfg.get("rsi_max", 50)
        d_sma20 = daily_cfg.get("sma20_period", 20)
        d_sma200 = daily_cfg.get("sma200_period", 200)
        dist_min = daily_cfg.get("dist_sma20_min_pct", -8.0)
        dist_max = daily_cfg.get("dist_sma20_max_pct", -2.0)
        vol_sma = daily_cfg.get("volume_sma_period", 20)
        vol_ratio_min = daily_cfg.get("volume_ratio_min", 0.8)
        earn_before = daily_cfg.get("earnings_blackout_before_bdays", 5)
        earn_after = daily_cfg.get("earnings_blackout_after_bdays", 2)

        signals = []

        for symbol, sub in df.groupby("symbol", sort=False):
            sub = sub.sort_values("date").copy()
            # Daily indicators
            sub_daily = add_daily_indicators(sub)

            # Weekly resample: W-FRI, use EUR columns
            # Need to set date as index for resample
            sub_weekly = sub.set_index("date")
            # Resample to weekly - aggregate
            # Use explicit rule
            rule = weekly_cfg.get("resample_rule", "W-FRI")
            agg_dict = {
                "open_eur": "first",
                "high_eur": "max",
                "low_eur": "min",
                "close_eur": "last",
                "volume": "sum",
            }
            # Only resample rows that exist; keep only columns needed
            weekly_resampled = sub_weekly.resample(rule).agg(agg_dict).dropna(subset=["close_eur"])
            # Add weekly indicators
            weekly_with_ind = add_weekly_indicators(weekly_resampled)

            # Determine weekly gate per weekly bar
            # Need close > SMA50_W, SMA50_W > SMA200_W, RSI in [45,70], ADX>=18
            weekly_with_ind["gate_weekly"] = (
                (weekly_with_ind["close_eur"] > weekly_with_ind["SMA50_W"])
                & (weekly_with_ind["SMA50_W"] > weekly_with_ind["SMA200_W"])
                & (weekly_with_ind["RSI14_W"] >= w_rsi_min)
                & (weekly_with_ind["RSI14_W"] <= w_rsi_max)
                & (weekly_with_ind["ADX14_W"] >= w_adx_min)
            )
            # Require for two completed weekly bars: gate true for last 2 weeks
            weekly_with_ind["gate_2w"] = weekly_with_ind["gate_weekly"].rolling(2).sum() == 2
            # Shift? For daily alignment, gate must be from last completed weekly bar before daily date.
            # Use merge_asof.

            # Prepare daily frame with date for merge
            # weekly_with_ind index is weekly end date (Fri)
            weekly_gate_df = weekly_with_ind[["gate_2w"]].copy()
            weekly_gate_df.index.name = "weekly_date"
            weekly_gate_df = weekly_gate_df.reset_index()

            # For each daily date, find last weekly_date <= daily_date - 1 day? Actually weekly bar must be completed.
            # If daily is Monday, last weekly is previous Fri. If daily is Fri, last weekly is that Fri's bar but that bar completes at Friday close same as daily close.
            # To avoid look-ahead for Friday daily bar, we should use weekly gate from previous week (shift 1) if we require completed bar.
            # Simpler: use weekly gate from prior week (shift).
            # We will use merge_asof with direction backward and require weekly_date < daily_date or <= daily_date - 1? We'll implement two modes and test.
            # For Phase 1, use weekly gate from last weekly bar strictly before daily date's week end? The safest is to use weekly gate as of previous weekly close.
            # Implementation: shift gate by 1 week to ensure no same-week look-ahead.
            weekly_gate_df["gate_2w_shifted"] = weekly_gate_df["gate_2w"].shift(1)
            # Now merge
            sub_daily = sub_daily.sort_values("date")
            # Create weekly gate mapping via asof
            # Use pd.merge_asof
            sub_daily["date_for_merge"] = sub_daily["date"]
            weekly_gate_df_sorted = weekly_gate_df.sort_values("weekly_date")
            merged = pd.merge_asof(
                sub_daily.sort_values("date_for_merge"),
                weekly_gate_df_sorted[["weekly_date", "gate_2w_shifted"]].sort_values("weekly_date"),
                left_on="date_for_merge",
                right_on="weekly_date",
                direction="backward",
            )
            # merged has gate_2w_shifted for each daily
            merged["weekly_gate_ok"] = merged["gate_2w_shifted"].fillna(False)

            # Daily conditions
            # RSI in [35,50]
            cond_rsi = (merged["RSI14"] >= d_rsi_min) & (merged["RSI14"] <= d_rsi_max)
            # distance to SMA20 in [-8,-2]
            # DIST already computed as (close - SMA20)/SMA20*100
            cond_dist = (merged["DIST_SMA20_PCT"] >= dist_min) & (merged["DIST_SMA20_PCT"] <= dist_max)
            # close above SMA200
            cond_sma200 = merged["close_eur"] > merged["SMA200"]
            # volume ratio >=0.8
            cond_vol = merged["VOLUME_RATIO"] >= vol_ratio_min

            merged["daily_ok"] = cond_rsi & cond_dist & cond_sma200 & cond_vol

            # Earnings blackout: no entry within 5 bdays before or 2 bdays after earnings
            # earnings_date per row is the next earnings date for that symbol (maybe repeated)
            # If earnings_date is NaT, no blackout
            def is_blackout(row):
                ed = row["earnings_date"]
                d = row["date"]
                if pd.isna(ed):
                    return False
                # Normalize to date
                # Compute business days between d and ed
                # If d is before ed: days_until = business days from d to ed
                # If d is after ed: days_after = business days from ed to d
                # Use bdate_range
                # Simplistic: count bdays
                # If d < ed: gap = bdays(d, ed)  ; if gap <=5 and gap>=0 -> block (5 before)
                # If d >= ed: gap = bdays(ed, d) ; if gap <=2 -> block (2 after)
                # We'll compute both
                # Use helper
                if d.tzinfo is None:
                    d = d.tz_localize("UTC")
                if ed.tzinfo is None:
                    ed = ed.tz_localize("UTC")
                # Normalize
                d_norm = d.normalize()
                ed_norm = ed.normalize()
                if d_norm < ed_norm:
                    # d before earnings
                    # Count bdays from d to ed exclusive of d? Let's use bdate_range
                    # Number of business days after d until ed inclusive
                    rng = pd.bdate_range(d_norm + pd.Timedelta(days=1), ed_norm, freq="B")
                    # But if d is Fri and ed is Mon, rng len =1
                    # We want to know if there are <=5 business days until earnings
                    # If ed is 5 bdays away, block
                    # So compute bdays between d and ed
                    # Use _business_days_diff helper logic: bdays from d to ed
                    # Let's compute via bdate_range including ed but excluding d
                    bdays_until = len(pd.bdate_range(d_norm, ed_norm, freq="B")) - 1
                    # bdays_until 0 means same day, 1 means next bday, etc.
                    if 0 <= bdays_until <= earn_before:
                        # Check if bdays_until is within [0,5] but if d is before ed, bdays_until should be >0
                        # If d is 5 bdays before ed, block
                        return True
                    # Also if d is same day as ed, block (0)
                    if bdays_until == 0:
                        return True
                elif d_norm >= ed_norm:
                    # d on or after earnings
                    rng = pd.bdate_range(ed_norm, d_norm, freq="B")
                    bdays_after = len(rng) - 1
                    if 0 <= bdays_after <= earn_after:
                        return True
                return False

            # Apply per row (vectorized loop is okay for small)
            merged["is_blackout"] = merged.apply(is_blackout, axis=1)

            # Weekly gate AND daily_ok AND not blackout => signal
            merged["signal_raw"] = merged["weekly_gate_ok"] & merged["daily_ok"] & (~merged["is_blackout"])
            # Ensure no signal where indicators NaN (warmup)
            # If any required indicator NaN, signal false
            required_indicators = ["RSI14", "SMA20", "SMA200", "VOLUME_RATIO", "DIST_SMA20_PCT"]
            for col in required_indicators:
                merged["signal_raw"] = merged["signal_raw"] & merged[col].notna()
            # Also weekly gate requires valid weekly indicators; those already NaN leads to gate false

            # Create signal DataFrame with date, symbol, signal
            for _, row in merged.iterrows():
                sig = 1 if row["signal_raw"] else 0
                reason_parts = []
                if row["weekly_gate_ok"]:
                    reason_parts.append("weekly_gate_ok")
                if row["daily_ok"]:
                    reason_parts.append("daily_ok")
                if row["is_blackout"]:
                    reason_parts.append("earnings_blackout")
                signals.append(
                    {
                        "date": row["date"],
                        "symbol": symbol,
                        "signal": sig,
                        "signal_raw": sig,
                        "weekly_gate_ok": bool(row["weekly_gate_ok"]),
                        "daily_ok": bool(row["daily_ok"]),
                        "is_blackout": bool(row["is_blackout"]),
                        "reason": ";".join(reason_parts),
                        "close_eur": row["close_eur"],
                        "open_eur": row["open_eur"],
                        "rsi14": row["RSI14"],
                        "dist_sma20": row["DIST_SMA20_PCT"],
                        "volume_ratio": row["VOLUME_RATIO"],
                    }
                )

        sig_df = pd.DataFrame(signals)
        if sig_df.empty:
            sig_df = pd.DataFrame(columns=["date", "symbol", "signal", "reason"])
        else:
            sig_df = sig_df.sort_values(["symbol", "date"]).reset_index(drop=True)
        return sig_df
