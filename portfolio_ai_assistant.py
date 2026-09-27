"""Command-line entry point for the portfolio investment report."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from investment_engine.config.settings import EngineSettings
from investment_engine.main import run_engine
from investment_engine.reporting.report_structure import (
    setup_debug_layer,
    save_ai_context_layer,
    generate_human_brief,
    write_human_brief,
    move_debug_outputs,
)


ROOT = Path(__file__).resolve().parent

logger = logging.getLogger(__name__)

REQUIRED_PYTHON_MAJOR = 3
REQUIRED_PYTHON_MINOR = 12


def check_python_version(version: tuple[int, int] | None = None) -> tuple[int, int]:
    """Fail early unless running on Python 3.12 (any patch version).

    Split out for testability: pass an explicit ``(major, minor)`` tuple in
    tests instead of depending on the running interpreter.
    """
    major, minor = version if version is not None else (sys.version_info.major, sys.version_info.minor)
    if (major, minor) != (REQUIRED_PYTHON_MAJOR, REQUIRED_PYTHON_MINOR):
        raise SystemExit(
            f"ERROR: Python {REQUIRED_PYTHON_MAJOR}.{REQUIRED_PYTHON_MINOR} is required "
            f"(found {major}.{minor}). Run with: py -3.12 portfolio_ai_assistant.py --config portfolio_config.json"
        )
    return (major, minor)


def setup_logging(log_folder: Path) -> Path:
    log_folder.mkdir(parents=True, exist_ok=True)
    run_log = log_folder / f"portfolio_{datetime.now():%Y-%m-%d_%H-%M-%S}.log"
    latest_log = log_folder / "latest_run.log"
    formatter = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()
    for handler in (logging.FileHandler(run_log, encoding="utf-8"), logging.StreamHandler(sys.stdout)):
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
    # A stable file makes debugging from the batch file and VS Code predictable.
    try:
        if latest_log.exists():
            latest_log.unlink()
        latest_log.hardlink_to(run_log)
    except OSError:
        # The hard link may be unavailable on some drives; write a copy on exit instead.
        logging.getLogger(__name__).debug("Could not create latest_run.log link; it will be copied at the end.")
    return run_log


def _atomic_write_text(dest: Path, text: str) -> None:
    """Write fully to a temp sibling, fsync, then atomically replace dest.

    newline="" is load-bearing: text-mode "\n"->CRLF translation on Windows
    used to make every manifest sha256/bytes mismatch the file on disk
    (finding 16 — deterministic on all 5 files, every run). Artifacts stay LF.
    """
    import os as _os

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".tmp.{dest.name}.{_os.getpid()}.part"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
        fh.flush()
        try:
            _os.fsync(fh.fileno())
        except OSError:
            pass
    _os.replace(tmp, dest)


def write_reports(
    result: dict,
    output_folder: Path,
    archive_folder: Path,
    run_id: str,
    failed_markdown: str = "",
) -> dict[str, Path]:
    """Atomic publish into latest/ + archive/. Returns {label: Path}.

    Every file is staged to a temp sibling and os.replace()d, so a crash or
    a concurrent run can never leave a half-written report behind.
    Also emits run_manifest.json (run_id, files+sha256, counts, status).
    """
    import hashlib as _hashlib

    # Phase 4 target topology: current/ holds this run; archive/<run_id>/ the copy.
    current_dir = output_folder / "current"
    run_archive_dir = archive_folder / run_id
    current_dir.mkdir(parents=True, exist_ok=True)
    run_archive_dir.mkdir(parents=True, exist_ok=True)
    payloads: dict[str, str] = {
        "portfolio_decision_brief.md": result.get("brief_markdown", ""),
        "t212_portfolio_snapshot.md": result.get("snapshot_markdown", ""),
        "portfolio_analysis.json": json.dumps(result, indent=2, ensure_ascii=False, default=str),
    }
    if failed_markdown:
        payloads["failed_tickers.md"] = failed_markdown
    if result.get("intelligence_brief_markdown"):
        payloads["portfolio_intelligence_brief.md"] = result["intelligence_brief_markdown"]
    if result.get("deep_dive_markdown"):
        payloads["portfolio_deep_dive.md"] = result["deep_dive_markdown"]

    published: dict[str, Path] = {}
    manifest_files: dict[str, dict] = {}
    for name, text in payloads.items():
        digest = _hashlib.sha256(text.encode("utf-8")).hexdigest()
        _atomic_write_text(current_dir / name, text)
        try:
            shutil.copy2(current_dir / name, run_archive_dir / name)
        except OSError:
            logger.warning("Archive copy failed for %s", name)
        published[name] = current_dir / name
        manifest_files[name] = {"sha256": digest, "bytes": len(text.encode("utf-8"))}

    recon = result.get("reconciliation") or {}
    # Phase 4: truthful provider attribution. manifest["provider"] carries the
    # actual per-stage winners; the human stance moves to manifest["stance"].
    from investment_engine.providers import attribution as _attr

    _stages = result.get("providers_per_stage") or {}
    _stance_value = (result.get("brief_metadata") or {}).get("stance", "")
    # Phase 3: record which indicator engine + library versions produced the
    # technicals (cross-PC parity). Phase 4 adds per-stage provider attribution.
    try:
        import importlib.util as _ilu

        _has_ta = _ilu.find_spec("pandas_ta") is not None
        _has_talib = _ilu.find_spec("talib") is not None
        import pandas as _pd
        import numpy as _np
        import scipy as _scipy

        _versions = {
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "pandas": _pd.__version__,
            "numpy": _np.__version__,
            "scipy": _scipy.__version__,
        }
    except Exception:
        _has_ta, _has_talib, _versions = False, False, {}
    _context_file = str(result.get("context_file", "") or "")
    _context_run_id = ""
    try:
        _stem = Path(_context_file).stem  # ai_context_<run_id>
        if _stem.startswith("ai_context_"):
            _context_run_id = _stem[len("ai_context_"):]
    except Exception:
        _context_run_id = ""
    _run_id_consistent = bool(_context_run_id) and _context_run_id == run_id
    if _context_file and not _run_id_consistent:
        # Non-blocking: the file exists but forked onto another run_id
        # (run 524fffa0: content 7e520fb5 vs wrapper 524fffa0). Visible here
        # and in verify_manifest.py instead of failing the whole run.
        logger.warning("Context run_id mismatch: file=%s wrapper=%s",
                       _context_run_id or "?", run_id)
    _finviz_sectors = 0
    try:
        _mr = result.get("market_regime") or {}
        _fv = (_mr.get("finviz_data") or {}) if isinstance(_mr, dict) else {}
        _sb = (_fv.get("sector_breadth") or {}) if isinstance(_fv, dict) else {}
        _finviz_sectors = len(_sb) if isinstance(_sb, dict) else 0
    except Exception:
        _finviz_sectors = 0
    manifest = {
        "run_id": run_id,
        "generated_at": datetime.now().astimezone().isoformat(),
        "recon_status": recon.get("status") if isinstance(recon, dict) else None,
        "context_run_id": _context_run_id,
        "run_id_consistent": _run_id_consistent,
        "finviz_sectors": _finviz_sectors,
        "files": manifest_files,
        "counts": {
            "failed_tickers": len(result.get("failed_tickers", []) or []),
            "monitoring_items": len(result.get("monitoring_items", []) or []),
            "decision_news": len(result.get("decision_news", []) or []),
            "corpus_items": ((result.get("research_corpus", {}) or {}).get("counts", {}) or {}).get("total", 0),
            "corpus_decision": ((result.get("research_corpus", {}) or {}).get("counts", {}) or {}).get("decision", 0),
            "corpus_rejected": ((result.get("research_corpus", {}) or {}).get("counts", {}) or {}).get("rejected", 0),
            "skill_outputs": len(result.get("skill_outputs", {}) or {}),
            "universe_resolved": ((result.get("universe", {}) or {}).get("counts", {}) or {}).get("resolved", 0),
            "playwright_technicals": (result.get("playwright", {}) or {}).get("technicals", 0),
            "playwright_earnings": (result.get("playwright", {}) or {}).get("earnings", 0),
            "playwright_facts": (result.get("playwright", {}) or {}).get("facts", 0),
            "tvnews_items": (result.get("tvnews", {}) or {}).get("items", 0),
            "tvnews_symbols": (result.get("tvnews", {}) or {}).get("symbols", 0),
            "dividends_tracked": len(result.get("dividends_12m", {}) or {}),
        },
        "research_corpus_run_id": (result.get("research_corpus", {}) or {}).get("run_id", ""),
        "skill_versions": result.get("skill_versions", {}) or {},
        "policy_version": ((result.get("investment_policy", {}) or {}).get("version", "")
                           or (result.get("investment_policy", {}) or {}).get("status", "")),
        "ai_execution_mode": result.get("api_execution_mode", "STANDARD"),
        "disabled_providers": result.get("disabled_providers", []) or [],
        "provider": _attr.summarize_winners(_stages) if _stages else _stance_value,
        "stance": _stance_value,
        "providers_per_stage": _stages,
        "indicator_engine": "reference" if _has_ta else "manual",
        "pandas_ta_present": _has_ta,
        "talib_present": _has_talib,
        "versions": _versions,
    }
    manifest_text = json.dumps(manifest, indent=2, ensure_ascii=False, default=str)
    _atomic_write_text(current_dir / "run_manifest.json", manifest_text)
    try:
        shutil.copy2(current_dir / "run_manifest.json",
                     run_archive_dir / "run_manifest.json")
    except OSError:
        logger.warning("Archive copy failed for run_manifest.json")
    published["run_manifest.json"] = current_dir / "run_manifest.json"

    result["brief_file"] = str(published["portfolio_decision_brief.md"])
    result["snapshot_file"] = str(published["t212_portfolio_snapshot.md"])
    return published


def main() -> int:
    check_python_version()
    parser = argparse.ArgumentParser(description="Generate a portfolio analysis report.")
    parser.add_argument("--config", default="portfolio_config.json", help="Path to the JSON configuration file.")
    parser.add_argument("--investment-engine", action="store_true", help="Run the modular investment engine.")
    parser.add_argument("--generate-full-report", action="store_true", help="Kept for compatibility; reports are always written.")
    parser.add_argument("--execution-mode", choices=["auto", "public", "locals"], default=None,
                        help="Provider classes for this run: auto (locals first, then public APIs), "
                             "public (public APIs only, locals never touched), "
                             "locals (local endpoints only + deterministic fallback). Overrides config.")
    parser.add_argument("--api-only", action="store_true",
                        help="Shorthand for --execution-mode public.")
    args = parser.parse_args()

    config_path = (ROOT / args.config).resolve() if not Path(args.config).is_absolute() else Path(args.config)
    if not config_path.is_file():
        print(f"ERROR: Config file not found: {config_path}", file=sys.stderr)
        return 2
    config = json.loads(config_path.read_text(encoding="utf-8"))
    raw_settings = config.get("settings", {})
    log_folder = ROOT / raw_settings.get("log_folder", "logs")
    run_log = setup_logging(log_folder)
    logger = logging.getLogger(__name__)

    # Setup debug layer with run_id
    import uuid as _uuid
    run_id = str(_uuid.uuid4())[:8]
    debug_paths = setup_debug_layer(run_id)

    try:
        settings = EngineSettings.from_mapping(raw_settings)
        # CLI overrides config (explicit flags win for this run only).
        if args.api_only:
            settings.api_only = True
        if args.execution_mode:
            settings.execution_mode = args.execution_mode
            if args.execution_mode == "public":
                settings.api_only = True
        assets = [asset for asset in config.get("assets", []) if asset.get("enabled", True)]
        if not assets:
            raise ValueError("No enabled assets were found in the configuration.")
        logger.info("Starting report for %d enabled assets; provider=%s, model=%s", len(assets), settings.provider, settings.lm_studio_model)
        result = run_engine(
            assets,
            portfolio_context={"config_path": str(config_path), "language": raw_settings.get("language", "English"), "run_id": run_id},
            settings=settings,
        )

        # Human-brief contract: run_engine() must return every key that
        # generate_human_brief() reads. Fail loudly instead of rendering
        # an empty brief (see tests/test_result_contract.py).
        _required_brief_keys = (
            "portfolio_rows", "monitoring_items", "regime_result",
            "decision_news", "earnings_7d", "ideas", "portfolio_names",
        )
        _missing_brief_keys = [k for k in _required_brief_keys if k not in result]
        if _missing_brief_keys:
            raise KeyError(
                "run_engine() result violates human-brief contract; "
                f"missing keys: {_missing_brief_keys}"
            )

        # Extract data needed for human brief (already computed in run_engine)
        portfolio_rows = result.get("portfolio_rows", [])
        monitoring_items = result.get("monitoring_items", [])
        regime_result = result.get("regime_result")
        t212_data = result.get("t212_data")
        decision_news = result.get("decision_news", [])
        earnings_7d = result.get("earnings_7d", {})
        ideas = result.get("ideas", [])
        portfolio_names = result.get("portfolio_names", {})
        reconciliation = result.get("reconciliation")
        monitoring_items = result.get("monitoring_items", [])

        # Generate human brief
        human_brief = generate_human_brief(
            result=result,
            portfolio_rows=portfolio_rows,
            monitoring_items=monitoring_items,
            regime_result=regime_result,
            t212_data=t212_data,
            decision_news=result.get("decision_news", []),
            earnings_7d=result.get("earnings_7d", {}),
            ideas=result.get("ideas", []),
            portfolio_names=result.get("portfolio_names", {}),
            reconciliation=result.get("reconciliation"),
        )

        # Phase 4: the human brief IS the intelligence brief — published into
        # current/portfolio_intelligence_brief.md by write_reports().
        result["intelligence_brief_markdown"] = human_brief
        # Write human brief to the current layer (target topology)
        write_human_brief(human_brief)

        # Failed-ticker report (empty string when everything resolved)
        from investment_engine.reporting.failed_tickers import (
            render_failed_tickers_md as _render_failed_md,
        )
        failed_markdown = _render_failed_md(result.get("failed_tickers", []) or [], run_id)
        if failed_markdown:
            logger.info("Unresolvable tickers: %d (see failed_tickers.md)",
                        len(result.get("failed_tickers", []) or []))

        # Write standard reports (atomic publish + manifest)
        published = write_reports(
            result,
            ROOT / raw_settings.get("output_folder", "reports"),
            ROOT / raw_settings.get("archive_folder", "reports/archive"),
            run_id,
            failed_markdown=failed_markdown,
        )
        brief_path = published["portfolio_decision_brief.md"]
        snapshot_path = published["t212_portfolio_snapshot.md"]
        json_path = published["portfolio_analysis.json"]

        # Save AI context layer
        context_file = result.get("context_file")
        if context_file and Path(context_file).exists():
            context_content = Path(context_file).read_text(encoding="utf-8")
            portfolio_analysis = {
                "total_equity": result.get("t212_data", {}).get("account_summary", {}).get("total_equity", 0),
                "positions": result.get("t212_data", {}).get("account_summary", {}).get("all_positions", []),
                "cash": result.get("t212_data", {}).get("cash", {}),
                "reconciliation": result.get("reconciliation"),
            }
            save_ai_context_layer(run_id, Path(context_file).read_text(encoding="utf-8"), portfolio_analysis)

        # Move debug outputs to debug layer
        move_debug_outputs(run_id, run_log, debug_paths)

        logger.info("Decision brief written: %s", brief_path)
        logger.info("Portfolio snapshot written: %s", snapshot_path)
        logger.info("Structured data written: %s", json_path)
        failures = ["portfolio_report"] if any(marker in result.get("brief_markdown", "").lower() for marker in ("lm studio error:", "returned no choices", "empty response")) else []
        if failures:
            logger.error("Model stages failed: %s", ", ".join(failures))
            print(f"\nERROR: Report was saved, but model stages failed: {', '.join(failures)}", file=sys.stderr)
            print(f"Log file: {run_log}", file=sys.stderr)
            return 1
        intel_path = published.get("portfolio_intelligence_brief.md", "")
        print(f"\nSUCCESS: Brief saved to {brief_path}")
        print(f"SUCCESS: Snapshot saved to {snapshot_path}")
        print(f"SUCCESS: Intelligence brief saved to {intel_path}")
        print(f"Log file: {run_log}")
        return 0
    except Exception:
        logger.exception("Report generation failed")
        print(f"\nERROR: Report generation failed. See log: {run_log}", file=sys.stderr)
        return 1
    finally:
        latest_log = log_folder / "latest_run.log"
        if not latest_log.exists() and run_log.exists():
            shutil.copy2(run_log, latest_log)
        # Best-effort VRAM cleanup: unload LM Studio models used this run.
        # Never breaks the exit path; servers without unload API just log.
        # Public/API-only runs never touch local endpoints (no unload calls at all).
        try:
            _s = locals().get("settings", None)
            try:
                from investment_engine.config.settings import resolve_execution_mode as _em
                _is_public = _em(_s) == "public"
            except Exception:
                _is_public = bool(getattr(_s, "api_only", False))
            if _is_public:
                logger.info("API-only mode: skipping LM Studio unload (locals never used)")
                _s = None
            _base = (getattr(_s, "lm_studio_base_url", "") or "").rstrip("/")
            _models = [m for m in (getattr(_s, "decision_model", ""),
                                   getattr(_s, "writer_model", "")) if m]
            if _base and _models:
                from investment_engine.providers.lmstudio import unload_lmstudio_models
                _unload_res = unload_lmstudio_models(_base, _models)
                logger.info("LM Studio unload at exit: %s", _unload_res)
        except Exception as _unload_exc:
            logger.debug("LM Studio unload skipped: %s", _unload_exc)


if __name__ == "__main__":
    raise SystemExit(main())
