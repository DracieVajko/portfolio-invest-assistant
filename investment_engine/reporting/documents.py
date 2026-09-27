"""Two-document reporting: decision brief (concise, daily) + T212 snapshot (full).

The brief never renders a full holdings table and carries no sized orders;
the snapshot is the complete financial inventory. All sections are built
deterministically from canonical rows — no LLM prose enters either document.
"""

from __future__ import annotations

import re
from datetime import date as _date
from typing import Any, Dict, List, Optional

from investment_engine.portfolio.symbols import resolve_company_name
from investment_engine.reporting.regime_report import normalize_signal, signal_emoji


# Urgency weights (higher = more urgent). No hard item cap: every triggered
# position is listed, sorted by urgency descending.
_SCORE_SELL_SIGNAL = 100
_SCORE_BREACH = 80
_SCORE_EARNINGS_7D = 70
_SCORE_WEIGHT = 60
_SCORE_BREAKOUT = 55
_SCORE_PNL = 50
_SCORE_RSI = 40
_SCORE_NEAR_LEVEL = 30
_SCORE_PIE_WARNING = 25
_SCORE_UNAVAILABLE = 20


def _fnum(value: Any, fmt: str = "{:.2f}") -> str:
    try:
        import math as _math
        f = float(value) if value is not None else None
        # float("nan")/float("inf") format as "nan"/"inf" — never render those
        # (run 524fffa0 snapshot: ESIF support/resistance showed nan).
        if f is None or _math.isnan(f) or _math.isinf(f):
            return "n/a"
        return fmt.format(f)
    except (TypeError, ValueError):
        return "n/a"


def _feur(value: Any) -> str:
    try:
        import math as _math
        f = float(value) if value is not None else None
        if f is None or _math.isnan(f) or _math.isinf(f):
            return "n/a"
        return f"€{f:,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _rsi_of(technicals: dict | None, display: str) -> float | None:
    try:
        import math as _math
        ind = (technicals or {}).get(display)
        rsi = (ind or {}).get("RSI_14") if isinstance(ind, dict) else None
        f = float(rsi) if rsi is not None else None
        if f is None or _math.isnan(f) or _math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _days_to_earnings(earnings_status: dict | None, display: str, report_date) -> tuple:
    """(days, date_iso) when an earnings date exists, else (None, '')."""
    try:
        val = (earnings_status or {}).get(display)
        d = val[0][:10] if isinstance(val, (tuple, list)) and val and isinstance(val[0], str) else None
        if not d:
            return None, ""
        delta = (_date.fromisoformat(d) - report_date).days
        return delta, d
    except (ValueError, TypeError):
        return None, ""


def build_monitoring_items(
    rows: list[dict] | None,
    *,
    earnings_status: dict | None = None,
    technicals: dict | None = None,
    pie_warnings: dict | None = None,
    recon_status: str = "UNKNOWN",
    report_date=None,
    canonical_map: dict | None = None,
) -> list[dict]:
    """Build urgency-sorted monitoring items for every triggered owned position.

    No item cap. In FAIL state owned positions stay canonical HOLD unless a
    risk-reduction SELL rule fires (weight>=5% or P&L<=-8% or price below
    support); urgent HOLDs present as REVIEW (presentation-only).
    
    Technical comparisons (support/resistance breach) are ONLY performed when:
    - external_mapping_status == "VERIFIED"
    - broker_quote_currency == external_quote_currency
    - broker_current_price_native is not None
    - external_support_native is not None
    """
    from datetime import date as _d
    if report_date is None:
        try:
            from investment_engine.research.market_data import report_now_bta
            report_date = report_now_bta().date()
        except Exception:
            report_date = _d.today()
    fail = str(recon_status or "").upper() != "PASS"
    items: list[dict] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        disp = str(r.get("display_symbol") or r.get("ticker") or "").strip().upper()
        if not disp or disp == "UNKNOWN":
            continue
        canon_sig = normalize_signal(r.get("signal", "HOLD"))
        entry = (canonical_map or {}).get(disp) if isinstance(canonical_map, dict) else None
        if isinstance(entry, dict) and entry.get("signal") in ("BUY", "SELL", "HOLD"):
            canon_sig = entry["signal"]
        triggers: list[tuple[int, str]] = []
        if canon_sig == "SELL":
            triggers.append((_SCORE_SELL_SIGNAL, "canonical SELL/REDUCE/TRIM signal"))
        try:
            weight = float(r.get("weight", 0) or 0)
        except (TypeError, ValueError):
            weight = 0.0
        if weight >= 5.0:
            triggers.append((_SCORE_WEIGHT, f"weight {weight:.1f}% ≥ 5%"))
        try:
            pnl = float(r.get("pnl_pct", 0) or 0)
        except (TypeError, ValueError):
            pnl = 0.0
        if pnl <= -8.0 or pnl >= 15.0:
            triggers.append((_SCORE_PNL, f"P&L {pnl:+.1f}% outside −8%/+15% band"))
        rsi = _rsi_of(technicals, disp)
        if rsi is not None and (rsi <= 25 or rsi >= 75):
            triggers.append((_SCORE_RSI, f"RSI {rsi:.1f} extreme"))
        
        # --- Technical comparison gate ---
        # Only compare if mapping is VERIFIED and currencies match
        ext_mapping_status = str(r.get("external_mapping_status", r.get("market_data_state", ""))).upper()
        broker_quote_ccy = str(r.get("quote_currency", r.get("broker_quote_currency", ""))).upper()
        ext_quote_ccy = str(r.get("external_quote_currency", "")).upper()
        # Get native prices (not EUR-converted)
        broker_price_native = r.get("current_price_native")
        if broker_price_native is None:
            # Fallback: if broker_price_eur and fx_rate available, reverse-calculate
            # But prefer native price field
            pass
        ext_support_native = r.get("support")
        ext_resistance_native = r.get("resistance")
        sup = None
        res = None
        
        technical_levels_allowed = (
            ext_mapping_status == "VERIFIED"
            and broker_quote_ccy
            and broker_quote_ccy == ext_quote_ccy
            and broker_price_native is not None
            and ext_support_native is not None
        )
        
        if technical_levels_allowed:
            try:
                px = float(broker_price_native)
                sup = float(ext_support_native) if ext_support_native is not None else None
                res = float(ext_resistance_native) if ext_resistance_native is not None else None
                
                breached = False
                if sup is not None and px > 0 and px < sup:
                    triggers.append((_SCORE_BREACH, f"stop-loss/invalidation breach: price {px:.2f} {broker_quote_ccy} below support {sup:.2f} {broker_quote_ccy}"))
                    breached = True
                elif res is not None and px > 0 and px > res:
                    triggers.append((_SCORE_BREAKOUT, f"breakout above resistance {res:.2f} {broker_quote_ccy}"))
                
                for lvl, lname in ((sup, "support"), (res, "resistance")):
                    try:
                        if lvl is not None and px > 0 and not breached and abs(px - lvl) / px <= 0.015:
                            triggers.append((_SCORE_NEAR_LEVEL, f"price within 1.5% of {lname} {lvl:.2f} {broker_quote_ccy}"))
                            break
                    except (TypeError, ValueError, ZeroDivisionError):
                        continue
            except (TypeError, ValueError):
                pass
        else:
            # Technical levels not comparable - add REVIEW_MAPPING flag
            if ext_mapping_status in ("MAPPING_SUSPECT", "UNRESOLVED", "BROKER_ONLY", "NOT_REQUIRED"):
                triggers.append((_SCORE_UNAVAILABLE, f"technical levels suppressed: mapping_status={ext_mapping_status}"))
        
        delta, earn_date = _days_to_earnings(earnings_status, disp, report_date)
        if delta is not None and 0 <= delta <= 7:
            triggers.append((_SCORE_EARNINGS_7D, f"earnings in {delta}d ({earn_date})"))
        if str(r.get("market_data_state", "") or "").upper() == "UNRESOLVED" and weight >= 2.0:
            triggers.append((_SCORE_UNAVAILABLE, f"market data unavailable (weight {weight:.1f}%)"))
        pie_note = (pie_warnings or {}).get(disp)
        if pie_note:
            triggers.append((_SCORE_PIE_WARNING, str(pie_note)))
        if not triggers:
            continue
        urgency = max(s for s, _ in triggers)
        why = "; ".join(t for _, t in sorted(triggers, key=lambda x: -x[0]))
        # Presentation status.
        # `breached` is only defined when technical_levels_allowed is True
        breached = False  # default
        risk_sell_rule = canon_sig == "SELL" and (weight >= 5.0 or pnl <= -8.0 or breached)
        if canon_sig == "SELL" and (not fail or risk_sell_rule):
            presentation = "SELL"
            action = (f"Risk-reduction review: consider trimming {disp} per stop discipline; "
                      f"no quantity in brief." if not fail else
                      f"Risk-reduction candidate under FAIL: manual review only, no automatic action.")
        elif fail:
            presentation = "REVIEW"
            action = "Review only; no transaction authorized."
        else:
            presentation = "HOLD"
            if canon_sig == "BUY":
                action = "Accumulation candidate — no sized order in this brief."
            else:
                action = "No action; hold position."
        try:
            mval = float(r.get("market_value", 0) or 0)
        except (TypeError, ValueError):
            mval = 0.0
        items.append({
            "display": disp,
            "company": str(r.get("company") or r.get("company_name") or disp),
            "presentation": presentation,
            "canonical": canon_sig,
            "triggers": why,
            "urgency": urgency,
            "market_value": mval,
            "weight": weight,
            "support": sup,
            "resistance": res,
            "earnings_date": earn_date if (delta is not None and 0 <= delta <= 7) else "",
            "action": action,
        })
    items.sort(key=lambda i: (-i["urgency"], -i["market_value"], i["display"]))
    return items


def extract_validated_ideas(
    candidate_section: str | None,
    news_by_symbol: dict | None,
    resolver=None,
) -> list[dict]:
    """Structured watchlist ideas: validated ticker + evidence + source URL.

    A bullet passes with a resolver-verified ticker (supported market data),
    at least one headline evidence with a valid URL, and a stated reason.
    Confidence high with ≥2 evidences, else medium.
    """
    headlines: list[tuple[str, str, str]] = []
    for _sym, entries in (news_by_symbol or {}).items():
        for e in entries or []:
            if isinstance(e, dict) and e.get("title") and e.get("url"):
                headlines.append((str(e["title"]), str(e["url"])))
    found: dict[str, dict] = {}
    for line in (candidate_section or "").splitlines():
        s = line.strip()
        if not s.startswith(("-", "*")):
            continue
        tickers = [t for t in re.findall(r"\b[A-Z]{2,6}\b", s)
                   if t not in ("BUY", "WATCH", "SELL", "HOLD", "THE", "AND", "FOR", "NOT")]
        reason = re.sub(r"^[-*]\s*", "", s)[:200]
        for t in tickers:
            if resolver is not None:
                try:
                    if not resolver(t):
                        continue
                except Exception:
                    continue
            ev = [(title, url) for title, url in headlines if t in title.upper()]
            if not ev:
                continue
            entry = found.setdefault(t, {"evidence": [], "urls": [], "reason": reason})
            for title, url in ev:
                if title not in entry["evidence"]:
                    entry["evidence"].append(title)
                if url not in entry["urls"]:
                    entry["urls"].append(url)
    ideas = []
    for t, info in sorted(found.items()):
        ideas.append({"ticker": t, "reason": info["reason"],
                      "evidence": info["evidence"][0][:140], "url": info["urls"][0],
                      "confidence": "high" if len(info["evidence"]) >= 2 else "medium"})
    return ideas[:5]


def _stance(rows, recon_status: str) -> str:
    if str(recon_status or "").upper() != "PASS":
        return "DEGRADED"
    buys = sum(1 for r in rows or [] if normalize_signal((r or {}).get("signal")) == "BUY")
    sells = sum(1 for r in rows or [] if normalize_signal((r or {}).get("signal")) == "SELL")
    if sells > 0 and sells >= buys:
        return "SELL"
    if buys > sells:
        return "BUY"
    return "HOLD"


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _mday(date_iso: str) -> str:
    """YYYY-MM-DD -> 'MMM DD' without locale dependence."""
    try:
        y, m, d = date_iso[:10].split("-")
        return f"{_MONTHS[int(m) - 1]} {int(d):02d}"
    except (ValueError, TypeError, IndexError):
        return date_iso


def portfolio_stance(rows, recon_status: str) -> str:
    """Single portfolio-stance source of truth (fail-safe: non-PASS → DEGRADED)."""
    return _stance(rows, recon_status)


def build_intelligence_brief(result: dict) -> str:
    """Concise human portfolio-intelligence briefing (Phase 4 target brief).

    Deterministic presentation filter over already-computed data. Content rules:
    - max 60 lines; short sections, no per-ticker prose duplication;
    - the account-level warning (FAIL/DEGRADED) appears exactly ONCE;
    - never contains sized orders, prices invented by prose, or trade sizes;
    - models collapsed to one compact line (full attribution lives in the
      manifest + decision-brief methodology section).
    """
    rows = result.get("portfolio_rows", []) or []
    recon = result.get("reconciliation") or {}
    recon_status = str(recon.get("status", "UNKNOWN")).upper()
    regime_result = result.get("regime_result")
    regime = getattr(regime_result, "regime", "UNKNOWN") if regime_result else "UNKNOWN"
    stance = portfolio_stance(rows, recon_status)

    def _num(value):
        try:
            return float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    summary = (result.get("t212_data", {}) or {}).get("account_summary", {}) or {}
    cash = (result.get("t212_data", {}) or {}).get("cash", {}) or {}
    equity = _num(summary.get("total_equity", 0))
    free_cash = _num(cash.get("free", 0))
    perf = result.get("account_performance", {}) or {}
    net_pnl = _num(perf.get("net_pnl_after_costs_eur", 0))
    ret_pct = _num(perf.get("return_pct", 0))

    lines = [
        "# Portfolio Intelligence Brief",
        "",
        f"Stance: **{stance}** | Regime: **{regime}** | Reconciliation: **{recon_status}**",
    ]
    for mode_line in (result.get("execution_mode_lines", []) or []):
        lines.append(str(mode_line))
    if recon_status in ("FAIL", "DEGRADED"):
        lines.append("> Account data quality is degraded — deployment withheld; positions individually remain broker data.")
    lines.extend([
        f"Equity: €{equity:,.2f} | Net P&L: €{net_pnl:+,.2f} ({ret_pct:+.2f}%) | Free cash: €{free_cash:,.2f}",
        "",
        "## Priority attention",
    ])
    shown = 0
    for item in (result.get("monitoring_items", []) or []):
        if shown >= 5:
            break
        if not isinstance(item, dict):
            continue
        disp = str(item.get("display", "")).strip().upper()
        pres = str(item.get("presentation", item.get("action", ""))).strip()
        trig = str(item.get("triggers", "") or "").strip()
        try:
            _w = float(item.get("weight", 0) or 0)
            wtxt = f"; {_w:.1f}% weight"
        except (TypeError, ValueError):
            wtxt = ""
        if disp and pres:
            why = f" — {trig[:130]}" if trig else ""
            lines.append(f"- **{disp}** — {pres[:100]}{why}{wtxt}")
            shown += 1
    if not shown:
        lines.append("No positions require attention this run.")
    lines.append("")
    lines.append("## News & earnings")
    research_note = str(result.get("research_note", "") or "")
    if research_note:
        lines.append(research_note)
    news_items = result.get("decision_news", []) or []
    if news_items:
        for item in news_items[:3]:
            if isinstance(item, dict):
                lines.append(f"- {str(item.get('title', ''))[:110]} ({item.get('source', '')})")
    else:
        lines.append("No decision-relevant news in the last 48 hours.")
    earnings = (result.get("earnings_7d", {}) or {}).get("in_window", []) or []
    if earnings:
        for ev in earnings[:5]:
            if isinstance(ev, dict):
                tag = "(confirmed)" if ev.get("status") == "confirmed" else "(estimated)"
                lines.append(f"- {ev.get('display', '')}: earnings {tag}")
    else:
        lines.append("No upcoming earnings within 7 days.")
    ideas = result.get("ideas", []) or []
    if ideas:
        lines.append("")
        lines.append("## Watchlist")
        for idea in ideas[:3]:
            if isinstance(idea, dict):
                lines.append(f"- **{idea.get('ticker', '')}** — {str(idea.get('reason', ''))[:120]}")
    stages = result.get("providers_per_stage") or {}
    if stages:
        try:
            from investment_engine.providers.attribution import format_stage_line as _fmt_stage
        except Exception:
            _fmt_stage = None
        if _fmt_stage is not None:
            winners = "; ".join(_fmt_stage(s, st) for s, st in sorted(stages.items()))
        else:
            winners = ", ".join(f"{s}={(st or {}).get('provider', '?')}" for s, st in sorted(stages.items()))
        lines.extend(["", f"Models: {winners}"])
    # When research is empty the brief still answers "what moves this":
    # top weights + regime + recon + data provenance (never invented).
    if not news_items and not earnings and not ideas:
        lines.extend(["", "## Portfolio drivers (no fresh catalysts)"])
        _top = sorted(((float(r.get("weight", 0) or 0) if isinstance(r, dict) else 0,
                        str(r.get("display_symbol") or r.get("ticker") or "?") if isinstance(r, dict) else "?")
                       for r in rows), reverse=True)[:3]
        if _top and _top[0][0] > 0:
            lines.append("- Largest weights: " + ", ".join(f"{d} {w:.1f}%" for w, d in _top) + ".")
        try:
            _delta = float(recon.get("cash_delta", recon.get("reconciliation_delta_eur", 0)) or 0)
        except (TypeError, ValueError):
            _delta = 0.0
        lines.append(f"- Regime {regime}, reconciliation {recon_status} (Δ €{_delta:+.2f}).")
        t212 = result.get("t212_data", {}) or {}
        _fx_src = ((t212.get("fx_rates_used", {}) or {}).get("source", "") or "broker")
        lines.append(f"- Prices/P&L: broker truth; FX: {_fx_src}; "
                     f"free cash €{free_cash:,.2f}.")
    lines.extend([
        "",
        "Details: t212_portfolio_snapshot.md (full broker-first positions) + "
        "portfolio_deep_dive.md (15-section analysis).",
    ])
    brief = "\n".join(lines)
    if len(brief.splitlines()) > 60:
        brief = "\n".join(brief.splitlines()[:60])
    return brief


def _perf_eur(value: Any) -> str:
    if value is None:
        return "Unavailable"
    try:
        return f"€{float(value):+,.2f}"
    except (TypeError, ValueError):
        return "Unavailable"


def _perf_pct(value: Any) -> str:
    if value is None:
        return "Unavailable"
    try:
        return f"{float(value):+.4f}%"
    except (TypeError, ValueError):
        return "Unavailable"


def _perf_alloc(value: Any) -> str:
    if value is None:
        return "Unavailable"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "Unavailable"


def render_brief(
    *,
    generated_at: str,
    regime_result,
    recon: dict,
    rows: list[dict],
    monitoring_items: list[dict],
    earnings_7d: dict,
    decision_news: list[dict],
    ideas: list[dict],
    cash_line: str,
    performance: dict | None = None,
    research_note: str = "",
    execution_mode_lines: list[str] | None = None,
    order_plans: dict | None = None,
) -> str:
    """Render the concise decision-first brief (exact section order).

    Optional research_note renders in the news section (coverage warning);
    optional execution_mode_lines render under the header (API-only mode).
    Both default to absent so existing callers are byte-identical.
    """
    from investment_engine.accounting.cashflows import FAIL_PERFORMANCE_NOTE
    status = recon.get("status", "UNKNOWN")
    regime = getattr(regime_result, "regime", "UNKNOWN") if regime_result is not None else "UNKNOWN"
    try:
        rsi = regime_result.timeframes.get("daily", {}).get("indicators", {}).get("RSI_14", 0)
        regime_line = f"{regime} (RSI {float(rsi):.0f})"
    except Exception:
        regime_line = str(regime)
    if status == "FAIL":
        quality = (f"Data quality: **{status}** — account totals withheld; "
                   f"{len(rows or [])} validated positions shown.")
        constraint = "Reconciliation FAIL blocks all deployment and new buys."
    else:
        quality = (f"Data quality: **{status}** — reconciled; {len(rows or [])} positions.")
        top_w = max([float((r or {}).get("weight", 0) or 0) for r in (rows or [])] or [0.0])
        constraint = ("Single-name concentration caps new adds." if top_w >= 7.0
                      else "No binding constraint; normal sizing rules apply.")
    perf = performance or {}
    perf_ok = perf.get("performance_status") == "Available"
    lines = [
        "# Portfolio Decision Brief", "",
        f"Generated: {generated_at}",
    ]
    for mode_line in (execution_mode_lines or []):
        lines.append(str(mode_line))
    lines += [
        quality, "",
        "## Executive Decision", "",
        f"- Portfolio stance: **{_stance(rows, status)}**",
        f"- Market regime: **{regime_line}**",
        f"- Account reconciliation: **{status}**",
        f"- Broker total equity: {_feur(perf.get('broker_total_equity_eur', (recon or {}).get('total_equity')))}",
        f"- Net deposits: {_feur(perf.get('net_deposits_eur'))} "
        f"({perf.get('net_deposits_source', 'Unavailable — no verified cash-flow source')} / "
        f"{perf.get('net_deposits_status', 'UNAVAILABLE')})",
        f"- Net P&L after costs: {_perf_eur(perf.get('net_pnl_after_costs_eur')) if perf_ok else 'Unavailable'}",
        f"- Return on net deposits: {_perf_pct(perf.get('return_pct')) if perf_ok else 'Unavailable'}",
        f"- Reported free cash: {_feur(perf.get('reported_free_cash_eur'))}",
        f"- Total reported cash: {_feur(perf.get('reported_cash_eur'))}",
        f"- Cash allocation: {_perf_alloc(perf.get('cash_allocation_pct'))}",
        f"- Cash/deployment: {cash_line}",
        f"- Key safety constraint: {constraint}",
    ]
    if status == "FAIL":
        lines += ["", f"> {FAIL_PERFORMANCE_NOTE}"]
    lines += ["", "## Portfolio Monitoring", ""]
    if not monitoring_items:
        lines.append("No owned positions meet urgency criteria.")
    for it in monitoring_items:
        lines.append(f"- **{it['display']}**, {it['company']} — **{it['presentation']}** "
                     f"(canonical {it['canonical']}) — {it['triggers']}.")
        sr = []
        if it.get("support") is not None:
            sr.append(f"support {_fnum(it['support'])}")
        if it.get("resistance") is not None:
            sr.append(f"resistance {_fnum(it['resistance'])}")
        if sr:
            lines.append(f"  - Levels: {' / '.join(sr)}.")
        if it.get("earnings_date"):
            lines.append(f"  - Earnings: {it['earnings_date']} (within 7 days).")
        lines.append(f"  - Action: {it['action']}")
    lines += ["", "## Earnings — Next 7 Days", ""]
    window = (earnings_7d or {}).get("in_window", []) if isinstance(earnings_7d, dict) else []
    if not window:
        lines.append("No relevant earnings events in the next 7 days.")
    else:
        for ev in window:
            tag = "(confirmed)" if ev.get("status") == "confirmed" else "(estimated)"
            lines.append(f"- {_mday(ev['date'])} — {ev['display']}, {ev['company']} — earnings {tag}")
    nxt = (earnings_7d or {}).get("next_after") if isinstance(earnings_7d, dict) else None
    if isinstance(nxt, dict) and nxt.get("date"):
        tag = "(confirmed)" if nxt.get("status") == "confirmed" else "(estimated)"
        lines.append(f"Next relevant earnings after this window: {_mday(nxt['date'])} — "
                     f"{nxt['display']}, {nxt['company']} — earnings {tag}.")
    lines += ["", "## Decision-Relevant News — Last 48 Hours", ""]
    if research_note:
        lines.append(str(research_note))
    if not decision_news:
        if not research_note:
            lines.append("No decision-relevant verified news in the last 48 hours.")
    else:
        for n in decision_news:
            lines.append(f"- [{n['title']}]({n['url']}) — {n['source']} · {n.get('published', '')} ({n.get('age', '')})")
            lines.append(f"  - {n.get('why', '')}")
    lines += ["", "## Watchlist Candidates", ""]
    if status == "FAIL":
        lines.append("> Research-only watchlist; no deployment is authorized while account reconciliation is failing.")
        lines.append("")
    if not ideas:
        lines.append("No new ideas met the current evidence and validation threshold.")
    else:
        for idea in ideas:
            lines.append(f"- **{idea['ticker']}** — WATCH — {idea['reason']}")
            lines.append(f"  - Evidence: {idea['evidence']} ({idea['url']}) [confidence: {idea['confidence']}]")
    # Advisory order plans: rendered ONLY when gated plans exist (policy set +
    # all gates passed). Always advisory-only, never executable.
    _plans = (order_plans or {}).get("plans", []) or [] if isinstance(order_plans, dict) else []
    if _plans and str(status or "").upper() != "FAIL":
        lines += ["", "## Advisory Order Plans (not executed)", "",
                  "> Advisory only — not executed. Confirm manually outside this program.", ""]
        for plan in _plans:
            if not isinstance(plan, dict):
                continue
            lines.append(
                f"- **{plan.get('ticker')}** {plan.get('direction')} "
                f"~€{float(plan.get('indicative_notional_eur', 0) or 0):,.2f} "
                f"(binding: {plan.get('binding_constraint')}, sleeve: {plan.get('sleeve')})")
    return "\n".join(lines).rstrip() + "\n"


def _group_rows_by_isin(rows: list[dict] | None) -> list[tuple[str, dict, list[dict]]]:
    """Group unified rows by ISIN (fallback: broker internal ID).

    Returns [(group_key, primary_row, lot_rows)] with groups sorted by market
    value descending. The primary row is the highest-value lot; aggregates
    (qty/value/P&L/weight) are exact sums. Rows without ISIN never merge.
    Deterministic, pure, no network.
    """
    groups: dict[tuple, dict] = {}
    order: list[tuple] = []

    def _num(value):
        try:
            return float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    for r in rows or []:
        if not isinstance(r, dict):
            continue
        isin = str(r.get("isin") or "").strip().upper()
        if isin:
            key = ("ISIN", isin)
        else:
            key = ("ID", str(r.get("internal_id") or r.get("display_symbol") or r.get("ticker") or "?"))
        if key not in groups:
            groups[key] = {"rows": [], "value": 0.0}
            order.append(key)
        groups[key]["rows"].append(r)
        groups[key]["value"] += _num(r.get("market_value"))

    out: list[tuple[str, dict, list[dict]]] = []
    for key in sorted(order, key=lambda k: groups[k]["value"], reverse=True):
        lots = sorted(groups[key]["rows"],
                      key=lambda r: _num(r.get("market_value")), reverse=True)
        if len(lots) > 1:
            # Same ISIN = same company: backfill lots whose name resolution
            # missed (e.g. EUR-duplicate displays absent from name maps).
            _best_company = next(
                (str(r.get("company") or "").strip() for r in lots
                 if str(r.get("company") or "").strip()
                 and str(r.get("company")).strip().lower() not in ("unknown instrument",)),
                "")
            if _best_company:
                lots = [dict(r) if str(r.get("company") or "").strip() not in ("", "Unknown instrument")
                        else dict(r, company=_best_company) for r in lots]
        primary = dict(lots[0])
        if len(lots) > 1:
            primary = dict(primary)
            primary["quantity"] = sum(_num(r.get("quantity")) for r in lots)
            primary["market_value"] = sum(_num(r.get("market_value")) for r in lots)
            primary["unrealized_pnl"] = sum(_num(r.get("unrealized_pnl")) for r in lots)
            primary["realized_pnl"] = (sum(_num(r.get("realized_pnl")) for r in lots)
                                       if any(isinstance(r.get("realized_pnl"), (int, float)) for r in lots)
                                       else None)
            primary["total_pnl"] = sum(_num(r.get("total_pnl")) for r in lots)
            primary["weight"] = sum(_num(r.get("weight")) for r in lots)
            cost = sum(_num(r.get("quantity")) * _num(r.get("avg_cost")) for r in lots)
            qty = sum(_num(r.get("quantity")) for r in lots)
            primary["avg_cost"] = (cost / qty) if qty else 0.0
            primary["pnl_pct"] = (primary["total_pnl"] / cost * 100.0) if cost else primary.get("pnl_pct", 0)
            others = sorted({str(r.get("display_symbol") or r.get("ticker")) for r in lots[1:]})
            note = str(primary.get("notes") or "")
            extra = f"same ISIN as {', '.join(others)} (separate listings/lots)"
            primary["notes"] = (note + "; " + extra).strip("; ") if note else extra
        out.append((key[1], primary, lots))
    return out


def _snapshot_row_lines(r: dict, monitoring_by_display: dict | None) -> list[str]:
    """Render one holdings-table row. Pure helper shared by group/lot rows."""
    if r.get("pnl_validated", True):
        unreal_s = _feur(r.get("unrealized_pnl"))
        total_s = _feur(r.get("total_pnl"))
        try:
            pct_s = f"{float(r.get('pnl_pct', 0) or 0):+.1f}%"
        except (TypeError, ValueError):
            pct_s = "n/a"
    else:
        unreal_s = total_s = pct_s = "n/a"
    mon = (monitoring_by_display or {}).get(
        str(r.get("display_symbol") or r.get("ticker") or "").strip().upper(), "HOLD")
    # Lot identity: same display can be several broker lots/ISINs (e.g. AAPL
    # AAPL_US_EQ + APCd_EQ). internal_id disambiguates the rows.
    lot = str(r.get("internal_id") or "").strip()
    return [
        f"| {r.get('display_symbol') or r.get('ticker')} | {lot or '—'} | {r.get('company')} | "
        f"{float(r.get('quantity', 0) or 0):.4f} | {_feur(r.get('avg_cost'))} | {_feur(r.get('current_price'))} | "
        f"{_feur(r.get('market_value'))} | {unreal_s} | {_feur(r.get('realized_pnl'))} | {total_s} | "
        f"{pct_s} | {float(r.get('weight', 0) or 0):.2f}% | {signal_emoji(r.get('signal', 'HOLD'))} | {mon} | "
        f"{_fnum(r.get('support'))} | {_fnum(r.get('resistance'))} | "
        f"{r.get('market_data_state', 'n/a')} | {r.get('notes') or '—'} |"
    ]


def render_snapshot(
    *,
    generated_at: str,
    recon: dict,
    cash: dict,
    rows: list[dict],
    monitoring_by_display: dict,
    earnings_status: dict | None,
    t212_data: dict | None = None,
    performance: dict | None = None,
    ledger_metadata: dict | None = None,
) -> str:
    """Render the complete Trading212 financial snapshot."""
    from investment_engine.accounting.cashflows import FAIL_PERFORMANCE_NOTE
    status = recon.get("status", "UNKNOWN")
    perf = performance or {}
    perf_ok = perf.get("performance_status") == "Available"

    def _pval(key: str) -> str:
        return _feur(perf.get(key)) if perf_ok or perf.get(key) is not None else "Unavailable"

    lines = [
        "# T212 Portfolio Snapshot", "",
        f"Generated: {generated_at}",
        f"Data quality / reconciliation: **{status}**",
        "",
        "## Account Status", "",
        "### Broker Account Performance",
        "| Metric | Value |",
        "|---|---:|",
        f"| Broker total equity | {_feur(perf.get('broker_total_equity_eur', (recon or {}).get('total_equity')))} |",
        f"| Net deposits | {_pval('net_deposits_eur')} |",
        f"| Net deposits source | {perf.get('net_deposits_source', 'Unavailable — no verified cash-flow source')} |",
        f"| Net P&L after costs | {_perf_eur(perf.get('net_pnl_after_costs_eur')) if perf_ok else 'Unavailable'} |",
        f"| Return on net deposits | {_perf_pct(perf.get('return_pct')) if perf_ok else 'Unavailable'} |",
        f"| Reported free cash | {_feur(perf.get('reported_free_cash_eur'))} |",
        f"| Pie cash | {_feur(perf.get('pie_cash_eur'))} |",
        f"| Reported cash total | {_feur(perf.get('reported_cash_eur'))} |",
        f"| Cash allocation | {_perf_alloc(perf.get('cash_allocation_pct'))} |",
        f"| Fees recorded | {_feur(perf.get('fees_eur')) if perf.get('fees_eur') is not None else 'n/a (display only)'} |",
        f"| Interest recorded | {_feur(perf.get('interest_eur')) if perf.get('interest_eur') is not None else 'n/a (display only)'} |",
        f"| Tax recorded | {_feur(perf.get('tax_eur')) if perf.get('tax_eur') is not None else 'n/a (display only)'} |",
        "",
        "### Position Reconciliation",
        "| Metric | Value |",
        "|---|---:|",
        f"| Authoritative positions value | {_feur(recon.get('positions_value'))} |",
        f"| Implied cash from broker equity | {_feur(recon.get('implied_cash'))} |",
        f"| Reported cash | {_feur(recon.get('reported_cash'))} |",
        f"| Reconciliation delta | {_feur(recon.get('cash_delta'))} |",
        f"| Reconciliation threshold | {_feur(recon.get('threshold'))} |",
        f"| Reconciliation status | **{status}** |",
    ]
    if status == "FAIL":
        lines += ["",
                  "> Reconciliation check failed — derived account totals withheld. "
                  "No cause is asserted.",
                  "",
                  f"> {FAIL_PERFORMANCE_NOTE}"]
    lines += ["", "## Holdings", ""]
    if not rows:
        lines.append("No positions to display.")
    else:
        lines.append("| Ticker | Lot / Broker ID | Company | Qty | Avg Cost | Current Price | Market Value | "
                     "Unrealized P&L | Realized P&L | Total P&L | P&L % | Weight | "
                     "Canonical Signal | Monitoring Status | Support | Resistance | Market Data Status | Notes |")
        lines.append("|--------|---------------|---------|-----|----------|---------------|--------------|----------------|--------------|-----------|-------|--------|"
                     "----------------|-------------------|---------|------------|--------------------|-------|")
        for _group_key, _primary, _lots in _group_rows_by_isin(rows):
            lines.extend(_snapshot_row_lines(_primary, monitoring_by_display))
            if len(_lots) > 1:
                for _lot in _lots:
                    for _lot_line in _snapshot_row_lines(_lot, monitoring_by_display):
                        lines.append(_lot_line.replace("| ", "| ↳ ", 1))
    lines += ["", "## Data Coverage", ""]
    unmapped = sorted({str(r.get("display_symbol") or r.get("ticker"))
                       for r in (rows or [])
                       if str(r.get("market_data_state", "") or "").upper() == "UNRESOLVED"})
    lines.append("- Instruments without market-data mapping: " + (", ".join(unmapped) if unmapped else "none."))
    broker_only = sorted({str(r.get("display_symbol") or r.get("ticker"))
                          for r in (rows or [])
                          if str(r.get("market_data_state", "") or "").upper() == "BROKER_ONLY"})
    lines.append("- Broker-only positions (no Yahoo mapping by design — e.g. CFDs/pie slices like APCd; "
                 "broker values authoritative, excluded from external technicals): " +
                 (", ".join(broker_only) if broker_only else "none."))
    no_earn: list[str] = []
    for r in rows or []:
        disp = str(r.get("display_symbol") or r.get("ticker") or "").strip().upper()
        val = (earnings_status or {}).get(disp)
        dated = (isinstance(val, (tuple, list)) and len(val) > 0
                 and isinstance(val[0], str) and len(val[0]) >= 10)
        if not dated:
            no_earn.append(disp)
    lines.append("- Earnings unavailable (actual holdings): " + (", ".join(sorted(set(no_earn))) if no_earn else "none."))
    lines.append("- Realized P&L: unavailable — transaction ledger not imported (shows n/a).")
    led = ledger_metadata or {}
    lines.append("- Cash-flow source: " + str(led.get("selected_source", (perf or {}).get(
        "net_deposits_source", "Unavailable — no verified cash-flow source")))
        + f" ({led.get('selected_status', (perf or {}).get('net_deposits_status', 'UNAVAILABLE'))}).")
    if led.get("selected_status") == "VERIFIED":
        lines.append(f"- Historical FX conversion: complete ({led.get('api_items', 0)} API items, "
                     f"{led.get('api_unresolved_fx', 0)} unresolved).")
    else:
        lines.append("- Historical FX conversion: manual EUR baseline — no per-item conversion required.")
    lines.append(f"- Reconciliation: positions {_feur(recon.get('positions_value'))} + cash "
                 f"{_feur(recon.get('reported_cash'))} = derived {_feur(recon.get('derived_total'))} vs broker "
                 f"{_feur(recon.get('total_equity'))} (delta {_feur(recon.get('cash_delta'))}, "
                 f"threshold {_feur(recon.get('threshold'))}) → **{status}**.")
    return "\n".join(lines).rstrip() + "\n"


def _nv(item: Any, key: str, default: Any = "") -> Any:
    """Field access across dict records and NewsItem-like objects. Never raises."""
    try:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)
    except Exception:
        return default


def portfolio_health_score(rows: list[dict] | None, recon_status: str) -> tuple[int, str]:
    """Deterministic 0-100 health score + label.

    Formula (documented, no judgment calls): FAIL → 40; else any canonical
    SELL → 70; else non-PASS (UNKNOWN/DEGRADED path) → 70; else 90.
    Labels: >=80 HEALTHY, >=60 WATCH, else CRITICAL.
    """
    status = str(recon_status or "").upper()
    if status == "FAIL":
        score = 40
    else:
        sells = sum(1 for r in (rows or []) if isinstance(r, dict)
                    and normalize_signal((r or {}).get("signal")) == "SELL")
        score = 70 if (sells > 0 or status != "PASS") else 90
    label = "HEALTHY" if score >= 80 else ("WATCH" if score >= 60 else "CRITICAL")
    return score, label


def _deep_item_line(item: Any) -> str:
    title = str(_nv(item, "title", "") or "")[:140]
    url = str(_nv(item, "url", "") or "")
    source = str(_nv(item, "source", "") or "")
    pub = str(_nv(item, "published", "") or _nv(item, "published_str", "") or "")
    title_md = f"[{title}]({url})" if url else title
    return f"- {title_md} — {source} · {pub}".rstrip(" ·")


def _deep_bucket_lines(bucket: Any, limit: int = 5) -> list[str]:
    items = bucket if isinstance(bucket, list) else []
    return [_deep_item_line(i) for i in items[:limit]
            if str(_nv(i, "title", "") or "").strip()]


def build_deep_dive(
    *,
    generated_at: str,
    run_id: str,
    regime_result,
    recon: dict,
    rows: list[dict],
    monitoring_items: list[dict],
    t212_data: dict | None,
    news_by_symbol: dict | None,
    decision_news: list[dict],
    earnings_7d: dict,
    ideas: list[dict],
    trump_tracking: dict | None,
    commodity_news: dict | None,
    analyst_news: dict | None,
    order_plans: dict | None,
    dividends_12m: dict | None = None,
    stage_attribution: dict | None = None,
    result_modes: dict | None = None,
) -> str:
    """Render the deep-dive companion brief (15 fixed sections, deterministic).

    Data-contract: every section reads only already-computed structures;
    missing/empty input renders "No coverage this run." — never invented
    content, never LLM prose, never sized orders beyond advisory plans.
    The concise briefs and their contract are untouched by this document.
    """
    recon = recon or {}
    status = str(recon.get("status", "UNKNOWN")).upper()
    regime = getattr(regime_result, "regime", "UNKNOWN") if regime_result is not None else "UNKNOWN"
    score, health = portfolio_health_score(rows, status)
    perf = ((t212_data or {}).get("account_summary", {}) or {}) if isinstance(t212_data, dict) else {}
    cash = ((t212_data or {}).get("cash", {}) or {}) if isinstance(t212_data, dict) else {}

    def _num(value):
        try:
            return float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    lines = [
        "# Portfolio Deep Dive", "",
        f"Run: `{run_id}` | Generated: {generated_at}",
        f"Health score: **{score}/100 ({health})** | Reconciliation: **{status}**",
        "",
        "## 1. Executive Summary & Portfolio Health Score", "",
        f"- Stance: **{portfolio_stance(rows, status)}** | Regime: **{regime}**",
        f"- Broker total equity: {_feur(perf.get('total_equity', (recon or {}).get('total_equity')))}",
        f"- Reported free cash: {_feur(cash.get('free', 0))} | Total reported cash: "
        f"{_feur(_num(cash.get('free', 0)) + _num(cash.get('pie_cash', 0)) + _num(cash.get('blocked', 0)))}",
        f"- Score rule: FAIL=40, any SELL or non-PASS=70, else 90.",
        "",
        "## 2. Macro & Policy Watch (Trump/Geopolitics)", "",
    ]
    trump_lines: list[str] = []
    for _cat, _items in (trump_tracking or {}).items() if isinstance(trump_tracking, dict) else []:
        for _it in (_items or [])[:3]:
            if str(_nv(_it, "title", "") or "").strip():
                trump_lines.append(f"- [{_cat}] " + _deep_item_line(_it))
    lines += trump_lines[:8] or ["No coverage this run."]
    lines += ["", "## 3. Energy & Commodities Analysis", ""]
    energy_lines: list[str] = []
    for _target, _items in (commodity_news or {}).items() if isinstance(commodity_news, dict) else []:
        for _it in (_items or [])[:3]:
            if str(_nv(_it, "title", "") or "").strip():
                energy_lines.append(f"- [{_target}] " + _deep_item_line(_it))
    lines += energy_lines[:8] or ["No coverage this run."]

    _health_kw = ("fda", "trial", "pharma", "biotech", "drug", "healthcare", "clinical")
    _crypto_kw = ("bitcoin", "ethereum", "crypto", "solana", "btc", "eth")
    health_lines: list[str] = []
    crypto_lines: list[str] = []
    for _sym, _entries in (news_by_symbol or {}).items() if isinstance(news_by_symbol, dict) else []:
        for _it in (_entries or [])[:5]:
            _t = f"{_nv(_it, 'title', '')} {_nv(_it, 'preview', '')}".lower()
            if any(k in _t for k in _health_kw):
                health_lines.append(_deep_item_line(_it))
            if any(k in _t for k in _crypto_kw):
                crypto_lines.append(_deep_item_line(_it))
    lines += ["", "## 4. Healthcare Sector Update", ""]
    lines += health_lines[:6] or ["No coverage this run."]
    lines += ["", "## 5. Crypto Market Trends", ""]
    lines += crypto_lines[:6] or ["No coverage this run."]
    lines += ["", "## 6. Earnings Calendar & Upcoming Catalysts", ""]
    window = (earnings_7d or {}).get("in_window", []) if isinstance(earnings_7d, dict) else []
    if window:
        for ev in window[:10]:
            tag = "(confirmed)" if ev.get("status") == "confirmed" else "(estimated)"
            lines.append(f"- {_mday(ev.get('date', ''))} — {ev.get('display', '')}, "
                         f"{ev.get('company', '')} — earnings {tag}")
    else:
        lines.append("No relevant earnings events in the next 7 days.")
    _nxt = (earnings_7d or {}).get("next_after") if isinstance(earnings_7d, dict) else None
    if isinstance(_nxt, dict) and _nxt.get("date"):
        tag = "(confirmed)" if _nxt.get("status") == "confirmed" else "(estimated)"
        lines.append(f"- Next after this window: {_nxt.get('date', '')} — {_nxt.get('display', '')}, "
                     f"{_nxt.get('company', '')} — earnings {tag}.")
    lines += ["", "## 7. Analyst Ratings & Consensus Changes", ""]
    analyst_lines: list[str] = []
    for _sym, _items in (analyst_news or {}).items() if isinstance(analyst_news, dict) else []:
        for _it in (_items or [])[:3]:
            if str(_nv(_it, "title", "") or "").strip():
                analyst_lines.append(f"- [{_sym}] " + _deep_item_line(_it))
    lines += analyst_lines[:8] or ["No coverage this run."]
    lines += ["", "## 8. Watchlist & Discovery Opportunities", ""]
    if ideas:
        for idea in ideas[:5]:
            lines.append(f"- **{idea.get('ticker', '')}** — WATCH — {idea.get('reason', '')}")
    else:
        lines.append("No new ideas met the current evidence and validation threshold.")
    lines += ["", "## 9. Portfolio Advisory & Rebalancing Recommendations", ""]
    _plans = (order_plans or {}).get("plans", []) or [] if isinstance(order_plans, dict) else []
    if _plans and status != "FAIL":
        lines.append("> Advisory only — not executed. Confirm manually outside this program.")
        lines.append("")
        for plan in _plans:
            if isinstance(plan, dict):
                lines.append(f"- **{plan.get('ticker')}** {plan.get('direction')} "
                             f"~€{float(plan.get('indicative_notional_eur', 0) or 0):,.2f}")
    else:
        lines.append("No advisory plans this run (policy not set, gates unmet, or reconciliation FAIL).")
    lines += ["", "## 10. Risk Management & Drawdown Analysis", ""]
    _sells = [r for r in (rows or []) if isinstance(r, dict)
              and normalize_signal((r or {}).get("signal")) == "SELL"]
    _conc = sorted(((float(r.get("weight", 0) or 0), str(r.get("display_symbol") or r.get("ticker")))
                    for r in (rows or []) if isinstance(r, dict)), reverse=True)[:5]
    if _sells:
        lines.append(f"- Positions flagged for reduction: {', '.join(sorted({str(r.get('display_symbol') or r.get('ticker')) for r in _sells}))}.")
    else:
        lines.append("- No positions flagged for reduction.")
    if _conc and _conc[0][0] > 0:
        lines.append("- Largest weights: " + ", ".join(f"{d} {w:.1f}%" for w, d in _conc) + ".")
    lines.append("- Drawdown history: unavailable — no historical equity series retained (per-run snapshots only).")
    if status == "FAIL":
        lines.append("- Deployment blocked: reconciliation FAIL (see brief).")
    lines += ["", "## 11. Technical Overview & Support/Resistance Levels", ""]
    _tech_rows = [r for r in (rows or []) if isinstance(r, dict)
                  and (r.get("support") is not None or r.get("resistance") is not None)][:10]
    if _tech_rows:
        for r in _tech_rows:
            lines.append(f"- **{r.get('display_symbol') or r.get('ticker')}**: "
                         f"support {_fnum(r.get('support'))} / resistance {_fnum(r.get('resistance'))}.")
    else:
        lines.append("No support/resistance levels available (mappings unverified or no data).")
    lines += ["", "## 12. Asset Allocation Breakdown", ""]
    _alloc = sorted(((float(r.get("weight", 0) or 0), str(r.get("display_symbol") or r.get("ticker")))
                     for r in (rows or []) if isinstance(r, dict)), reverse=True)[:10]
    if _alloc and _alloc[0][0] > 0:
        for w, d in _alloc:
            lines.append(f"- {d}: {w:.2f}%")
    else:
        lines.append("No allocation weights available.")
    lines += ["", "## 13. Dividend & Yield Tracker", ""]
    _divs = dividends_12m if isinstance(dividends_12m, dict) else {}
    _div_rows = sorted(
        ((v.get("yield_pct") if isinstance(v.get("yield_pct"), (int, float)) else -1.0, k, v)
         for k, v in _divs.items() if isinstance(v, dict)),
        reverse=True)[:8]
    if _div_rows and _div_rows[0][0] >= 0:
        for _y, _d, _v in _div_rows:
            lines.append(f"- **{_d}**: TTM yield {_y:.2f}% "
                         f"({_v.get('ttm_per_share', '?')}/share native, "
                         f"{_v.get('payments_12m', '?')}x/12M, last ex {_v.get('last_ex_date', '?')}).")
        lines.append("- Yields from Yahoo Finance payouts vs latest close; broker P&L untouched.")
    else:
        lines.append("No trailing-12M dividends reported for covered holdings (Yahoo Finance).")
    lines += ["",
              "## 14. ESG & Governance Notes", "",
              "No ESG feed wired - governance notes out of scope for this run.",
              ""]
    lines += ["", "## 15. Appendix & Data Sources", "",
              "- Broker: Trading 212 read-only API (quantities, prices, P&L are broker truth).",
              "- Market data: Yahoo Finance (+ Playwright sweep fallback); indicators: manual engine.",
              "- News: Google News RSS + verified RSS fallbacks + corpus tiers + TradingView symbol news.",
              "- Dividends: Yahoo Finance trailing-12M payouts (informational; broker P&L untouched).",
              ]
    _sa = stage_attribution if isinstance(stage_attribution, dict) else {}
    if _sa:
        try:
            from investment_engine.providers.attribution import format_stage_line as _fmt_sa
        except Exception:
            _fmt_sa = None
        lines.append("- Models actually used this run:")
        for _st in sorted(_sa):
            _rec = _sa.get(_st) or {}
            if _fmt_sa is not None:
                lines.append(f"  - {_fmt_sa(_st, _rec)}")
            else:
                lines.append(f"  - {_st}: {(_rec.get('provider') or '?')}/"
                             f"{(_rec.get('model_served') or '?')}")
    else:
        lines.append("- Models per stage: see run_manifest `providers_per_stage`.")
    _mode_line = str((result_modes or {}).get("api_execution_mode", "") or "").strip() if isinstance(result_modes, dict) else ""
    lines.append("- Execution mode: " + (_mode_line or "see run_manifest ai_execution_mode") + ".")
    return "\n".join(lines).rstrip() + "\n"
    return "\n".join(lines).rstrip() + "\n"
