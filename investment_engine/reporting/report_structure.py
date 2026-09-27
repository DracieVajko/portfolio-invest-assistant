"""
Report structure layer (Phase 4 target topology).

- current/                 this run: decision brief, snapshot, portfolio_analysis.json,
                           portfolio_intelligence_brief.md, failed_tickers.md?, run_manifest.json
- ai_context/              machine context: ai_context_<run_id>.md,
                           portfolio_state_<run_id>.json (Phase 5: research_corpus_*.*)
- debug/<run_id>/          raw_endpoint_dump.json, reconciliation diagnostics, run.log
- archive/<run_id>/        full copy of current/ for this run
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from investment_engine.reporting.documents import portfolio_stance

logger = logging.getLogger(__name__)

# Retention settings
DEBUG_RETENTION_DAYS = 30
DEBUG_MAX_RUNS = 14


def setup_debug_layer(run_id: str, base_dir: Path = Path("reports")) -> Dict[str, Path]:
    """
    Create debug layer folder structure for a run.
    Returns dict with paths for each debug output.
    """
    debug_dir = Path("reports") / "debug" / run_id
    debug_dir.mkdir(parents=True, exist_ok=True)

    # Prune old debug folders
    _prune_debug_layer(base_dir=Path("reports") / "debug")

    paths = {
        "debug_dir": debug_dir,
        "raw_endpoint_dump": debug_dir / "raw_endpoint_dump.json",
        "reconciliation_diagnostic_md": debug_dir / "reconciliation_diagnostic.md",
        "reconciliation_diagnostic_csv": debug_dir / "reconciliation_diagnostic.csv",
        "run_log": None,  # Will be set after log is known
    }
    return paths


def _prune_debug_layer(base_dir: Path) -> None:
    """Prune debug folders older than retention policy."""
    if not base_dir.exists():
        return

    cutoff_date = datetime.now() - timedelta(days=DEBUG_RETENTION_DAYS)
    run_dirs: List[tuple[datetime, Path]] = []

    for item in base_dir.iterdir():
        if item.is_dir():
            try:
                # Expect format: run_id (8 chars) or timestamp
                dir_name = item.name
                # Try to parse as date from name or use mtime
                mtime = datetime.fromtimestamp(item.stat().st_mtime)
                run_dirs.append((mtime, item))
            except Exception:
                pass

    run_dirs.sort(key=lambda x: x[0], reverse=True)

    # Keep by count
    for _, dir_path in run_dirs[DEBUG_MAX_RUNS:]:
        logger.info("Pruning debug folder (max runs exceeded): %s", dir_path)
        shutil.rmtree(dir_path, ignore_errors=True)

    # Keep by date
    for mtime, dir_path in run_dirs:
        if mtime < cutoff_date:
            logger.info("Pruning debug folder (older than %d days): %s", DEBUG_RETENTION_DAYS, dir_path)
            shutil.rmtree(dir_path, ignore_errors=True)


def move_debug_outputs(run_id: str, run_log_path: Path, debug_paths: Dict[str, Path]) -> None:
    """Move debug outputs to the debug layer folder."""
    debug_dir = debug_paths["debug_dir"]

    # Move reconciliation diagnostic files
    for src_name, dst_path in [
        ("reconciliation_diagnostic.csv", debug_paths["reconciliation_diagnostic_csv"]),
        ("reconciliation_diagnostic.md", debug_paths["reconciliation_diagnostic_md"]),
    ]:
        src = Path("reports") / src_name
        if src.exists():
            try:
                shutil.move(str(src), str(dst_path))
                logger.debug("Moved %s to %s", src, dst_path)
            except Exception as e:
                logger.warning("Failed to move %s: %s", src, e)

    # Move raw endpoint dump (pattern: raw_endpoint_dump_<run_id>.json or raw_endpoint_dump.json)
    for pattern in [f"raw_endpoint_dump_{run_id}.json", "raw_endpoint_dump.json"]:
        src = Path("reports") / pattern
        if src.exists():
            try:
                shutil.move(str(src), str(debug_paths["raw_endpoint_dump"]))
                logger.debug("Moved %s to %s", src, debug_paths["raw_endpoint_dump"])
                break
            except Exception as e:
                logger.warning("Failed to move %s: %s", src, e)

    # Copy run log
    if run_log_path and run_log_path.exists():
        dst_log = debug_dir / "run.log"
        try:
            shutil.copy2(run_log_path, dst_log)
            debug_paths["run_log"] = dst_log
        except Exception as e:
            logger.warning("Failed to copy run log: %s", e)


def save_ai_context_layer(
    run_id: str,
    context_content: str,
    portfolio_analysis: dict,
    base_dir: Path = Path("reports")
) -> tuple[Path, Path]:
    """
    Save AI context layer files.
    Returns (context_file_path, portfolio_analysis_path).
    """
    ai_context_dir = Path("reports") / "ai_context"
    ai_context_dir.mkdir(parents=True, exist_ok=True)

    context_file = ai_context_dir / f"ai_context_{run_id}.md"
    analysis_file = ai_context_dir / f"portfolio_analysis_{run_id}.json"

    # _save_ai_context() already wrote this exact .md into the layer; copying
    # it again would duplicate the artifact. Persist only when different.
    try:
        existing = context_file.read_text(encoding="utf-8")
    except OSError:
        existing = None
    if existing != context_content:
        context_file.write_text(context_content, encoding="utf-8")
        logger.info("AI context saved: %s", context_file)
    else:
        logger.debug("AI context already present, copy skipped: %s", context_file)
    analysis_file.write_text(json.dumps(portfolio_analysis, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    logger.info("Portfolio analysis saved: %s", analysis_file)

    return context_file, analysis_file


def generate_human_brief(
    result: dict,
    portfolio_rows: list[dict],
    monitoring_items: list[dict],
    regime_result: Optional[Any],
    t212_data: Optional[dict],
    decision_news: list[dict],
    earnings_7d: dict,
    ideas: list[dict],
    portfolio_names: dict,
    reconciliation: Optional[Any],
) -> str:
    """Generate the intelligence brief (Phase 4: delegates to the concise builder).

    Signature is frozen by tests/test_result_contract.py (wrapper-key parity).
    """
    from investment_engine.reporting.documents import build_intelligence_brief

    return build_intelligence_brief(result if isinstance(result, dict) else {})

def _get_portfolio_stance(portfolio_rows: list[dict], recon_status: str) -> str:
    """Single stance source (Phase 4): alias of documents.portfolio_stance."""
    return portfolio_stance(portfolio_rows, recon_status)


def _build_ai_attribution_section(result: dict) -> list[str]:
    """Build the AI Agent Attribution section showing which model did what.

    Phase 4: actual per-stage winners (providers_per_stage) take precedence
    over configured model names.
    """
    lines = ["", "## AI Agent Attribution & Sources"]

    # Actual winners first (truth), configured intent as fallback.
    stages = result.get("providers_per_stage") or {}
    if stages:
        lines.append("")
        lines.append("### Actual Providers Per Stage")
        for stage in sorted(stages):
            rec = stages[stage] or {}
            lines.append(
                f"- **{stage}**: {(rec.get('provider') or '?')} "
                f"(served {(rec.get('model_served') or '?')}, depth {(rec.get('fallback_depth', '?'))})"
            )

    # Get model info from result
    ai_recs = result.get("ai_recommendations", {}) or {}
    model_info = ai_recs.get("model_info", {}) or {}
    
    # Determine which model was used for each stage
    decision_model = model_info.get("decision_model", "deterministic_fallback")
    summary_model = model_info.get("summary_model", "deterministic_fallback")
    discovery_model = model_info.get("discovery_model", "deterministic_fallback")
    decision_detail_model = model_info.get("decision_detail_model", "deterministic_fallback")
    
    # Python-sourced calculations
    lines = [
        "",
        "### Decision & Reasoning",
        f"- **Decision/Recommendation**: {_format_model_name(decision_model)}",
        f"- **Reasoning/Thinking**: {_format_model_name(decision_detail_model)} (finr1 for math, gpt-oss/qwen for reasoning)",
        f"- **Summary Generation**: {_format_model_name(summary_model)} (gemma with fallback)",
        f"- **Discovery/New Ideas**: {_format_model_name(discovery_model)}",
        "",
        "### Analyst Recommendations & External Sources",
    ]
    
    # Add analyst consensus if available
    analyst_consensus = result.get("analyst_consensus", {})
    if analyst_consensus:
        lines.append(f"- **Analyst Consensus**: {analyst_consensus.get('summary', 'Available')}")
    
    # Python-sourced calculations
    lines.extend([
        "",
        "### Python-Sourced Calculations (Deterministic)",
        "- **Reconciliation & Valuation**: Python (broker-first, no LLM)",
        "- **Technical Indicators (RSI, MACD, SMA, ATR, Support/Resistance)**: Python via yfinance/Playwright",
        "- **Position Valuation & P&L**: Python (broker-first EUR valuation)",
        "- **Reconciliation Delta & Thresholds**: Python (strict tolerance)",
        "- **Position Weights & Concentration**: Python",
        "- **Earnings Dates**: Python (yfinance + calendar classification)",
        "",
        "### Model Fallback Chain",
        "- **Primary**: LM Studio (local, gpt-oss-20b / qwen3.8-9b / gemma4-12b)",
        "- **Fallback 1**: llama.cpp (Raspberry Pi, qwen3.8-9b)",
        "- **Fallback 2**: Ollama (Raspberry Pi, qwen3.8-9b-pi / qwen3-4b-pi / noema-2b)",
        "- **Fallback 3**: Gemini (free tier, gemini-2.5-flash)",
        "- **Fallback 4**: Mistral (free tier, open-mistral-nemo)",
        "- **Fallback 5**: Deterministic Python (always available)",
    ])
    
    return lines


def _format_model_name(model: str) -> str:
    """Format model name for display."""
    if not model or model == "deterministic_fallback":
        return "Python (deterministic)"
    model_lower = model.lower()
    if "gpt-oss" in model_lower:
        return f"gpt-oss (OpenAI OSS)"
    elif "qwen" in model_lower:
        return f"qwen (Alibaba)"
    elif "gemma" in model_lower:
        return f"gemma (Google)"
    elif "mistral" in model_lower:
        return f"mistral (Mistral AI)"
    elif "gemini" in model_lower:
        return f"gemini (Google)"
    elif "finr1" in model_lower:
        return "finr1 (math reasoning)"
    elif "qwen" in model_lower and ("3.8" in model_lower or "9b" in model_lower):
        return f"qwen3.8-9b (Alibaba)"
    else:
        return model

def write_human_brief(brief_content: str, output_dir: Path = Path("reports/current")) -> Path:
    """Write the intelligence brief (Phase 4 target topology: current/).

    The legacy reports/summary/ path is retired; per-run copies live in
    archive/<run_id>/ via write_reports(). Existing summary/ files on disk
    are left untouched (stale, not rewritten).
    """
    output_dir = Path("reports") / "current"
    output_dir.mkdir(parents=True, exist_ok=True)

    brief_path = output_dir / "portfolio_intelligence_brief.md"
    brief_path.write_text(brief_content, encoding="utf-8")

    logger.info("Intelligence brief written: %s", brief_path)

    return brief_path