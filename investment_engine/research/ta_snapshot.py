"""Technical snapshot builder implementing Appendix B exactly.

Pure functions over an OHLCV frame (no network, no fabricated data).
Formulas: B2 definitions verbatim (EMA seed=SMA, Wilder RSI/ATR/ADX,
Bollinger ddof=0, CAGR with 365.25). Regime B3 in exact evaluation order.
Every output field is a computed value or null with a note (B5).

Conventions:
- `close/high/low` are split-adjusted (levels, indicators).
- `adj` (adj_close when available, else close) is total-return basis for
  returns/drawdowns/volatility/relative strength; `price_basis` records it.
- NaN warm-up is never forward-filled; percentiles need >= 756 daily bars.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

SCHEMA_VERSION = 1

ANNUALIZATION = {"equity": 252, "fund": 252, "crypto": 365}
RETURN_WINDOWS = {"equity": (21, 63, 126, 252), "fund": (21, 63, 126, 252),
                  "crypto": (30, 91, 182, 365)}


# --------------------------------------------------------------------------
# primitives (B2)
# --------------------------------------------------------------------------

def _sma(values: np.ndarray, n: int) -> np.ndarray:
    out = np.full_like(values, np.nan, dtype=float)
    if len(values) >= n and n > 0:
        out[n - 1:] = np.convolve(values, np.ones(n) / n, mode="valid")
    return out


def _ema(values: np.ndarray, n: int) -> np.ndarray:
    """EMA with alpha=2/(n+1), seeded by the SMA of the first n closes."""
    out = np.full_like(values, np.nan, dtype=float)
    if len(values) >= n and n > 0:
        alpha = 2.0 / (n + 1)
        out[n - 1] = float(np.mean(values[:n]))
        for t in range(n, len(values)):
            out[t] = alpha * values[t] + (1.0 - alpha) * out[t - 1]
    return out


def _wilder_smooth(values: np.ndarray, n: int, seed_n: int | None = None) -> np.ndarray:
    """Wilder smoothing: first average = mean of first n, then avg=(prev*(n-1)+x)/n."""
    out = np.full_like(values, np.nan, dtype=float)
    seed_n = seed_n or n
    if len(values) >= seed_n and n > 0:
        out[seed_n - 1] = float(np.nanmean(values[:seed_n]))
        for t in range(seed_n, len(values)):
            out[t] = (out[t - 1] * (n - 1) + values[t]) / n
    return out


def _rsi_wilder(close: np.ndarray, n: int = 14) -> np.ndarray:
    out = np.full_like(close, np.nan, dtype=float)
    if len(close) <= n:
        return out
    delta = np.diff(close, prepend=np.nan)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    gain[0] = loss[0] = np.nan
    avg_gain = _wilder_smooth(gain, n)
    avg_loss = _wilder_smooth(loss, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        out = 100.0 - 100.0 / (1.0 + rs)
    both_zero = (avg_gain == 0) & (avg_loss == 0)
    out[both_zero & ~np.isnan(avg_gain)] = 50.0
    only_gain = (avg_gain > 0) & (avg_loss == 0)
    out[only_gain] = 100.0
    return out


def _macd(close: np.ndarray, fast: int = 12, slow: int = 26,
          signal: int = 9) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    macd = _ema(close, fast) - _ema(close, slow)
    valid = macd[~np.isnan(macd)]
    sig = np.full_like(close, np.nan, dtype=float)
    if len(valid) >= signal:
        first = len(close) - len(valid)
        sig[first + signal - 1:] = _ema(valid, signal)[signal - 1:]
    return macd, sig, macd - sig


def _true_range(high: np.ndarray, low: np.ndarray,
                close: np.ndarray) -> np.ndarray:
    prev = np.concatenate([[np.nan], close[:-1]])
    return np.maximum.reduce([high - low, np.abs(high - prev), np.abs(low - prev)])


def _atr_wilder(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                n: int = 14) -> np.ndarray:
    tr = _true_range(high, low, close)
    tr[0] = np.nan
    return _wilder_smooth(tr, n)


def _adx_wilder(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                n: int = 14) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Returns (adx, plus_di, minus_di, dx)."""
    up = high[1:] - high[:-1]
    down = low[:-1] - low[1:]
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    pad = np.concatenate([[np.nan], plus_dm])
    mad = np.concatenate([[np.nan], minus_dm])
    tr = _true_range(high, low, close)
    tr[0] = np.nan
    sm_tr = _wilder_smooth(tr, n)
    sm_p = _wilder_smooth(pad, n)
    sm_m = _wilder_smooth(mad, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * sm_p / sm_tr
        minus_di = 100.0 * sm_m / sm_tr
        denom = plus_di + minus_di
        dx = np.where(denom == 0, 0.0, 100.0 * np.abs(plus_di - minus_di) / denom)
    dx[np.isnan(sm_tr)] = np.nan
    first_valid = int(np.argmax(~np.isnan(dx))) if np.any(~np.isnan(dx)) else len(dx)
    adx = np.full_like(close, np.nan, dtype=float)
    tail = dx[first_valid:]
    if len(tail) >= n:
        sm = _wilder_smooth(tail, n)
        adx[first_valid:] = sm
    return adx, plus_di, minus_di, dx


def _bollinger(close: np.ndarray, n: int = 20,
               k: float = 2.0) -> tuple[np.ndarray, np.ndarray, np.ndarray,
                                       np.ndarray, np.ndarray]:
    s = pd.Series(close)
    mid = s.rolling(n).mean().to_numpy()
    sd = s.rolling(n).std(ddof=0).to_numpy()
    upper, lower = mid + k * sd, mid - k * sd
    with np.errstate(divide="ignore", invalid="ignore"):
        pct_b = (close - lower) / (upper - lower)
        bandwidth = (upper - lower) / mid
    return upper, lower, pct_b, bandwidth, mid


def _realized_vol(logret: np.ndarray, window: int, annual: int) -> np.ndarray:
    out = np.full_like(logret, np.nan, dtype=float)
    s = pd.Series(logret)
    out[:] = (s.rolling(window).std(ddof=1) * math.sqrt(annual)).to_numpy()
    return out


def _percentile(value: float, history: np.ndarray) -> float | None:
    hist = history[~np.isnan(history)]
    if hist.size == 0 or value != value:
        return None
    return float(100.0 * np.mean(hist <= value))


def _cagr(start_v: float, end_v: float, days: float) -> float | None:
    if days < 365 or not start_v or start_v <= 0 or end_v is None or end_v != end_v:
        return None
    try:
        return float((end_v / start_v) ** (365.25 / days) - 1.0)
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


# --------------------------------------------------------------------------
# resampling / session logic (B1)
# --------------------------------------------------------------------------

def _to_daily(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    frame.index = pd.to_datetime(frame.index)
    frame = frame.sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    frame.columns = [str(c).lower() for c in frame.columns]
    return frame


def _resample_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last",
           "volume": "sum"}
    if "adj_close" in daily.columns:
        agg["adj_close"] = "last"
    weekly = daily.resample("W-FRI").agg(agg).dropna(subset=["close"])
    return weekly


def _resample_monthly(daily: pd.DataFrame) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last",
           "volume": "sum"}
    if "adj_close" in daily.columns:
        agg["adj_close"] = "last"
    monthly = daily.resample("ME").agg(agg).dropna(subset=["close"])
    return monthly


def last_complete_bar(as_of_utc: datetime, exchange_tz: str = "America/New_York",
                      session_close_hour: int = 17) -> date:
    """Last bar date known complete. US session counts complete from 17:00 ET.

    Weekends roll back to Friday (no weekend bars exist).
    """
    try:
        from zoneinfo import ZoneInfo
        local = as_of_utc.astimezone(ZoneInfo(exchange_tz))
    except Exception:
        local = as_of_utc
    if local.time().hour >= session_close_hour:
        candidate = local.date()
    else:
        candidate = local.date().fromordinal(local.date().toordinal() - 1)
    while candidate.weekday() >= 5:  # Sat/Sun -> Friday
        candidate = candidate.fromordinal(candidate.toordinal() - 1)
    return candidate


# --------------------------------------------------------------------------
# drawdowns / distributions / structure (B2, B4)
# --------------------------------------------------------------------------

def _drawdown_series(adj: np.ndarray) -> np.ndarray:
    peak = np.maximum.accumulate(np.where(np.isnan(adj), -np.inf, adj))
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak > 0, adj / peak - 1.0, np.nan)
    return dd


def _top_drawdowns(dates: list[date], adj: np.ndarray,
                   max_n: int = 5, min_depth: float = 0.05) -> list[dict]:
    out: list[dict] = []
    work = np.array(adj, dtype=float)
    for _ in range(max_n):
        valid = ~np.isnan(work)
        if valid.sum() < 2:
            break
        trough = int(np.nanargmin(np.where(valid, work / np.maximum.accumulate(
            np.where(valid, work, -np.inf)) - 1.0, np.nan)))
        peak_val = np.nanmax(work[:trough + 1])
        peak_candidates = np.where(work[:trough + 1] == peak_val)[0]
        peak = int(peak_candidates[-1])
        depth = float(work[trough] / peak_val - 1.0) if peak_val else 0.0
        if depth > -min_depth:
            break
        rec = None
        for t in range(trough + 1, len(work)):
            if valid[t] and work[t] >= peak_val:
                rec = t
                break
        out.append({
            "peak_date": dates[peak].isoformat(), "trough_date": dates[trough].isoformat(),
            "depth": depth, "bars_to_trough": trough - peak,
            "recovery_date": dates[rec].isoformat() if rec is not None else None,
            "bars_to_recover": (rec - trough) if rec is not None else None,
        })
        work[peak:(rec + 1 if rec is not None else len(work))] = np.nan
    return out


def _rolling_distribution(adj: np.ndarray, window: int) -> dict:
    out = {"p5": None, "p25": None, "p50": None, "p75": None, "p95": None,
           "current_pctile": None}
    if len(adj) <= window:
        return out
    rets = adj[window:] / adj[:-window] - 1.0
    rets = rets[~np.isnan(rets)]
    if rets.size == 0:
        return out
    qs = np.percentile(rets, [5, 25, 50, 75, 95])
    out.update(dict(zip(("p5", "p25", "p50", "p75", "p95"), (float(q) for q in qs))))
    out["current_pctile"] = _percentile(float(rets[-1]), rets[:-1])
    return out


def _swing_pivots(high: np.ndarray, low: np.ndarray, k: int) -> tuple[list, list]:
    highs, lows = [], []
    n = len(high)
    for i in range(k, n - k):
        window_h = high[i - k:i]
        ahead_h = high[i + 1:i + 1 + k]
        if high[i] > np.nanmax(window_h) and high[i] >= np.nanmax(ahead_h):
            highs.append(i)
        window_l = low[i - k:i]
        ahead_l = low[i + 1:i + 1 + k]
        if low[i] < np.nanmin(window_l) and low[i] <= np.nanmin(ahead_l):
            lows.append(i)
    return highs, lows


def _zones(prices: np.ndarray, idx: list[int], dates: list[date], atr: float,
           relvol: np.ndarray, side: str) -> list[dict]:
    if not idx or not atr or atr != atr or atr <= 0:
        return []
    pts = sorted((float(prices[i]), i) for i in idx)
    clusters: list[list[tuple[float, int]]] = []
    for p in pts:
        if clusters and abs(p[0] - clusters[-1][-1][0]) <= 0.5 * atr:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    recent_cutoff = len(prices) - 252
    ranked = []
    for cl in clusters:
        vals = [p[0] for p in cl]
        lo, hi = min(vals), max(vals)
        mean = sum(vals) / len(vals)
        lo = min(lo, mean - 0.25 * atr)
        hi = max(hi, mean + 0.25 * atr)
        touches = len(cl)
        score = touches
        for _, i in cl:
            if i >= recent_cutoff:
                score += 1
            if i < len(relvol) and relvol[i] == relvol[i] and relvol[i] >= 1.5:
                score += 1
        ranked.append((score, max(p[1] for p in cl), lo, hi, touches))
    ranked.sort(reverse=True)
    close = float(prices[-1])
    picked = [r for r in ranked
              if (r[2] + r[3]) / 2 > close] if side == "resistance" else \
             [r for r in ranked if (r[2] + r[3]) / 2 < close]
    return [{"low": round(r[2], 4), "high": round(r[3], 4), "touches": r[4],
             "last_touch": dates[r[1]].isoformat()} for r in picked[:2]]


def _significant_gaps(dates: list[date], op: np.ndarray, prev_close: np.ndarray,
                      atr14: np.ndarray, lookback: int = 60) -> list[dict]:
    out: list[dict] = []
    n = len(dates)
    start = max(1, n - lookback)
    for t in range(start, n):
        if any(v != v for v in (op[t], prev_close[t], atr14[t - 1])):
            continue
        if atr14[t - 1] <= 0:
            continue
        gap = op[t] - prev_close[t]
        if abs(gap) < atr14[t - 1]:
            continue
        filled = any((op[s] <= prev_close[t] <= prev_close[s] or
                       op[s] >= prev_close[t] >= prev_close[s])
                      for s in range(t + 1, n))
        out.append({"date": dates[t].isoformat(),
                    "size_atr": round(float(gap / atr14[t - 1]), 2),
                    "filled": bool(filled)})
    return out


# --------------------------------------------------------------------------
# regime (B3, exact order)
# --------------------------------------------------------------------------

def classify_regime(close: float, s50: float, s200: float, slope200: float) -> str:
    """Exactly one regime, evaluated in B3 order. NaN inputs -> TRANSITION_OR_RANGE."""
    try:
        vals = [float(close), float(s50), float(s200), float(slope200)]
    except (TypeError, ValueError):
        return "TRANSITION_OR_RANGE"
    if any(v != v for v in vals):
        return "TRANSITION_OR_RANGE"
    if close > s50 and s50 > s200 and slope200 > 0:
        return "STRONG_UPTREND"
    if s50 > s200 and slope200 > 0 and close <= s50 and close > s200:
        return "UPTREND_PULLBACK"
    if s50 < s200 and slope200 < 0 and close <= s50:
        return "DOWNTREND"
    if s50 < s200 and slope200 <= 0 and close > s50:
        return "BEAR_RALLY"
    return "TRANSITION_OR_RANGE"


# --------------------------------------------------------------------------
# built-in self-tests (B8 subset, recorded in every snapshot)
# --------------------------------------------------------------------------

def run_snapshot_selftests() -> dict:
    """Fast invariant checks on synthetic data. Never raises."""
    failed: list[str] = []
    try:
        if abs(float(_sma(np.arange(1.0, 101.0), 100)[-1]) - 50.5) > 1e-9:
            failed.append("sma-exact")
        up = np.arange(1.0, 301.0)
        if abs(float(_rsi_wilder(up)[-1]) - 100.0) > 1e-9:
            failed.append("rsi-up")
        if abs(float(_rsi_wilder(up[::-1].copy())[-1]) - 0.0) > 1e-9:
            failed.append("rsi-down")
        flat = np.full(300, 42.0)
        if abs(float(_rsi_wilder(flat)[-1]) - 50.0) > 1e-9:
            failed.append("rsi-flat")
        if bool(np.any(_atr_wilder(flat, flat, flat)[14:] < 0)):
            failed.append("atr-nonneg")
        cu, cl_, _, bw, _ = _bollinger(np.arange(1.0, 301.0))
        if not (bool(np.all(cu[19:] >= cl_[19:]))):
            failed.append("bollinger-order")
        dd = _drawdown_series(np.arange(1.0, 501.0))
        if not bool(np.all(dd[~np.isnan(dd)] <= 0)):
            failed.append("drawdown-nonpositive")
        if abs(float(_cagr(100.0, 200.0, 365.25)) - 1.0) > 1e-9:
            failed.append("cagr-doubling")
        trend = 100.0 + np.arange(500.0)
        if classify_regime(trend[-1], float(np.mean(trend[-50:])),
                           float(np.mean(trend[-200:])),
                           float(np.mean(trend[-200:]) / np.mean(trend[-220:-20]) - 1)) != "STRONG_UPTREND":
            failed.append("regime-uptrend")
    except Exception as exc:
        failed.append(f"harness:{type(exc).__name__}")
    return {"passed": not failed, "failed": failed}


# --------------------------------------------------------------------------
# snapshot builder (B5/B7)
# --------------------------------------------------------------------------

def _num(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _normalize_dates(values: Any) -> list[date]:
    """Tolerant date-list parser (str/date/datetime/Series). Never raises."""
    out: list[date] = []
    try:
        items = list(values) if values is not None else []
    except TypeError:
        return out
    for item in items:
        try:
            if isinstance(item, datetime):
                out.append(item.date())
            elif isinstance(item, date):
                out.append(item)
            else:
                out.append(date.fromisoformat(str(item)[:10]))
        except (ValueError, TypeError):
            continue
    return out


def _normalize_splits(splits: Any) -> dict[date, float]:
    """Split map date -> post-split price multiplier (0.25 = 4:1).

    Accepts {date-like: ratio} or [(date-like, ratio), ...]. Never raises.
    """
    out: dict[date, float] = {}
    try:
        pairs = list(splits.items()) if isinstance(splits, dict) else list(splits or [])
    except TypeError:
        return out
    for pair in pairs:
        try:
            d, ratio = pair
            parsed = _normalize_dates([d])
            mult = float(ratio)
            if parsed and mult > 0:
                out[parsed[0]] = mult
        except (TypeError, ValueError):
            continue
    return out


def build_snapshot(
    df: pd.DataFrame,
    *,
    symbol: str,
    name: str = "",
    isin: str = "",
    exchange: str = "",
    currency: str = "",
    price_unit: str = "",
    asset_class: str = "equity",
    benchmark: pd.DataFrame | None = None,
    benchmark_meta: dict | None = None,
    fx: pd.Series | None = None,
    eps: list | None = None,
    events: list | None = None,
    ref_close: float | None = None,
    ex_div_dates: Any = None,
    splits: Any = None,
    raw_path: str = "",
    chart_paths: list | None = None,
    source: str = "yfinance",
    now: datetime | None = None,
) -> dict:
    """Build the Appendix B7 snapshot. Never raises; gaps are null + notes.

    ex_div_dates: known ex-dividend dates (vendor auto-adjust detection).
    splits: {date: post-split multiplier} e.g. 0.25 for 4:1 (pre-split bars
    are adjusted; without an entry, unexplained >50% overnight gaps refuse
    indicator computation).
    """
    notes: list[str] = []
    quality_flags: list[str] = []
    now_utc = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    frame = _to_daily(df)
    try:
        _had_dups = bool(pd.Series(pd.to_datetime(df.index)).duplicated().any())
    except Exception:
        _had_dups = False
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        return _empty_snapshot(symbol, name, isin, exchange, currency, price_unit,
                               asset_class, source, now_utc, raw_path, chart_paths or [],
                               [f"missing columns: {sorted(required - set(frame.columns))}"])
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    # --- corporate-action adjustment first (T09b): known splits rescale
    # pre-split bars; without an entry, unexplained gaps refuse indicators.
    split_map = _normalize_splits(splits)
    applied: list[str] = []
    if split_map:
        try:
            bar_dates = [d.date() for d in frame.index]
            for sdate, mult in sorted(split_map.items()):
                if mult == 1.0:
                    continue
                mask = np.array([d < sdate for d in bar_dates])
                if not mask.any():
                    continue
                for col in ("open", "high", "low", "close"):
                    frame[col] = np.where(mask, frame[col].to_numpy() * mult,
                                          frame[col].to_numpy())
                frame["volume"] = np.where(
                    mask, frame["volume"].to_numpy() / mult, frame["volume"].to_numpy())
                applied.append(f"{sdate.isoformat()} x{mult:g}")
            if applied:
                notes.append("split-adjusted pre-split bars: " + ", ".join(applied))
        except Exception as exc:
            notes.append(f"split adjustment skipped: {type(exc).__name__}")
    frame = frame.dropna(subset=["close"])
    if frame.empty:
        return _empty_snapshot(symbol, name, isin, exchange, currency, price_unit,
                               asset_class, source, now_utc, raw_path, chart_paths or [],
                               ["no valid close bars"])
    has_adj = "adj_close" in frame.columns
    adj_col = "adj_close" if has_adj else "close"
    if not has_adj:
        notes.append("no adj_close column: returns use price-only basis")
        notes.append("single price series: split-adjusted levels assumed")
    dates: list[date] = [d.date() for d in frame.index]
    n = len(frame)
    annual = ANNUALIZATION.get(asset_class, 252)
    win = RETURN_WINDOWS.get(asset_class, RETURN_WINDOWS["equity"])
    w1, w3, w6, w12 = win

    op = frame["open"].to_numpy(dtype=float)
    hi = frame["high"].to_numpy(dtype=float)
    lo = frame["low"].to_numpy(dtype=float)
    cl = frame["close"].to_numpy(dtype=float)
    vo = frame["volume"].to_numpy(dtype=float)
    adj = pd.to_numeric(frame[adj_col], errors="coerce").to_numpy(dtype=float)

    # --- data quality (6.2.6, T09) ---
    if _had_dups or frame.index.has_duplicates:
        quality_flags.append("duplicate bars (kept last)")
    try:
        expected_bdays = set(pd.date_range(dates[0], dates[-1], freq="B").date)
        missing_wd = len(expected_bdays - {d for d in dates if d.weekday() < 5})
        if missing_wd > 0:
            quality_flags.append(f"missing {missing_wd} weekday sessions")
    except Exception:
        pass
    run = maxrun = 0
    for v in vo:
        run = run + 1 if v == 0 else 0
        maxrun = max(maxrun, run)
    if maxrun >= 3:
        quality_flags.append(f"zero-volume run of {maxrun} bars")
    elif bool((vo == 0).any()):
        quality_flags.append("zero-volume bars present")
    if n >= 2:
        rets_chk = np.diff(np.log(np.where(adj > 0, adj, np.nan)))
        valid_chk = rets_chk[~np.isnan(rets_chk)]
        with np.errstate(invalid="ignore"):
            big = np.abs(rets_chk) > 0.40
        if bool(np.any(big[~np.isnan(rets_chk)])):
            quality_flags.append(">40% daily move: check splits/corporate actions")
        if valid_chk.size >= 30:
            sigma = float(np.std(valid_chk, ddof=1))
            if sigma > 0:
                n5 = int(np.sum(np.abs(valid_chk) > 5 * sigma))
                if n5 > 0:
                    quality_flags.append(f"{n5} moves beyond 5 sigma")
    if ref_close is not None and cl[-1]:
        try:
            if abs(float(ref_close) - float(cl[-1])) / abs(float(cl[-1])) > 0.02:
                quality_flags.append("last close differs >2% from second source")
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    if (hi < lo).any():
        quality_flags.append("high<low bars present")
    # --- refusal: unexplained >50% overnight gaps (T09a) ---
    refused: list[str] = []
    split_dates = set(split_map)
    if n > 1:
        with np.errstate(divide="ignore", invalid="ignore"):
            gaps_chk = op[1:] / np.where(cl[:-1] != 0, cl[:-1], np.nan) - 1.0
        for t in range(len(gaps_chk)):
            if gaps_chk[t] != gaps_chk[t] or abs(gaps_chk[t]) <= 0.5:
                continue
            gdate = dates[t + 1]
            explained = any(abs((gdate - s).days) <= 3 for s in split_dates)
            if not explained:
                refused.append(gdate.isoformat())
    if refused:
        quality_flags.append("bad_data_possible_split at "
                             + ",".join(refused[:3]) + ": indicators refused")
    # --- price-basis detection (B1, T08) ---
    basis_levels = "split_adjusted"
    if has_adj:
        exdiv = _normalize_dates(ex_div_dates)
        if exdiv:
            auto_evidence = None
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.where(cl > 0, adj / np.where(cl > 0, cl, np.nan), np.nan)
            for d in exdiv:
                window = [i for i, dt in enumerate(dates)
                          if dt < d and (d - dt).days <= 60]
                window = [i for i in window if ratio[i] == ratio[i]]
                if window and all(abs(ratio[i] - 1.0) < 1e-9 for i in window):
                    auto_evidence = d.isoformat()
                    break
            if auto_evidence is not None:
                basis_levels = "adjusted_only"
                quality_flags.append(
                    "vendor auto-adjusted data (close==adj before ex-div "
                    f"{auto_evidence}): levels flagged")
                notes.append("price_basis.levels=adjusted_only: zones suppressed, "
                             "levels fields flagged as total-return based")

    last_bar = dates[-1]
    first_bar = dates[0]
    last_close = float(cl[-1])

    # --- indicators on levels (split-adjusted close) ---
    sma50, sma200 = _sma(cl, 50), _sma(cl, 200)
    slope200 = (sma200[-1] / sma200[-21] - 1.0) if n > 220 and sma200[-21] else float("nan")
    rsi14 = _rsi_wilder(cl)
    macd_v, macd_s, macd_h = _macd(cl)
    atr14 = _atr_wilder(hi, lo, cl)
    adx14, _, _, _ = _adx_wilder(hi, lo, cl)
    bb_u, bb_l, pct_b, bw, _ = _bollinger(cl)
    with np.errstate(invalid="ignore"):
        logret = np.log(adj[1:] / adj[:-1])
    logret = np.concatenate([[np.nan], np.where((adj[1:] > 0) & (adj[:-1] > 0), logret, np.nan)])
    rv21 = _realized_vol(logret, 21, annual)
    rv63 = _realized_vol(logret, 63, annual)
    rv252 = _realized_vol(logret, 252, annual)
    relvol = vo[-1] / np.nanmean(vo[-21:-1]) if n > 21 and np.nanmean(vo[-21:-1]) else float("nan")
    adv20 = float(np.nanmean(cl[-20:] * vo[-20:])) if n >= 20 else float("nan")

    # --- returns on total-return basis ---
    def _ret(k: int) -> float | None:
        if n > k and adj[-1 - k] and adj[-1 - k] > 0:
            return float(adj[-1] / adj[-1 - k] - 1.0)
        return None

    r1m, r3m, r6m, r12m = _ret(w1), _ret(w3), _ret(w6), _ret(w12)
    ytd = None
    try:
        prev_year_end = np.where(np.array([d.year for d in dates]) < dates[-1].year)[0]
        if prev_year_end.size:
            base = adj[prev_year_end[-1]]
            ytd = float(adj[-1] / base - 1.0) if base and base > 0 else None
    except Exception:
        pass
    mom_12_1 = float(adj[-w1] / adj[-w12] - 1.0) if n > w12 and adj[-w12] > 0 else None
    days_total = (dates[-1] - dates[0]).days
    cagr_full = _cagr(adj[0], adj[-1], days_total)
    cagr_3y = _cagr(adj[-756], adj[-1], (dates[-1] - dates[-756]).days) if n > 756 else None
    cagr_5y = _cagr(adj[-1260], adj[-1], (dates[-1] - dates[-1260]).days) if n > 1260 else None
    cagr_10y = _cagr(adj[-2520], adj[-1], (dates[-1] - dates[-2520]).days) if n > 2520 else None

    # --- drawdowns / distributions ---
    dd = _drawdown_series(adj)
    cur_dd = float(dd[-1]) if dd[-1] == dd[-1] else None
    max_dd = float(np.nanmin(dd)) if np.any(~np.isnan(dd)) else None
    underwater = 0
    for v in dd[::-1]:
        if v != v or v >= 0:
            break
        underwater += 1
    top5 = _top_drawdowns(dates, adj)
    dd_hist = dd[~np.isnan(dd)]
    cur_dd_pct = (float(100.0 * np.mean(dd_hist <= cur_dd))
                  if cur_dd is not None and dd_hist.size else None)
    dist_1y = _rolling_distribution(adj, w12)
    dist_3y = _rolling_distribution(adj, w12 * 3)
    if n >= 1800:
        dist_5y = _rolling_distribution(adj, w12 * 5)
    else:
        dist_5y = {"p5": None, "p25": None, "p50": None, "p75": None,
                   "p95": None, "current_pctile": None}
        notes.append("rolling 5y needs >=1800 bars: null")

    # --- 52w / ATH (levels) ---
    high52 = float(np.nanmax(hi[-251:])) if n >= 2 else None
    low52 = float(np.nanmin(lo[-251:])) if n >= 2 else None
    ath = float(np.nanmax(cl)) if n else None

    # --- extension / percentiles (need 756 bars) ---
    ext_atr = float((cl[-1] - sma50[-1]) / atr14[-1]) if atr14[-1] and atr14[-1] > 0 else float("nan")
    ext200 = float(cl[-1] / sma200[-1] - 1.0) if sma200[-1] else float("nan")
    hist_ok = n >= 756
    if not hist_ok:
        notes.append("history <756 bars: percentiles not computed")
        quality_flags.append("short_history (<756 bars)")
    ext_hist = ((cl - sma50) / atr14)[(3 * 50):] if hist_ok else np.array([])
    ext_pct = _percentile(ext_atr, ext_hist) if hist_ok else None
    bw_hist = bw[(3 * 20):] if hist_ok else np.array([])
    bw_pct = _percentile(bw[-1] if bw.size else float("nan"), bw_hist) if hist_ok else None
    rv_hist = rv63[(3 * 63):] if hist_ok else np.array([])
    rv_pct = _percentile(rv63[-1] if rv63.size else float("nan"), rv_hist) if hist_ok else None

    # --- regime + modifiers (no classification under ~1y of history) ---
    if n >= 252:
        regime = classify_regime(cl[-1], sma50[-1], sma200[-1], slope200)
    else:
        regime = ""
        notes.append("history <252 bars (~1y): no daily regime classification")
    mods: list[str] = []
    if (ext_pct is not None and ext_pct >= 95) or (ext_pct is None and ext_atr == ext_atr and ext_atr >= 5):
        mods.append("EXTENDED")
    if bw_pct is not None and bw_pct <= 10:
        mods.append("SQUEEZE")
    if rv_pct is not None and rv_pct >= 90:
        mods.append("HIGH_VOL")
    if cl[-1] == cl[-1] and sma200[-1] == sma200[-1] and cl[-1] < sma200[-1]:
        mods.append("BELOW_200")
    broke_out = broke_down = False
    if n > 60 and relvol == relvol:
        if cl[-1] > np.nanmax(hi[-56:-1]) and relvol >= 1.5:
            broke_out = True
        if cl[-1] < np.nanmin(lo[-56:-1]) and relvol >= 1.5:
            broke_down = True
    h55 = np.nanmax(hi[-56:-1]) if n > 56 else float("nan")
    l55 = np.nanmin(lo[-56:-1]) if n > 56 else float("nan")
    for t in range(max(0, n - 5), n):
        rv_t = vo[t] / np.nanmean(vo[max(0, t - 20):t]) if t > 0 else float("nan")
        if h55 == h55 and cl[t] > h55 and rv_t == rv_t and rv_t >= 1.5 and cl[-1] > h55:
            broke_out = True
        if l55 == l55 and cl[t] < l55 and rv_t == rv_t and rv_t >= 1.5 and cl[-1] < l55:
            broke_down = True
    if broke_out:
        mods.append("BREAKOUT")
    if broke_down:
        mods.append("BREAKDOWN")
    prev_close = np.concatenate([[np.nan], cl[:-1]])
    gaps = _significant_gaps(dates, op, prev_close, atr14)
    if any(not g["filled"] for g in gaps):
        mods.append("GAP_UNFILLED")

    # --- weekly / monthly ---
    weekly = _resample_weekly(frame)
    wcl = weekly["close"].to_numpy(dtype=float)
    w50 = _sma(wcl, 10)
    w200 = _sma(wcl, 40)
    wslope = (w200[-1] / w200[-5] - 1.0) if len(wcl) > 44 and w200[-5] else float("nan")
    if len(wcl) >= 40:
        wregime = classify_regime(wcl[-1] if len(wcl) else float("nan"),
                                  w50[-1] if len(w50) else float("nan"),
                                  w200[-1] if len(w200) else float("nan"), wslope)
    else:
        wregime = ""
        notes.append("weekly bars <40: no weekly regime classification")
    w_rsi = _rsi_wilder(wcl)
    w_m, w_s, w_h = _macd(wcl)
    monthly = _resample_monthly(frame)
    mcl = monthly["close"].to_numpy(dtype=float)
    sma10m = _sma(mcl, 10)
    if len(mcl) >= 13 and sma10m[-1] == sma10m[-1] and sma10m[-4] == sma10m[-4]:
        mstate = (("ABOVE_" if mcl[-1] > sma10m[-1] else "BELOW_")
                  + ("RISING" if sma10m[-1] > sma10m[-4] else "FALLING"))
    else:
        mstate = ""
        notes.append("monthly state unavailable (<13 bars)")

    # --- structure (suppressed when levels basis is not split-adjusted) ---
    lookback = frame.iloc[-504:] if n > 504 else frame
    ph, pl = _swing_pivots(lookback["high"].to_numpy(dtype=float),
                           lookback["low"].to_numpy(dtype=float), 5)
    ldates = [d.date() for d in lookback.index]
    if basis_levels != "split_adjusted":
        supports, resistances = [], []
        notes.append("support/resistance zones suppressed (levels basis "
                     f"{basis_levels})")
    else:
        supports = _zones(lookback["low"].to_numpy(dtype=float), pl, ldates,
                          float(atr14[-1]) if atr14[-1] == atr14[-1] else 0.0,
                          (lookback["volume"] / lookback["volume"].rolling(20).mean()).to_numpy(), "support")
        resistances = _zones(lookback["high"].to_numpy(dtype=float), ph, ldates,
                             float(atr14[-1]) if atr14[-1] == atr14[-1] else 0.0,
                             (lookback["volume"] / lookback["volume"].rolling(20).mean()).to_numpy(), "resistance")

    # --- benchmark-relative (optional) ---
    bench = _to_daily(benchmark) if benchmark is not None and not benchmark.empty else None
    bmeta = benchmark_meta or {}
    rel: dict[str, Any] = {"relret_3m": None, "relret_6m": None, "relret_12m": None,
                           "rs_vs_sma50": "", "rs_vs_sma200": "",
                           "beta_weekly": None, "corr_weekly": None, "n_weeks": 0,
                           "up_capture": None, "down_capture": None}
    stress: list[dict] = []
    if bench is None:
        notes.append("no benchmark: relative strength, beta, stress windows null")
    else:
        try:
            bcl = pd.to_numeric(bench["close"], errors="coerce")
            joined = pd.DataFrame({"a": adj, "b": bcl.reindex(frame.index)}).dropna()
            if str(bmeta.get("currency", "") or "").upper() != str(currency or "").upper():
                notes.append("benchmark currency differs: relative figures flagged")
            if len(joined) > w12:
                for key, kk in (("relret_3m", w3), ("relret_6m", w6), ("relret_12m", w12)):
                    if len(joined) > kk:
                        ra = joined["a"].iloc[-1] / joined["a"].iloc[-1 - kk] - 1.0
                        rb = joined["b"].iloc[-1] / joined["b"].iloc[-1 - kk] - 1.0
                        rel[key] = float((1 + ra) / (1 + rb) - 1.0)
                rs = (joined["a"] / joined["b"]).to_numpy()
                rs50, rs200 = _sma(rs, 50), _sma(rs, 200)
                rel["rs_vs_sma50"] = "above" if rs[-1] > rs50[-1] else "below"
                rel["rs_vs_sma200"] = "above" if rs[-1] > rs200[-1] else "below"
            # weekly beta/corr, >=52 weeks
            wj = pd.DataFrame({"a": frame["close"], "b": bcl}).dropna().resample("W-FRI").last().dropna()
            if len(wj) >= 52:
                use = wj.tail(156)
                ra_w = use["a"].pct_change().dropna().to_numpy()
                rb_w = use["b"].pct_change().dropna().to_numpy()
                m = min(len(ra_w), len(rb_w))
                ra_w, rb_w = ra_w[-m:], rb_w[-m:]
                if m >= 52 and np.var(rb_w) > 0:
                    rel["beta_weekly"] = float(np.cov(ra_w, rb_w)[0, 1] / np.var(rb_w, ddof=1))
                    rel["corr_weekly"] = float(np.corrcoef(ra_w, rb_w)[0, 1])
                    rel["n_weeks"] = int(m)
            # monthly up/down capture, >=36 months
            mj = pd.DataFrame({"a": frame["close"], "b": bcl}).dropna().resample("ME").last().dropna()
            if len(mj) >= 37:
                ma = mj["a"].pct_change().dropna()
                mb = mj["b"].pct_change().dropna()
                up_m = mb > 0
                if up_m.sum() > 0 and mb[up_m].mean() != 0:
                    rel["up_capture"] = float(ma[up_m].mean() / mb[up_m].mean())
                dn_m = mb < 0
                if dn_m.sum() > 0 and mb[dn_m].mean() != 0:
                    rel["down_capture"] = float(ma[dn_m].mean() / mb[dn_m].mean())
            # stress windows from the benchmark's own drawdowns >15%
            bclean = bcl.dropna().to_numpy()
            bdates = [d.date() for d in bcl.dropna().index]
            for dd_row in _top_drawdowns(bdates, bclean, max_n=10):
                if dd_row["depth"] is not None and dd_row["depth"] <= -0.15:
                    stress.append({"from": dd_row["peak_date"], "to": dd_row["trough_date"],
                                   "benchmark_dd": dd_row["depth"], "asset_dd": None})
        except Exception as exc:
            notes.append(f"benchmark block failed: {type(exc).__name__}")

    # --- base-currency view (optional FX) ---
    base_ret: dict[str, Any] = {"r12m": None, "fx_contribution_r12m": None}
    adv_base = None
    if fx is not None:
        try:
            fxj = pd.Series(fx).reindex(frame.index).ffill().to_numpy(dtype=float)
            adj_base = adj * fxj
            if n > w12 and adj_base[-1 - w12] and adj_base[-1 - w12] > 0:
                base_ret["r12m"] = float(adj_base[-1] / adj_base[-1 - w12] - 1.0)
                if r12m is not None:
                    base_ret["fx_contribution_r12m"] = float(base_ret["r12m"] - r12m)
            adv_base = float(np.nanmean(adj_base[-20:] * vo[-20:] / fxj[-20:])) if n >= 20 else None
        except Exception as exc:
            notes.append(f"fx block failed: {type(exc).__name__}")
    else:
        notes.append("no fx series: base-currency view null")

    # --- attribution (optional EPS history) ---
    attribution = {"eps_basis": "", "eps_factor": None, "pe_factor": None,
                   "dividend_component": None}
    if eps:
        notes.append("eps history supplied but attribution needs two dated points: null")
    else:
        notes.append("no eps history: attribution null")

    # --- events passthrough ---
    clean_events: list[dict] = []
    for ev in events or []:
        if isinstance(ev, dict) and ev.get("date"):
            clean_events.append({"date": str(ev["date"])[:10],
                                 "type": str(ev.get("type", "")),
                                 "asset_return": _num(ev.get("asset_return")),
                                 "benchmark_return": _num(ev.get("benchmark_return")),
                                 "source": str(ev.get("source", ""))})

    snap = {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": now_utc.isoformat(),
        "asset": {"name": name, "isin": isin, "ticker": symbol,
                  "exchange": exchange, "currency": currency,
                  "price_unit": price_unit,
                  "benchmark": {"name": str(bmeta.get("name", "")),
                                "ticker": str(bmeta.get("ticker", "")),
                                "currency": str(bmeta.get("currency", ""))}},
        "data": {"source": source, "first_bar": first_bar.isoformat(),
                 "last_bar_in_data": last_bar.isoformat(),
                 "last_complete_bar": last_bar.isoformat(), "n_bars": n,
                 "price_basis": {"levels": basis_levels,
                                 "returns": "total_return" if has_adj else "price_only"},
                 "quality_flags": quality_flags, "notes": notes},
        "daily": {
            "close": _num(last_close), "sma50": _num(sma50[-1]), "sma200": _num(sma200[-1]),
            "slope200_20d": _num(slope200),
            "rsi14": _num(rsi14[-1]),
            "macd": {"macd": _num(macd_v[-1]), "signal": _num(macd_s[-1]), "hist": _num(macd_h[-1])},
            "atr14": _num(atr14[-1]),
            "atr_pct": _num(atr14[-1] / last_close) if last_close else None,
            "adx14": _num(adx14[-1]),
            "bollinger": {"upper": _num(bb_u[-1]), "lower": _num(bb_l[-1]),
                          "pct_b": _num(pct_b[-1]), "bandwidth": _num(bw[-1]),
                          "bandwidth_pctile_252": bw_pct},
            "ext_atr_from_sma50": _num(ext_atr), "ext_pct_from_sma200": _num(ext200),
            "ext_atr_pctile": ext_pct,
            "vol": {"rv21": _num(rv21[-1]), "rv63": _num(rv63[-1]), "rv252": _num(rv252[-1]),
                    "rv63_pctile": rv_pct},
            "relvol": _num(relvol), "adv20_value": _num(adv20),
            "adv20_value_base_ccy": _num(adv_base),
            "high52": _num(high52), "low52": _num(low52),
            "dist_high52_pct": _num(last_close / high52 - 1.0) if high52 else None,
            "dist_low52_pct": _num(last_close / low52 - 1.0) if low52 else None,
            "ath": _num(ath),
            "dist_ath_pct": _num(last_close / ath - 1.0) if ath else None,
            "regime": regime, "modifiers": mods,
        },
        "weekly": {
            "close": _num(wcl[-1]) if len(wcl) else None,
            "sma10w": _num(w50[-1]) if len(w50) else None,
            "sma40w": _num(w200[-1]) if len(w200) else None,
            "slope40w_4w": _num(wslope),
            "rsi14": _num(_rsi_wilder(wcl)[-1]) if len(wcl) else None,
            "macd": {"macd": _num(w_m[-1]) if len(wcl) else None,
                     "signal": _num(w_s[-1]) if len(wcl) else None,
                     "hist": _num(w_h[-1]) if len(wcl) else None},
            "regime": wregime, "modifiers": [],
        },
        "monthly": {"close": _num(mcl[-1]) if len(mcl) else None,
                    "sma10m": _num(sma10m[-1]) if len(sma10m) else None,
                    "state": mstate},
        "returns": {
            "r1m": r1m, "r3m": r3m, "r6m": r6m, "r12m": r12m, "ytd": ytd,
            "mom_12_1": mom_12_1, "cagr_since_first_bar": cagr_full,
            "cagr_3y": cagr_3y, "cagr_5y": cagr_5y, "cagr_10y": cagr_10y,
            "base_currency_total_return": base_ret,
        },
        "relative": rel,
        "drawdown": {"current": cur_dd, "current_pctile": cur_dd_pct, "max": max_dd,
                     "underwater_bars": underwater, "top5": top5},
        "distributions": {"rolling_1y": dist_1y, "rolling_3y": dist_3y,
                          "rolling_5y": dist_5y,
                          "effective_sample_note": "overlapping windows: effective sample ~N/window"},
        "levels": {"support": supports, "resistance": resistances},
        "gaps": gaps,
        "events": clean_events,
        "stress_windows": stress,
        "attribution": attribution,
        "tests": run_snapshot_selftests(),
        "files": {"raw_data": raw_path, "charts": list(chart_paths or [])},
    }
    if refused:
        # Bad-data refusal (T09a): indicators, regimes and levels are nulled;
        # returns/drawdowns/gaps stay as flagged evidence.
        notes.append("indicators refused: bad_data_possible_split (see quality_flags)")
        snap["data"]["notes"] = notes
        daily = snap["daily"]
        for key in ("sma50", "sma200", "slope200_20d", "rsi14", "atr14",
                    "atr_pct", "adx14", "ext_atr_from_sma50", "ext_pct_from_sma200",
                    "ext_atr_pctile", "relvol", "adv20_value",
                    "adv20_value_base_ccy", "high52", "low52",
                    "dist_high52_pct", "dist_low52_pct", "ath", "dist_ath_pct"):
            daily[key] = None
        daily["macd"] = {"macd": None, "signal": None, "hist": None}
        daily["bollinger"] = {"upper": None, "lower": None, "pct_b": None,
                              "bandwidth": None, "bandwidth_pctile_252": None}
        daily["vol"] = {"rv21": None, "rv63": None, "rv252": None, "rv63_pctile": None}
        daily["regime"] = ""
        daily["modifiers"] = []
        snap["weekly"]["regime"] = ""
        for key in ("sma10w", "sma40w", "slope40w_4w", "rsi14"):
            snap["weekly"][key] = None
        snap["weekly"]["macd"] = {"macd": None, "signal": None, "hist": None}
        snap["monthly"]["state"] = ""
        snap["monthly"]["sma10m"] = None
        snap["levels"] = {"support": [], "resistance": []}
    return snap


def _empty_snapshot(symbol: str, name: str, isin: str, exchange: str, currency: str,
                    price_unit: str, asset_class: str, source: str, now_utc: datetime,
                    raw_path: str, chart_paths: list, notes: list[str]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION, "generated_utc": now_utc.isoformat(),
        "asset": {"name": name, "isin": isin, "ticker": symbol, "exchange": exchange,
                  "currency": currency, "price_unit": price_unit,
                  "benchmark": {"name": "", "ticker": "", "currency": ""}},
        "data": {"source": source, "first_bar": "", "last_bar_in_data": "",
                 "last_complete_bar": "", "n_bars": 0,
                 "price_basis": {"levels": "unknown", "returns": "unknown"},
                 "quality_flags": ["insufficient data"], "notes": notes},
        "daily": {}, "weekly": {}, "monthly": {}, "returns": {}, "relative": {},
        "drawdown": {}, "distributions": {}, "levels": {}, "gaps": [],
        "events": [], "stress_windows": [], "attribution": {},
        "tests": {"passed": None, "failed": []},
        "files": {"raw_data": raw_path, "charts": list(chart_paths)},
    }
