"""Metrics from post-cost EUR equity curve."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class Metrics:
    start_equity: float
    end_equity: float
    total_return: float
    cagr: float
    max_drawdown: float
    annualized_volatility: float
    sharpe_ratio: float  # rf=0
    sortino_ratio: float  # target=0
    calmar_ratio: float
    trade_count: int
    win_rate: float
    avg_holding_days: float
    turnover: float  # annualized
    avg_gross_exposure: float


def compute_metrics(
    equity_curve: pd.Series,
    trades: list[dict] | pd.DataFrame,
    start_equity: float | None = None,
) -> Metrics:
    """
    Compute metrics from post-cost EUR equity.
    equity_curve: indexed by date (UTC), values are EUR equity.
    trades: list of dicts with keys: pnl_eur, holding_days, entry_value, exit_value etc.
            or DataFrame.
    """
    if equity_curve is None or len(equity_curve) < 2:
        # Degenerate
        s = float(equity_curve.iloc[0]) if equity_curve is not None and len(equity_curve) > 0 else 0.0
        return Metrics(
            start_equity=s,
            end_equity=s,
            total_return=0.0,
            cagr=0.0,
            max_drawdown=0.0,
            annualized_volatility=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            calmar_ratio=0.0,
            trade_count=0,
            win_rate=0.0,
            avg_holding_days=0.0,
            turnover=0.0,
            avg_gross_exposure=0.0,
        )

    equity_curve = equity_curve.sort_index()
    # Ensure float
    equity_curve = equity_curve.astype(float)

    start_eq = float(equity_curve.iloc[0]) if start_equity is None else float(start_equity)
    end_eq = float(equity_curve.iloc[-1])
    total_return = (end_eq / start_eq - 1) if start_eq != 0 else 0.0

    n = len(equity_curve) - 1  # number of returns
    # CAGR with 252 trading days
    if n > 0 and start_eq > 0 and end_eq > 0:
        # Use actual trading days count
        cagr = (end_eq / start_eq) ** (252 / n) - 1
    else:
        cagr = 0.0

    # Daily returns
    daily_returns = equity_curve.pct_change().dropna()
    # Max drawdown
    running_max = equity_curve.cummax()
    drawdown = (equity_curve - running_max) / running_max
    max_dd = float(drawdown.min())  # negative
    max_dd_abs = abs(max_dd)

    # Annualized volatility
    if len(daily_returns) > 1:
        ann_vol = float(daily_returns.std(ddof=1) * np.sqrt(252))
    else:
        ann_vol = 0.0

    # Sharpe rf=0
    if len(daily_returns) > 1 and daily_returns.std(ddof=1) != 0:
        sharpe = float(daily_returns.mean() / daily_returns.std(ddof=1) * np.sqrt(252))
    else:
        sharpe = 0.0

    # Sortino target=0
    downside = daily_returns[daily_returns < 0]
    if len(downside) > 1 and downside.std(ddof=1) != 0:
        # Use downside deviation (std of downside) * sqrt(252)
        sortino = float(daily_returns.mean() / downside.std(ddof=1) * np.sqrt(252))
    elif len(downside) == 0:
        # No downside => Sortino 0 or inf; we set 0 if no downside and mean positive? But define 0
        sortino = 0.0 if daily_returns.mean() == 0 else float("inf") if daily_returns.mean() > 0 else 0.0
        # Clamp inf to large number for JSON serialization? Keep 0 for simplicity if no downside
        if sortino == float("inf"):
            sortino = 0.0
    else:
        sortino = 0.0

    calmar = (cagr / max_dd_abs) if max_dd_abs != 0 else 0.0

    # Trades
    if isinstance(trades, pd.DataFrame):
        trades_list = trades.to_dict("records") if not trades.empty else []
    else:
        trades_list = trades or []

    trade_count = len(trades_list)
    if trade_count > 0:
        wins = sum(1 for t in trades_list if t.get("pnl_eur", 0) > 0)
        win_rate = wins / trade_count
        holding_days = [t.get("holding_days", 0) for t in trades_list]
        avg_holding = float(np.mean(holding_days)) if holding_days else 0.0
        # Turnover annualized: sum(|trade_value|) / avg_equity / years * ?
        # We have entry_value and exit_value per trade (post-cost notional)
        total_traded = sum(abs(t.get("entry_value", 0)) + abs(t.get("exit_value", 0)) for t in trades_list)
        avg_equity = float(equity_curve.mean())
        years = n / 252 if n > 0 else 0
        if avg_equity > 0 and years > 0:
            turnover = total_traded / avg_equity / years  # annualized turnover (x per year)
        else:
            turnover = 0.0
        # Also alternative per-day turnover: total_traded / avg_equity / n *252 same as above
        # Avg gross exposure: need equity_curve with market_value; if not available, estimate from trades
        # For now, if trades have gross_exposure series, use mean; else 0.
        # Engine will provide equity_curve with market_value; we can compute if equity_curve has attribute?
        # We will compute from trades if possible: avg holding exposure = avg market_value / equity
        # Simpler: if trades_list has avg_gross_exposure key, use.
        avg_gross_exposure = 0.0
        # Try to compute from equity_curve if we have market value series passed via attrs
        # Fallback: estimate as total market value days / equity days - not available, keep 0
        # Engine will compute and pass via separate param; here we just handle if trades contain exposure
        # If any trade has gross_exposure, average it
        exposures = [t.get("gross_exposure", 0) for t in trades_list if "gross_exposure" in t]
        if exposures:
            avg_gross_exposure = float(np.mean(exposures))
    else:
        win_rate = 0.0
        avg_holding = 0.0
        turnover = 0.0
        avg_gross_exposure = 0.0

    return Metrics(
        start_equity=start_eq,
        end_equity=end_eq,
        total_return=float(total_return),
        cagr=float(cagr),
        max_drawdown=float(max_dd_abs),
        annualized_volatility=float(ann_vol),
        sharpe_ratio=float(sharpe),
        sortino_ratio=float(sortino),
        calmar_ratio=float(calmar),
        trade_count=int(trade_count),
        win_rate=float(win_rate),
        avg_holding_days=float(avg_holding),
        turnover=float(turnover),
        avg_gross_exposure=float(avg_gross_exposure),
    )


def metrics_to_dict(m: Metrics) -> dict:
    return {
        "start_equity": round(m.start_equity, 2),
        "end_equity": round(m.end_equity, 2),
        "total_return": round(m.total_return, 6),
        "cagr": round(m.cagr, 6),
        "max_drawdown": round(m.max_drawdown, 6),
        "annualized_volatility": round(m.annualized_volatility, 6),
        "sharpe_ratio": round(m.sharpe_ratio, 4),
        "sortino_ratio": round(m.sortino_ratio, 4) if m.sortino_ratio != float("inf") else 0.0,
        "calmar_ratio": round(m.calmar_ratio, 4),
        "trade_count": m.trade_count,
        "win_rate": round(m.win_rate, 4),
        "avg_holding_days": round(m.avg_holding_days, 2),
        "turnover": round(m.turnover, 4),
        "avg_gross_exposure": round(m.avg_gross_exposure, 4),
    }
