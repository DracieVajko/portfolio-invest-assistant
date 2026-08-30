from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from investment_engine.research.market_regime import RegimeResult
from investment_engine.research.peak_valley import PriceStructure, KeyLevel


class RegimeReportGenerator:
    """Generates clean, actionable Markdown reports from regime analysis."""

    def __init__(self, include_charts: bool = False):
        self.include_charts = include_charts

    def generate(self, result: RegimeResult, t212_data: dict = None, ai_recs: dict = None) -> str:
        """Generate clean, actionable Markdown report."""
        sections = [
            self._header(result),
            self._regime_summary(result),
            self._key_levels(result),
            self._news_section(result),
        ]
        
        # Add Trading212 data if available
        if t212_data:
            sections.append(self._t212_portfolio(t212_data))
        
        # Add AI recommendations if available
        if ai_recs:
            sections.append(self._ai_recommendations(ai_recs))
        
        sections.extend([
            self._earnings_calendar(result),
            self._warnings(result),
        ])
        
        return "\n\n".join(sections)

    def _header(self, result: RegimeResult) -> str:
        regime_emoji = {
            "PEAK_HOLD": "🔴",
            "DECLINING": "🟠", 
            "MUST_BUY": "🟢",
            "NEUTRAL": "⚪",
            "ERROR": "❌",
        }.get(result.regime, "❓")

        dt = datetime.fromisoformat(result.generated_at.replace('Z', '+00:00'))
        return f"""# {regime_emoji} EXI2 Market Regime — {dt.strftime('%Y-%m-%d %H:%M UTC')}

**Regime:** **{result.regime}** ({result.confidence:.0%}) | **EXI2:** €{result.timeframes.get('daily', {}).get('indicators', {}).get('CLOSE', 0):.2f} | **RSI:** {result.timeframes.get('daily', {}).get('indicators', {}).get('RSI_14', 0):.0f} | **Trend:** {result.timeframes.get('daily', {}).get('trend', 'N/A')}"""

    def _regime_summary(self, result: RegimeResult) -> str:
        impl = result.implications
        return f"""## 📊 Regime Summary

| Action | Detail |
|--------|--------|
| **Portfolio Action** | {impl.get('portfolio_action', 'N/A')} |
| **Tech Allocation** | {impl.get('tech_allocation', 'N/A')} |
| **Crypto Allocation** | {impl.get('crypto_allocation', 'N/A')} |
| **DCA Multiplier** | {impl.get('dca_multiplier', 1.0)}x |
| **Cash Target** | {impl.get('cash_target_pct', 10)}% |
| **Guidance** | {impl.get('message', 'N/A')} |

**Primary Signal:** {result.primary_signal or 'None'}"""

    def _key_levels(self, result: RegimeResult) -> str:
        ps: PriceStructure = result.price_structure
        lines = ["## 🎯 Key Levels to Watch"]

        if ps.nearest_support:
            lines.append(f"- **Support:** €{ps.nearest_support:.2f} (break → €{ps.nearest_support * 0.97:.2f})")
        if ps.nearest_resistance:
            lines.append(f"- **Resistance:** €{ps.nearest_resistance:.2f} (clear → €{ps.nearest_resistance * 1.03:.2f})")

        daily_ind = result.timeframes.get("daily", {}).get("indicators", {})
        if daily_ind.get("SMA_200"):
            status = "Above" if daily_ind.get('CLOSE', 0) > daily_ind['SMA_200'] else "Below"
            lines.append(f"- **200-Day MA:** €{daily_ind['SMA_200']:.2f} ({status})")
        if daily_ind.get("SMA_50"):
            lines.append(f"- **50-Day MA:** €{daily_ind['SMA_50']:.2f}")

        # Fib levels
        if ps.peaks and ps.valleys:
            fib = self._compute_fib_levels(ps.peaks, ps.valleys)
            if fib:
                lines.append("- **Fib Levels:** " + " | ".join(f"{k}: €{v:.2f}" for k, v in fib.items() if k in ["0.382", "0.500", "0.618", "0.786"]))

        return "\n".join(lines)

    def _compute_fib_levels(self, peaks, valleys) -> dict[str, float] | None:
        if not peaks or not valleys:
            return None
        last_peak = max(peaks, key=lambda p: p.date)
        last_valley = max(valleys, key=lambda v: v.date)
        if last_peak.date > last_valley.date:
            high, low = last_peak.price, last_valley.price
        else:
            high, low = last_valley.price, last_peak.price
        diff = high - low
        return {
            "0.382": high - diff * 0.382,
            "0.500": high - diff * 0.500,
            "0.618": high - diff * 0.618,
            "0.786": high - diff * 0.786,
        }

    def _news_section(self, result: RegimeResult) -> str:
        ns = result.news_sentiment
        if not ns or ns.get("count", 0) == 0:
            return "## 📰 News (Last 48h)\n\nNo qualifying news found."

        lines = ["## 📰 News (Last 48h)", ""]
        lines.append(f"**Sentiment:** {ns.get('sentiment', 'N/A')} ({ns.get('score', 0)}/100) | **Articles:** {ns.get('count', 0)}")
        
        # Note: news items with URLs would be added here if passed in
        # For now, show sentiment summary
        if ns.get('key_topics'):
            lines.append(f"**Topics:** {', '.join(ns['key_topics'])}")
        
        return "\n".join(lines)

    def _t212_portfolio(self, data: dict) -> str:
        """Format Trading212 portfolio data."""
        if not data or data.get("status") == "failed":
            return "## 💼 Trading212 Portfolio\n\n❌ Unable to fetch portfolio data."

        summary = data.get("account_summary", {})
        positions = data.get("positions", [])
        # Filter out pie constituents for display (avoid double counting)
        positions = [p for p in positions if not p.get("is_pie_constituent", False)]
        cash = data.get("cash", {})

        lines = ["## 💼 Trading212 Portfolio", ""]
        
        # Account summary
        total_equity = summary.get("total_equity", 0)
        free_cash = cash.get("free", 0)
        pie_cash = cash.get("pie_cash", 0)
        invested = cash.get("invested", 0)
        blocked_cash = summary.get("cash_blocked", 0)
        
        # Reconciliation fields
        derived_holdings_plus_available_cash = summary.get("derived_holdings_plus_available_cash", 0)
        reconciliation_difference = summary.get("reconciliation_difference", 0)
        reconciliation_threshold = summary.get("reconciliation_threshold", 0)
        reconciliation_status = summary.get("reconciliation_status", "UNKNOWN")
        
        # Position-level P&L (not account-level)
        unrealized_pnl = summary.get("unrealized_pnl", 0)
        unrealized_pnl_calc = summary.get("unrealized_pnl_calc", 0)
        
        lines.append(f"**Total Equity (broker):** €{total_equity:,.2f}")
        lines.append(f"**Free Cash:** €{free_cash:,.2f} | **Pie Cash:** €{pie_cash:,.2f} | **Blocked:** €{blocked_cash:,.2f}")
        lines.append(f"**Validated positions market value:** €{invested:,.2f}")
        lines.append(f"**Derived Holdings + Available Cash:** €{derived_holdings_plus_available_cash:,.2f}")
        lines.append(f"**Reconciliation:** {reconciliation_status} (diff: €{reconciliation_difference:,.2f}, threshold: €{reconciliation_threshold:,.2f})")
        lines.append("*Position values use normalized quote units and are reconciled to broker equity.*")
        
        if reconciliation_status == "PASS":
            lines.append(f"**Open Position Unrealized P&L (T212):** €{unrealized_pnl:,.2f}")
            lines.append(f"**Open Position Unrealized P&L (recalc):** €{unrealized_pnl_calc:,.2f}")
            lines.append("*Note: Realized P&L and account-level return require full transaction ledger (not available via API).*")
        else:
            lines.append("**⚠️ Account performance unreconciled — derived return withheld.**")
            lines.append("*Open position P&L and totals suppressed due to reconciliation failure.*")
        lines.append("")

        # Positions (excluding pie constituents to avoid double counting)
        if positions:
            lines.append("### Positions (excl. pie constituents)")
            lines.append("| Symbol | Qty | Value (EUR) | P&L (EUR) | P&L% |")
            lines.append("|--------|-----|-------------|-----------|------|")
            for pos in sorted(positions, key=lambda x: x.get("value_eur", x.get("value", 0)), reverse=True)[:15]:
                sym = pos.get("symbol", "?")
                qty = pos.get("quantity", 0)
                val = pos.get("value_eur", pos.get("value", 0))
                pnl_pos = pos.get("pnl_eur", pos.get("pnl", 0))
                pnl_pct_pos = pos.get("pnl_pct", 0)
                lines.append(f"| {sym} | {qty:.4f} | €{val:,.2f} | €{pnl_pos:,.2f} | {pnl_pct_pos:+.1f}% |")
            lines.append("")
        
        return "\n".join(lines)

    def _ai_recommendations(self, recs: dict) -> str:
        """Format AI recommendations section."""
        if not recs:
            return ""

        lines = ["## 🤖 AI Recommendations", ""]
        
        # Portfolio-level actions
        if recs.get("portfolio_actions"):
            lines.append("### Portfolio Actions")
            for action in recs["portfolio_actions"]:
                lines.append(f"- **{action['action'].upper()}** {action['asset']}: {action['reason']}")
                if action.get("details"):
                    lines.append(f"  - {action['details']}")
            lines.append("")

        # Specific trade recommendations
        if recs.get("trades"):
            lines.append("### Trade Recommendations")
            lines.append("| Action | Asset | Qty | Price | Stop Loss | Take Profit | Risk |")
            lines.append("|--------|-------|-----|-------|-----------|-------------|------|")
            for trade in recs["trades"]:
                lines.append(
                    f"| {trade['side']} | {trade['asset']} | {trade['qty']} | "
                    f"€{trade['price']:.2f} | €{trade['stop_loss']:.2f} | "
                    f"€{trade['take_profit']:.2f} | {trade['risk_pct']:.1f}% |"
                )
            lines.append("")

        # Stop loss / take profit updates
        if recs.get("position_updates"):
            lines.append("### Position Management")
            for upd in recs["position_updates"]:
                lines.append(f"- **{upd['asset']}**: SL €{upd['stop_loss']:.2f} → €{upd['new_stop']:.2f} | TP €{upd['take_profit']:.2f} → €{upd['new_tp']:.2f} ({upd['reason']})")
            lines.append("")

        # Cash deployment
        if recs.get("cash_deployment"):
            cd = recs["cash_deployment"]
            lines.append("### Cash Deployment")
            lines.append(f"- **Free Cash:** €{cd['free_cash']:,.2f}")
            lines.append(f"- **Deploy:** €{cd['deploy_amount']:,.2f} ({cd['deploy_pct']:.0f}%)")
            lines.append(f"- **Keep Reserve:** €{cd['reserve']:,.2f}")
            for target in cd.get("targets", []):
                lines.append(f"  - {target['asset']}: €{target['amount']:,.2f} @ €{target['price']:.2f} (max {target['max_risk']:.1f}% risk)")
            lines.append("")

        return "\n".join(lines)

    def _earnings_calendar(self, result: RegimeResult) -> str:
        """Show upcoming earnings for portfolio/watchlist stocks."""
        # This would need earnings data passed in
        return "## 📅 Earnings Calendar\n\n*Earnings data not available in current report.*"

    def _warnings(self, result: RegimeResult) -> str:
        warnings = result.warnings
        if not warnings:
            return ""
        
        lines = ["## ⚠️ Risk Warnings", ""]
        for w in warnings:
            lines.append(f"- ⚠️ {w}")
        return "\n".join(lines)


class AIContextBuilder:
    """Builds a separate context file for AI with all detailed data."""
    
    def __init__(self):
        pass

    def build_context_file(self, result: RegimeResult, t212_data: dict = None, 
                           news_items: list = None, earnings_data: dict = None) -> str:
        """Build comprehensive context file for AI (not shown in main report)."""
        sections = [
            "# AI Context File - Detailed Market Data",
            f"Generated: {datetime.now(timezone.utc).isoformat()}",
            "",
            "## EXI2 Regime Analysis",
            f"Regime: {result.regime} ({result.confidence:.0%})",
            f"Primary Signal: {result.primary_signal}",
            "",
            "## Multi-Timeframe Indicators",
        ]
        
        for tf_name, tf_data in result.timeframes.items():
            ind = tf_data.get("indicators", {})
            if ind:
                sections.append(f"\n### {tf_name.upper()}")
                for k, v in sorted(ind.items()):
                    sections.append(f"  {k}: {v}")
        
        sections.extend([
            "",
            "## Price Structure",
            f"Trend: {result.price_structure.current_trend}",
            f"Quality: {result.price_structure.trend_quality:.0%}",
            f"Support: €{result.price_structure.nearest_support:.2f}" if result.price_structure.nearest_support is not None else "Support: N/A",
            f"Resistance: €{result.price_structure.nearest_resistance:.2f}" if result.price_structure.nearest_resistance is not None else "Resistance: N/A",
            "",
            "## Key Levels",
        ])
        
        for level in result.price_structure.key_levels.get("support", [])[:5]:
            sections.append(f"  Support €{level.price:.2f} (strength: {level.strength})")
        for level in result.price_structure.key_levels.get("resistance", [])[:5]:
            sections.append(f"  Resistance €{level.price:.2f} (strength: {level.strength})")
        
        sections.extend([
            "",
            "## News Sentiment",
            f"Sentiment: {result.news_sentiment.get('sentiment')}",
            f"Score: {result.news_sentiment.get('score')}/100",
            f"Count: {result.news_sentiment.get('count')}",
            f"Topics: {', '.join(result.news_sentiment.get('key_topics', []))}",
        ])
        
        if t212_data:
            sections.extend(["", "## Trading212 Portfolio"])
            summary = t212_data.get("account_summary", {})
            sections.append(f"  Total Equity (broker): €{summary.get('total_equity', 0):,.2f}")
            sections.append(f"  Free Cash: €{t212_data.get('cash', {}).get('free', 0):,.2f}")
            sections.append(f"  Pie Cash: €{t212_data.get('cash', {}).get('pie_cash', 0):,.2f}")
            sections.append(f"  Validated positions market value: €{summary.get('invested', 0):,.2f}")
            recon_status = summary.get("reconciliation_status", "UNKNOWN")
            sections.append(f"  Reconciliation: {recon_status}")
            if recon_status == "PASS":
                sections.append(f"  Open Position Unrealized P&L (T212): €{summary.get('unrealized_pnl', 0):,.2f}")
                sections.append(f"  Open Position Unrealized P&L (recalc): €{summary.get('unrealized_pnl_calc', 0):,.2f}")
            else:
                sections.append("  ⚠️ Account performance unreconciled — derived return withheld.")
            for pos in t212_data.get("positions", [])[:20]:
                val_eur = pos.get('value_eur', pos.get('value', 0))
                avg_price_eur = pos.get('average_price_eur', pos.get('avg_price', 0))
                pnl_pct = pos.get('pnl_pct', 0)
                validation_status = pos.get('validation_status', 'UNKNOWN')
                sections.append(f"  {pos.get('symbol')}: {pos.get('quantity')} @ €{avg_price_eur:.2f} = €{val_eur:,.2f} (P&L: {pnl_pct:+.1f}%) [validation: {validation_status}]")
        
        if news_items:
            sections.extend(["", "## Recent News (with URLs)"])
            for item in news_items[:20]:
                sections.append(f"  - [{item.get('title')}]({item.get('url')}) — {item.get('source')} — {item.get('published')}")
        
        if earnings_data:
            sections.extend(["", "## Earnings Calendar"])
            for sym, data in earnings_data.items():
                sections.append(f"  {sym}: {data}")
        
        return "\n".join(sections)


def generate_regime_markdown(result: RegimeResult, t212_data: dict = None, ai_recs: dict = None) -> str:
    """Convenience function for quick report generation."""
    generator = RegimeReportGenerator()
    return generator.generate(result, t212_data, ai_recs)