from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

from investment_engine.config.settings import EngineSettings
from investment_engine.providers.factory import ProviderFactory
from investment_engine.research.market_data import analyst_consensus, fetch_recent_headlines, recent_earnings_date
from investment_engine.research.market_regime import EXI2RegimeAnalyzer
from investment_engine.research.news_engine import StrictNewsFetcher, NewsItem, analyze_news_sentiment
from investment_engine.research.pies import PieLoader
from investment_engine.reporting.regime_report import RegimeReportGenerator, AIContextBuilder
from investment_engine.schemas.ai_recommendations import parse_ai_recommendations, sanitize_ai_output


def _symbol(asset: dict[str, Any]) -> str:
    return str(asset.get("broker_symbol") or asset.get("yahoo_symbol") or asset.get("name") or "UNKNOWN")


def _is_us_symbol(symbol: str) -> bool:
    """Check if symbol is likely US-listed (for FinViz compatibility)."""
    # EXI2.DE, .L, .F, .CO, .KS, .DE, .AS, etc. are non-US
    non_us_suffixes = ('.DE', '.L', '.F', '.CO', '.KS', '.AS', '.SW', '.MI', '.PA', '.BR', '.OL', '.HE', '.VI', '.ST', '.CO', '.IC')
    return not any(symbol.endswith(suf) for suf in non_us_suffixes)


def _fetch_t212_data(settings_dict: dict) -> dict[str, Any] | None:
    """Fetch Trading212 portfolio data if enabled, with graceful degradation."""
    if not settings_dict.get("use_trading212_api", True):
        return None
    try:
        from trading212_integration import create_integration
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
        
        t212 = create_integration(config_file="api.env")
        if not t212:
            return {"status": "failed", "error": "T212 not configured"}

        # Single call to get_account_summary gets everything (cash + positions)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(t212.get_account_summary)
            try:
                summary = future.result(timeout=20)
            except FuturesTimeoutError:
                return {"status": "failed", "error": "T212 summary timeout"}

        if summary.get("status") == "failed":
            return {"status": "failed", "error": summary.get("error")}

        # Extract everything from single response
        return {
            "status": "ok",
            "account_summary": summary,
            "positions": summary.get("positions", []),
            "cash": {
                "free": summary.get("cash_free", 0),
                "invested": summary.get("invested", 0),
                "pie_cash": summary.get("cash_pie", 0),
                "total": summary.get("total_equity", 0),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def _fetch_all_news_parallel(settings_dict: dict, assets: List[Dict[str, Any]], max_workers: int = 8) -> tuple[dict, list[NewsItem]]:
    """Fetch news for assets in parallel, limited to top holdings."""
    fetcher = StrictNewsFetcher(
        max_age_hours=settings_dict.get("market_regime", {}).get("news", {}).get("max_age_hours", 48),
        min_relevance=settings_dict.get("market_regime", {}).get("news", {}).get("min_relevance_score", 60),
    )

    # Limit to top 10 assets by priority (first 10 enabled)
    priority_assets = assets[:10]

    def fetch_one(asset):
        sym = _symbol(asset)
        name = asset.get("name", sym)
        items = fetcher.fetch_for_symbol(
            sym, name,
            limit=settings_dict.get("market_regime", {}).get("news", {}).get("max_articles_per_symbol", 3)
        )
        return sym, [item.__dict__ for item in items], items

    news_by_symbol: dict[str, list[dict]] = {}
    all_news_items: list[NewsItem] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_one, asset): asset for asset in priority_assets}
        for future in as_completed(futures):
            try:
                sym, items_dict, items_obj = future.result(timeout=10)
                if items_dict:
                    news_by_symbol[sym] = items_dict
                    all_news_items.extend(items_obj)
            except Exception as e:
                asset = futures[future]
                print(f"News fetch failed for {asset.get('name', 'unknown')}: {e}")

    # Fetch macro news (SPY, BTC) in parallel
    with ThreadPoolExecutor(max_workers=2) as executor:
        macro_futures = {
            executor.submit(fetcher.fetch_for_symbol, "SPY", "S&P 500", 3): "MACRO",
            executor.submit(fetcher.fetch_for_symbol, "BTC", "Bitcoin", 2): "CRYPTO-MACRO",
        }
        for future in as_completed(macro_futures):
            try:
                label = macro_futures[future]
                items = future.result(timeout=10)
                if items:
                    news_by_symbol[label] = [item.__dict__ for item in items]
            except Exception:
                pass

    return news_by_symbol, []


def _yahoo_symbol(asset: dict[str, Any]) -> str:
    """Get the Yahoo Finance symbol for market data lookups."""
    return str(asset.get("yahoo_symbol") or asset.get("broker_symbol") or asset.get("name") or "UNKNOWN")


def _fetch_earnings_fast(assets: List[Dict[str, Any]], timeout_per_symbol: float = 3.0) -> dict[str, str]:
    """Fast earnings fetch with timeout per symbol. Uses Yahoo Finance symbols for lookup."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

    def fetch_one(asset):
        yahoo_sym = _yahoo_symbol(asset)
        broker_sym = _symbol(asset)
        try:
            date = recent_earnings_date(yahoo_sym)
            return broker_sym, date if date else "no_data"
        except Exception as e:
            return broker_sym, f"error: {str(e)[:50]}"

    earnings: dict[str, str] = {}
    # Limit to 15 assets, use yahoo_symbol for lookup but broker_symbol as key
    priority_assets = assets[:15]

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_one, asset): asset for asset in priority_assets}
        for future in as_completed(futures):
            try:
                sym, date = future.result(timeout=timeout_per_symbol)
                earnings[sym] = date
            except FuturesTimeoutError:
                asset = futures[future]
                earnings[_symbol(asset)] = "timeout"
            except Exception as e:
                asset = futures[future]
                earnings[_symbol(asset)] = f"error: {str(e)[:50]}"

    return earnings


def _build_regime_context(regime_result) -> str:
    """Build concise regime context for AI prompts."""
    if not regime_result:
        return "Regime analysis unavailable"
    impl = regime_result.implications
    return (
        f"EXI2 Regime: {regime_result.regime} ({regime_result.confidence:.0%} confidence)\n"
        f"Current Price: €{regime_result.timeframes.get('daily', {}).get('indicators', {}).get('CLOSE', 0):.2f}\n"
        f"RSI Daily: {regime_result.timeframes.get('daily', {}).get('indicators', {}).get('RSI_14', 0):.1f} | "
        f"RSI Weekly: {regime_result.timeframes.get('weekly', {}).get('indicators', {}).get('RSI_14', 0):.1f}\n"
        f"Trend Daily: {regime_result.timeframes.get('daily', {}).get('trend', 'UNKNOWN')} | "
        f"Trend Weekly: {regime_result.timeframes.get('weekly', {}).get('trend', 'UNKNOWN')}\n"
        f"Portfolio Action: {impl.get('portfolio_action', 'UNKNOWN')}\n"
        f"Tech Allocation: {impl.get('tech_allocation', 'NORMAL_DCA')}\n"
        f"Crypto Allocation: {impl.get('crypto_allocation', 'NORMAL_DCA')}\n"
        f"DCA Multiplier: {impl.get('dca_multiplier', 1.0)}x\n"
        f"Cash Target: {impl.get('cash_target_pct', 10)}%\n"
        f"Guidance: {impl.get('message', '')}\n"
        f"Warnings: {'; '.join(regime_result.warnings) if regime_result.warnings else 'None'}"
    )


def _fetch_finviz_safe(symbol: str) -> dict:
    """Safe FinViz fetch - only for US symbols."""
    if not _is_us_symbol(symbol):
        return {}
    try:
        from finvizfinance.quote import finvizfinance
        from finvizfinance.screener.overview import Overview as FinvizOverview
        from finvizfinance.group.overview import Overview as FinvizGroupOverview

        stock = finvizfinance(symbol)
        fund = stock.ticker_fundament()
        if not fund:
            return {}

        # Also get sector breadth
        breadth = {}
        try:
            go = FinvizGroupOverview()
            sectors = ["Technology", "Healthcare", "Financial", "Consumer Cyclical",
                       "Industrial", "Energy", "Utilities", "Real Estate",
                       "Basic Materials", "Consumer Defensive", "Communication Services"]
            for sector in sectors:
                try:
                    go.set_filter(filters_dict={"Sector": sector})
                    df = go.screener_view()
                    if not df.empty:
                        breadth[sector] = {
                            "count": len(df),
                            "avg_change": float(df["Change"].astype(str).str.rstrip('%').astype(float).mean()) if "Change" in df.columns else None,
                        }
                except Exception:
                    pass
        except Exception:
            pass

        return {
            "fundamentals": fund,
            "sector_breadth": breadth,
        }
    except Exception as e:
        return {"error": str(e)}


def _is_us_symbol(symbol: str) -> bool:
    """Check if symbol is likely US-listed (for FinViz compatibility)."""
    non_us_suffixes = ('.DE', '.L', '.F', '.CO', '.KS', '.AS', '.SW', '.MI', '.PA', '.BR', '.OL', '.HE', '.VI', '.ST', '.CO', '.IC')
    return not any(symbol.endswith(suf) for suf in non_us_suffixes)


def _symbol(asset: dict[str, Any]) -> str:
    return str(asset.get("broker_symbol") or asset.get("yahoo_symbol") or asset.get("name") or "UNKNOWN")


def run_engine(assets: List[Dict[str, Any]], portfolio_context: Dict[str, Any] | None = None, settings: EngineSettings | None = None) -> Dict[str, Any]:
    """Create a compact, live-data report with short model calls per active category."""
    settings = settings or EngineSettings.from_defaults()
    language = str((portfolio_context or {}).get("language") or "English")
    by_symbol = {_symbol(asset): asset for asset in assets}
    pies = PieLoader().load_all()
    tech_pies = [pie for pie in pies if "tech" in pie["name"].lower()]
    renewable_pies = [pie for pie in pies if "renew" in pie["name"].lower()]
    passive_pies = [pie for pie in pies if pie not in tech_pies and pie not in renewable_pies]
    crypto_assets = [asset for asset in assets if str(asset.get("group", "")).upper() == "CRYPTO"]

    def entries_from_pies(group: list[dict[str, Any]]) -> list[dict[str, str]]:
        return [{"ticker": holding["slice"], "name": holding["name"]} for pie in group for holding in pie["holdings"]]

    tech = entries_from_pies(tech_pies)
    renewable = entries_from_pies(renewable_pies)
    existing = {item["ticker"] for item in tech + renewable}
    other = [{"ticker": _symbol(asset), "name": str(asset.get("name") or _symbol(asset))} for asset in assets if str(asset.get("group", "")).upper() in {"TECH_PIE", "SHORT_TERM_TRADING"} and _symbol(asset) not in existing]
    crypto = [{"ticker": _symbol(asset), "name": str(asset.get("name") or _symbol(asset))} for asset in crypto_assets]

    all_active_assets = tech + renewable + other + crypto
    
    # Filter out pie constituents from active positions for AI recommendations
    active_positions = [p for p in all_active_assets if not p.get("is_pie_constituent", False)]

    # --- FETCH DATA IN PARALLEL ---
    import time
    start_time = time.time()

    # 1. Trading212 (with timeout protection)
    settings_dict = asdict(settings) if hasattr(settings, '__dataclass_fields__') else settings.__dict__
    t212_data = _fetch_t212_data(settings_dict)

    # Reconciliation guard: validate T212 data before proceeding
    reconciliation_ok = True
    if t212_data and t212_data.get("status") == "ok":
        summary = t212_data.get("account_summary", {})
        recon_status = summary.get("reconciliation_status", "UNKNOWN")
        recon_diff = summary.get("reconciliation_difference", 0)
        recon_threshold = summary.get("reconciliation_threshold", 0)
        total_equity = summary.get("total_equity", 0)
        derived_holdings = summary.get("derived_holdings_plus_available_cash", 0)
        
        logger.info(f"T212 Reconciliation: {recon_status} | Equity: €{total_equity:,.2f} | Derived: €{derived_holdings:,.2f} | Diff: €{recon_diff:,.2f} (threshold: €{recon_threshold:,.2f})")
        
        if recon_status == "FAIL":
            reconciliation_ok = False
            logger.warning("T212 reconciliation FAILED - suppressing account-level P&L and derived return from report and LLM")
            # Mark t212_data as reconciled=false so downstream code knows
            t212_data["reconciliation_ok"] = False
        else:
            t212_data["reconciliation_ok"] = True
    else:
        t212_data = t212_data or {"status": "failed", "reconciliation_ok": False}

    # 2. News (parallel, top 10 holdings)
    news_by_symbol, all_news_items = _fetch_all_news_parallel(settings_dict, all_active_assets)

    # 3. Earnings (parallel, fast)
    earnings = _fetch_earnings_fast(all_active_assets)

    # 4. Market Regime
    regime_result = None
    regime_context = ""
    regime_markdown = ""
    if settings.market_regime.enabled:
        try:
            regime_analyzer = EXI2RegimeAnalyzer(
                symbol=settings.market_regime.symbol,
                cache_ttl_seconds=settings.market_regime.cache_ttl_seconds,
            )
            regime_result = regime_analyzer.analyze()
            regime_context = _build_regime_context(regime_result)
        except Exception as e:
            regime_context = f"Regime analysis failed: {e}"

    # 5. AI Recommendations
    ai_recs = {}
    if regime_result and t212_data and t212_data.get("status") == "ok":
        provider = ProviderFactory.create(settings)
        ai_recs = _build_ai_recommendations(regime_result, t212_data, all_active_assets, provider, settings_dict, language)

    # 6. Generate Reports
    if settings.market_regime.enabled and regime_result:
        report_gen = RegimeReportGenerator(
            include_charts=settings.market_regime.reporting.get("include_charts", False),
        )
        regime_markdown = report_gen.generate(regime_result, t212_data, ai_recs)
    else:
        regime_markdown = ""

    # Save AI context file
    output_dir = Path("reports")
    output_dir.mkdir(parents=True, exist_ok=True)
    context_file = _save_ai_context(regime_result, t212_data, [], all_active_assets, output_dir)

    # 7. LLM Decisions for active positions
    provider = ProviderFactory.create(settings)
    decision_sections: list[str] = []
    
    # Prepare news for LLM
    news_for_llm = news_by_symbol
    
    for title, positions in (("Crypto — decision only", crypto), ("Active Positions — Tech", tech + other), ("Active Positions — Renewables", renewable)):
        if not positions:
            continue
        position_symbols = {item["ticker"] for item in positions}
        scoped_news = {symbol: entries for symbol, entries in news_for_llm.items() if symbol in position_symbols or (title.startswith("Crypto") and symbol in ["CRYPTO-MACRO", "MACRO"])}
        scoped_earnings = {symbol: date for symbol, date in earnings.items() if symbol in position_symbols}
        
        # Special handling for crypto - no earnings dates, simpler format
        if title.startswith("Crypto"):
            prompt = f"""Write only the '{title}' section of a concise portfolio report in {language}. This is research, not financial advice.
Return at most 300 tokens. Use one Markdown bullet for every supplied ticker, with exactly: TICKER — BUY/SELL/HOLD/WAIT/WATCH — horizon (swing/long-term) — concise catalyst or risk.
Do not invent prices, results, dates, news, or analyst opinions. Use the supplied headlines only as current evidence.

MARKET REGIME CONTEXT:
{regime_context}

POSITIONS: {positions}
RECENT HEADLINES: {[f"{sym}: {e.get('title','')[:120]} ({e.get('source','')}, {e.get('published','')})" for sym, entries in scoped_news.items() for e in entries] or 'None'}"""
        else:
            prompt = f"""Write only the '{title}' section of a concise portfolio report in {language}. This is research, not financial advice.
Return at most 500 tokens. Use one Markdown bullet for every supplied ticker, with exactly: TICKER — BUY/SELL/HOLD/WAIT/WATCH — horizon (intraday/swing/long-term) — concise catalyst or risk — earnings date/status.
Do not invent prices, results, dates, news, or analyst opinions. Use the supplied headlines only as current evidence. Aggressive short or intraday ideas must be marked HIGH RISK.

MARKET REGIME CONTEXT:
{regime_context}

POSITIONS: {positions}
EARNINGS: {scoped_earnings}
RECENT HEADLINES: {[f"{sym}: {e.get('title','')[:120]} ({e.get('source','')}, {e.get('published','')})" for sym, entries in scoped_news.items() for e in entries] or 'None'}"""
        
        section = _generate_with_fallback(provider, prompt, settings, tokens=500, stage="decision")
        decision_sections.append(f"## {title}\n{section}")

    # Candidate section
    discovery_prompt = f"""Write a Markdown section called '## Potential New Ideas' in {language}.
Using only these recent headlines, name at most three ticker candidates as WATCH, never BUY. Explain in one short line why each needs further verification. If no ticker is clearly identifiable and supported, write 'None supported by current evidence.'"""
    candidate_section = _generate_with_fallback(provider, discovery_prompt, settings, tokens=400, stage="discovery")

    # Summary
    summary_prompt = f"""Write only these two Markdown sections in {language}, based strictly on the supplied decisions:
## Summary
At most 5 concise bullets.
## Priority Actions
A compact table with at most 10 highest-priority BUY/SELL/HOLD/WAIT/WATCH actions and horizons.
Do not invent price, news or analyst facts. This is research, not financial advice.

DECISIONS:
{chr(10).join(decision_sections + [candidate_section])}"""
    summary_section = _generate_with_fallback(provider, summary_prompt, settings, tokens=1000, stage="summary")

    # Build final markdown
    summary_header = "\n".join([
        "# Portfolio Decision Report",
        "",
        summary_section,
        "",
        "_Scope: News limited to last 48h. AI context file saved separately with full data._",
    ])

    markdown_parts = [summary_header]
    if regime_markdown:
        markdown_parts.append(regime_markdown)
    markdown_parts.extend([
        *decision_sections,
        _passive_pie_lines(passive_pies),
        candidate_section,
        _format_trump_watch(news_by_symbol.get("TRUMP", [])),
        _format_news(news_by_symbol),
    ])
    markdown = "\n\n".join(markdown_parts)

    # Write reports
    output_dir = Path("reports")
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    md_file = output_dir / f"portfolio_analysis_{timestamp}.md"
    md_file.write_text(markdown, encoding="utf-8")

    elapsed = time.time() - start_time
    print(f"Report generated in {elapsed:.1f}s")

    return {
        "markdown": markdown,
        "markdown_file": str(md_file),
        "context_file": str(context_file),
        "tech_tickers": tech + other,
        "renewable_tickers": renewable,
        "crypto": crypto,
        "passive_pies": [{"name": pie["name"], "tickers": [holding["slice"] for holding in pie["holdings"]]} for pie in passive_pies],
        "recent_news": news_by_symbol,
        "trump_watch": {},
        "earnings_dates": earnings,
        "settings": asdict(settings),
        "report_mode": "clean_actionable_report",
        "market_regime": regime_result.to_dict() if regime_result else None,
        "ai_recommendations": ai_recs,
        "t212_data": t212_data,
    }


def _build_ai_recommendations(
    regime_result, 
    t212_data: dict, 
    assets: List[Dict[str, Any]],
    provider,
    settings_dict: dict,
    language: str
) -> dict:
    """Generate AI-powered trade recommendations based on regime, portfolio, and free cash."""
    
    if not regime_result or not t212_data or t212_data.get("status") != "ok":
        return {"error": "Missing regime or T212 data"}
    
    # Reconciliation guard: don't send unreliable P&L to LLM
    reconciliation_ok = t212_data.get("reconciliation_ok", False)
    if not reconciliation_ok:
        logger.warning("Skipping AI recommendations: T212 reconciliation failed")
        return {"error": "T212 reconciliation failed - AI recommendations suppressed", "portfolio_actions": [], "trades": [], "position_updates": [], "cash_deployment": {"free_cash": 0, "deploy_amount": 0, "deploy_pct": 0, "reserve": 0, "targets": []}}
    
    summary = t212_data.get("account_summary", {})
    positions = t212_data.get("positions", [])
    cash = t212_data.get("cash", {})
    free_cash = cash.get("free", 0)
    total_equity = summary.get("total_equity", 0)
    
    def _canonical_position(pos: dict) -> dict:
        """Extract canonical normalized position data for LLM prompts."""
        return {
            "symbol": pos.get("symbol"),
            "qty": pos.get("quantity"),
            "value_eur": pos.get("value_eur"),
            "pnl_pct": pos.get("pnl_pct"),
            "average_price_eur": pos.get("average_price_eur"),
            "current_price_eur": pos.get("current_price_eur"),
            "quote_currency": pos.get("quote_currency"),
            "price_unit": pos.get("price_unit"),
            "unrealized_pnl_eur": pos.get("pnl_eur"),
        }


    pos_summary = []
    for pos in positions:
        canonical = _canonical_position(pos)
        # Validation: quantity > 0 but value_eur is 0 or missing
        if canonical["qty"] and canonical["qty"] > 0 and (canonical["value_eur"] is None or canonical["value_eur"] == 0):
            logger.warning(f"Position validation FAIL: {canonical['symbol']} qty={canonical['qty']} value_eur={canonical['value_eur']}")
            canonical["validation_status"] = "FAIL"
        else:
            canonical["validation_status"] = "PASS"
        pos_summary.append(canonical)
    
    regime_summary = {
        "regime": regime_result.regime,
        "confidence": regime_result.confidence,
        "action": regime_result.implications.get("portfolio_action"),
        "cash_target_pct": regime_result.implications.get("cash_target_pct"),
        "dca_multiplier": regime_result.implications.get("dca_multiplier"),
        "tech_allocation": regime_result.implications.get("tech_allocation"),
        "crypto_allocation": regime_result.implications.get("crypto_allocation"),
    }
    
    key_levels = {
        "support": regime_result.price_structure.nearest_support,
        "resistance": regime_result.price_structure.nearest_resistance,
    }
    
    support_str = f"{key_levels['support']:.2f}" if key_levels['support'] else 'N/A'
    resistance_str = f"{key_levels['resistance']:.2f}" if key_levels['resistance'] else 'N/A'
    
    prompt = f"""You are a portfolio manager. Output ONLY valid JSON with specific trade actions.

PORTFOLIO:
- Equity: {total_equity:,.0f} EUR | Cash: {free_cash:,.0f} EUR | Cash Target: {regime_summary['cash_target_pct']}%
- Regime: {regime_summary['regime']} ({regime_summary['confidence']:.0%}) | Action: {regime_summary['action']}
- DCA Multiplier: {regime_summary['dca_multiplier']}x
- Tech Allocation: {regime_summary['tech_allocation']} | Crypto: {regime_summary['crypto_allocation']}
- Support: {support_str} | Resistance: {resistance_str}

POSITIONS: {pos_summary}

REGIME GUIDANCE: {regime_summary['action']} - Tech: {regime_summary['tech_allocation']}, Crypto: {regime_summary['crypto_allocation']}

OUTPUT ONLY VALID JSON:
{{
  "portfolio_actions": [{{"action": "hold", "asset": "SYMBOL", "reason": "reason based on regime/indicators/news"}}],
  "trades": [{{"side": "BUY", "asset": "SYMBOL", "qty": 0.0, "price": 0.0, "stop_loss": 0.0, "take_profit": 0.0, "risk_pct": 0.0, "reason": "specific reason: regime/indicators/news"}}],
  "position_updates": [{{"asset": "SYMBOL", "stop_loss": 0.0, "new_stop": 0.0, "take_profit": 0.0, "new_tp": 0.0, "reason": "reason"}}],
  "cash_deployment": {{"free_cash": 0.0, "deploy_amount": 0.0, "deploy_pct": 0.0, "reserve": 0.0, "targets": [{{"asset": "SYMBOL", "amount": 0.0, "price": 0.0, "max_risk": 0.0}}]}}
}}

RULES:
- Max 2% risk per trade, min 1:2 R:R, mandatory stops, 50/50 scale-out
- PEAK_HOLD = prioritize SELLs, reduce new BUYs
- MUST_BUY = prioritize BUYs, deploy cash aggressively
- DECLINING = prepare watchlist, raise cash, no new positions
- NEUTRAL = normal DCA
- Use free cash for deployment, respect cash target
- Base decisions on: regime signals, technical indicators (RSI, MACD, moving averages), news sentiment, earnings
- Be concise, specific, actionable. No hedging language.
- Output ONLY valid JSON, no markdown, no extra text."""

    try:
        result = provider.generate(prompt, stage="ai_recommendations", context={"max_output_tokens": 1024})
        # Clean up response - remove any markdown code fences or extra text
        cleaned = result.strip()
        # Remove markdown code fences if present
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        # Use the new schema validation
        from investment_engine.schemas.ai_recommendations import parse_ai_recommendations
        return parse_ai_recommendations(cleaned)
    except Exception as e:
        return {"error": f"AI recommendation failed: {str(e)}", "raw_response": result[:500] if 'result' in locals() else "no response"}


def _save_ai_context(result, t212_data, news_items, assets, output_dir: Path) -> Path:
    """Save AI context file separately."""
    builder = AIContextBuilder()
    context = builder.build_context_file(
        result, 
        t212_data, 
        news_items, 
        None
    )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    context_file = output_dir / f"ai_context_{timestamp}.md"
    context_file.write_text(context, encoding="utf-8")
    return context_file


def _generate_with_fallback(provider, prompt, settings, tokens=800, stage="decision") -> str:
    """Generate with fallback - let the provider handle fallback internally."""
    import time as _time
    start = _time.time()
    
    def _log_attempt(stage_name, model_name, success, duration, content_len, error=None):
        if success:
            logger.info("LLM stage=%s model=%s duration=%.1fs content_len=%d OK", stage_name, model_name, duration, content_len)
        else:
            logger.warning("LLM stage=%s model=%s duration=%.1fs FAILED: %s", stage_name, model_name, duration, error)
    
    # Single call - let the provider (FallbackProvider) handle fallback internally
    try:
        result = provider.generate(prompt, stage=stage, context={"model": settings.decision_model, "max_output_tokens": tokens})
        duration = _time.time() - start
        if result and not result.startswith(f"[{stage}]") and "error" not in result.lower():
            _log_attempt(stage, settings.decision_model, True, duration, len(result))
            return result
        else:
            _log_attempt(stage, settings.decision_model, False, duration, len(result) if result else 0, result)
    except Exception as exc:
        duration = _time.time() - start
        _log_attempt(stage, settings.decision_model, False, duration, 0, str(exc))
    
    # Deterministic fallback - never return error text to user
    return _deterministic_fallback(stage, language=str((settings.__dict__ if hasattr(settings, '__dict__') else {}).get("language", "English")))


def _deterministic_fallback(stage: str, language: str = "English") -> str:
    """Return a safe, deterministic fallback response for a given stage."""
    fallbacks = {
        "decision": "HOLD — insufficient data for actionable decision",
        "discovery": "None supported by current evidence.",
        "summary": "## Summary\n- Unable to generate AI summary due to model unavailability.\n- Regime-based DCA schedule continues per configuration.\n- Reconciliation status determines data reliability.\n\n## Priority Actions\n| Ticker | Action | Horizon |\n|--------|--------|---------|\n| — | HOLD | long-term |",
        "ai_recommendations": '{"portfolio_actions": [], "trades": [], "position_updates": [], "cash_deployment": {"free_cash": 0, "deploy_amount": 0, "deploy_pct": 0, "reserve": 0, "targets": []}}',
    }
    return fallbacks.get(stage, "Data unavailable - model error")


def _passive_pie_lines(pies: list[dict[str, Any]]) -> str:
    lines = ["## Passive Pies — pie-level decision only"]
    for pie in pies:
        lines.append(f"- **{pie['name']}** — HOLD / regular add — long-term. Decisions are intentionally at Pie level; individual constituents are not analysed.")
    return "\n".join(lines)


def _format_trump_watch(trump_watch: list[dict]) -> str:
    if not trump_watch:
        return "## Trump Watch (last 48h)\n\nNo Trump-related news found."
    lines = ["## Trump Watch (last 48h)"]
    for item in trump_watch:
        lines.append(f"- **{item.get('title','')}** — {item.get('source','')} · {item.get('published','')}")
    return "\n".join(lines)


def _format_news(news_by_symbol: dict[str, list[dict]]) -> str:
    if not news_by_symbol:
        return "## News (last 48h)\n\nNo news found."
    lines = ["## News (last 48h)"]
    for symbol, entries in news_by_symbol.items():
        if not entries:
            continue
        lines.append(f"### {symbol}")
        for item in entries[:3]:
            title = item.get('title', '')
            url = item.get('url', '')
            source = item.get('source', '')
            pub = item.get('published', '')
            lines.append(f"- [{title}]({url}) — {source} · {pub}")
    return "\n".join(lines)