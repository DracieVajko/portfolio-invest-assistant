"""Chart pack renderer (Appendix B6).

C1 daily 2y + SMA50/200 + volume + RSI/MACD panels. C2 weekly 10y log +
SMA40w/10w. C3 full history log + underwater panel. C4 relative strength
vs benchmark + its averages. C5 held-asset overlay (buys/sells + avg cost).

12x7in, 150dpi, colorblind-safe Okabe-Ito palette, as-of in every title,
log y-axis for >=5y windows. Agg backend (headless-safe). Per-chart
try/except: failures log a warning and yield no file (never raises).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from investment_engine.research.ta_snapshot import (  # noqa: E402
    _macd,
    _rsi_wilder,
    _sma,
)

logger = logging.getLogger(__name__)

# Okabe-Ito colorblind-safe palette.
C_PRICE, C_SMA50, C_SMA200 = "#0072B2", "#E69F00", "#D55E00"
C_VOL, C_RSI, C_MACD, C_SIG = "#56B4E9", "#009E73", "#0072B2", "#CC79A7"
C_RS, C_DD, C_BUY, C_SELL, C_COST = "#0072B2", "#D55E00", "#009E73", "#CC79A7", "#000000"


def _prep(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out.index = pd.to_datetime(out.index)
    out = out.sort_index()
    out.columns = [str(c).lower() for c in out.columns]
    return out


def _title(symbol: str, as_of: str, extra: str = "") -> str:
    base = f"{symbol} — as of {as_of}"
    return f"{base} | {extra}" if extra else base


def _save(fig: plt.Figure, path: Path) -> str | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=150, bbox_inches="tight")
        return str(path)
    except Exception as exc:
        logger.warning("chart save failed for %s: %s", path, type(exc).__name__)
        return None
    finally:
        plt.close(fig)


def render_c1(frame: pd.DataFrame, symbol: str, as_of: str, out_dir: Path,
              regime: str = "") -> str | None:
    """Daily last 2y: price + SMA50/200, volume, RSI, MACD."""
    try:
        df = _prep(frame).tail(504)
        if len(df) < 60:
            return None
        cl = df["close"].to_numpy(dtype=float)
        fig, axes = plt.subplots(4, 1, figsize=(12, 7), sharex=True,
                                 gridspec_kw={"height_ratios": [3, 1, 1, 1]})
        ax = axes[0]
        ax.plot(df.index, cl, color=C_PRICE, lw=1.0, label="Close")
        ax.plot(df.index, _sma(cl, 50), color=C_SMA50, lw=0.9, label="SMA50")
        ax.plot(df.index, _sma(cl, 200), color=C_SMA200, lw=0.9, label="SMA200")
        ax.set_title(_title(symbol, as_of, regime or "C1 daily 2y"))
        ax.legend(fontsize=8, loc="upper left")
        axes[1].bar(df.index, df["volume"].to_numpy(), color=C_VOL, width=1.0)
        axes[1].set_ylabel("Vol")
        rsi = _rsi_wilder(cl)
        axes[2].plot(df.index, rsi, color=C_RSI, lw=0.9)
        axes[2].axhline(70, color="grey", lw=0.6)
        axes[2].axhline(30, color="grey", lw=0.6)
        axes[2].set_ylabel("RSI14")
        macd_v, macd_s, macd_h = _macd(cl)
        axes[3].plot(df.index, macd_v, color=C_MACD, lw=0.9, label="MACD")
        axes[3].plot(df.index, macd_s, color=C_SIG, lw=0.9, label="Signal")
        axes[3].bar(df.index, np.nan_to_num(macd_h), color="grey", width=1.0)
        axes[3].set_ylabel("MACD")
        for a in axes:
            a.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        fig.autofmt_xdate()
        return _save(fig, Path(out_dir) / f"{symbol}_{as_of}_C1.png")
    except Exception as exc:
        logger.warning("C1 failed for %s: %s", symbol, type(exc).__name__)
        return None


def render_c2(frame: pd.DataFrame, symbol: str, as_of: str, out_dir: Path,
              regime: str = "") -> str | None:
    """Weekly last 10y (or max), log scale, SMA40w/10w."""
    try:
        df = _prep(frame)
        agg: dict[str, str] = {"open": "first", "high": "max", "low": "min",
                               "close": "last", "volume": "sum"}
        weekly = df.resample("W-FRI").agg(
            {k: v for k, v in agg.items() if k in df.columns}).dropna(subset=["close"])
        weekly = weekly.tail(520)
        if len(weekly) < 40:
            return None
        cl = weekly["close"].to_numpy(dtype=float)
        fig, ax = plt.subplots(figsize=(12, 7))
        ax.plot(weekly.index, cl, color=C_PRICE, lw=1.0, label="Close (weekly)")
        ax.plot(weekly.index, _sma(cl, 40), color=C_SMA200, lw=0.9, label="SMA40w")
        ax.plot(weekly.index, _sma(cl, 10), color=C_SMA50, lw=0.9, label="SMA10w")
        ax.set_yscale("log")
        ax.set_title(_title(symbol, as_of, regime or "C2 weekly 10y (log)"))
        ax.legend(fontsize=8, loc="upper left")
        fig.autofmt_xdate()
        return _save(fig, Path(out_dir) / f"{symbol}_{as_of}_C2.png")
    except Exception as exc:
        logger.warning("C2 failed for %s: %s", symbol, type(exc).__name__)
        return None


def render_c3(frame: pd.DataFrame, symbol: str, as_of: str, out_dir: Path) -> str | None:
    """Full history, log scale, with underwater (drawdown) panel."""
    try:
        df = _prep(frame)
        if len(df) < 60:
            return None
        cl = df["close"].to_numpy(dtype=float)
        peak = np.maximum.accumulate(np.where(np.isnan(cl), -np.inf, cl))
        with np.errstate(divide="ignore", invalid="ignore"):
            dd = np.where(peak > 0, cl / peak - 1.0, np.nan)
        fig, (ax, axd) = plt.subplots(2, 1, figsize=(12, 7), sharex=True,
                                      gridspec_kw={"height_ratios": [3, 1]})
        ax.plot(df.index, cl, color=C_PRICE, lw=0.9)
        ax.set_yscale("log")
        ax.set_title(_title(symbol, as_of, "C3 full history (log)"))
        axd.fill_between(df.index, np.nan_to_num(dd) * 100.0, 0.0, color=C_DD, alpha=0.6)
        axd.set_ylabel("Drawdown %")
        fig.autofmt_xdate()
        return _save(fig, Path(out_dir) / f"{symbol}_{as_of}_C3.png")
    except Exception as exc:
        logger.warning("C3 failed for %s: %s", symbol, type(exc).__name__)
        return None


def render_c4(frame: pd.DataFrame, bench: pd.DataFrame, symbol: str, as_of: str,
              out_dir: Path, bench_name: str = "") -> str | None:
    """Relative strength vs benchmark + its SMA50/200."""
    try:
        a = _prep(frame)["close"].dropna()
        b = _prep(bench)["close"].dropna()
        joined = pd.DataFrame({"a": a, "b": b}).dropna()
        if len(joined) < 60:
            return None
        rs = (joined["a"] / joined["b"]).to_numpy()
        fig, ax = plt.subplots(figsize=(12, 7))
        ax.plot(joined.index, rs, color=C_RS, lw=1.0, label="RS line")
        ax.plot(joined.index, _sma(rs, 50), color=C_SMA50, lw=0.9, label="RS SMA50")
        ax.plot(joined.index, _sma(rs, 200), color=C_SMA200, lw=0.9, label="RS SMA200")
        ax.set_title(_title(symbol, as_of, f"C4 RS vs {bench_name or 'benchmark'}"))
        ax.legend(fontsize=8, loc="upper left")
        fig.autofmt_xdate()
        return _save(fig, Path(out_dir) / f"{symbol}_{as_of}_C4.png")
    except Exception as exc:
        logger.warning("C4 failed for %s: %s", symbol, type(exc).__name__)
        return None


def render_c5(frame: pd.DataFrame, symbol: str, as_of: str, out_dir: Path,
              transactions: list[dict] | None = None,
              avg_cost: float | None = None) -> str | None:
    """Held-asset overlay: buys/sells markers + average-cost line on C1 base."""
    try:
        base = render_c1(frame, symbol, as_of, out_dir, regime="C5 with transactions")
        if not base:
            return None
        # Re-render on top is wasteful; overlay by re-opening is unreliable in
        # Agg — instead draw markers onto a fresh C1-style figure.
        df = _prep(frame).tail(504)
        cl = df["close"].to_numpy(dtype=float)
        fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True,
                                 gridspec_kw={"height_ratios": [3, 1]})
        axes[0].plot(df.index, cl, color=C_PRICE, lw=1.0)
        for tx in transactions or []:
            try:
                when = pd.to_datetime(tx.get("date"))
                price = float(tx.get("price", 0) or 0)
                side = str(tx.get("side", "")).upper()
            except (TypeError, ValueError):
                continue
            if side.startswith("BUY"):
                axes[0].scatter([when], [price], color=C_BUY, marker="^", s=60, zorder=5)
            elif side.startswith("SELL"):
                axes[0].scatter([when], [price], color=C_SELL, marker="v", s=60, zorder=5)
        if avg_cost:
            try:
                axes[0].axhline(float(avg_cost), color=C_COST, ls="--", lw=1.0,
                                label=f"Avg cost {float(avg_cost):.2f}")
                axes[0].legend(fontsize=8, loc="upper left")
            except (TypeError, ValueError):
                pass
        axes[0].set_title(_title(symbol, as_of, "C5 transactions + avg cost"))
        axes[1].bar(df.index, df["volume"].to_numpy(), color=C_VOL, width=1.0)
        fig.autofmt_xdate()
        out = _save(fig, Path(out_dir) / f"{symbol}_{as_of}_C5.png")
        # The plain C1 stays too (pack completeness); C5 is the overlay.
        return out or base
    except Exception as exc:
        logger.warning("C5 failed for %s: %s", symbol, type(exc).__name__)
        return None


def render_pack(frame: pd.DataFrame, symbol: str, as_of: str,
                out_dir: str | Path, *,
                bench: pd.DataFrame | None = None,
                bench_name: str = "",
                transactions: list[dict[str, Any]] | None = None,
                avg_cost: float | None = None,
                kinds: tuple[str, ...] = ("C1", "C2", "C3", "C4", "C5"),
                regime: str = "") -> list[str]:
    """Render the requested kinds. Returns saved paths (subset on failure)."""
    out = Path(out_dir)
    saved: list[str] = []
    if "C1" in kinds:
        p = render_c1(frame, symbol, as_of, out, regime)
        if p:
            saved.append(p)
    if "C2" in kinds:
        p = render_c2(frame, symbol, as_of, out, regime)
        if p:
            saved.append(p)
    if "C3" in kinds:
        p = render_c3(frame, symbol, as_of, out)
        if p:
            saved.append(p)
    if "C4" in kinds and bench is not None:
        p = render_c4(frame, bench, symbol, as_of, out, bench_name)
        if p:
            saved.append(p)
    if "C5" in kinds and (transactions or avg_cost):
        p = render_c5(frame, symbol, as_of, out, transactions, avg_cost)
        if p:
            saved.append(p)
    return saved
