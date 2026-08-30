"""Command-line entry point for the portfolio investment report."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

from investment_engine.config.settings import EngineSettings
from investment_engine.main import run_engine


ROOT = Path(__file__).resolve().parent


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


def write_reports(result: dict, output_folder: Path, archive_folder: Path) -> tuple[Path, Path]:
    output_folder.mkdir(parents=True, exist_ok=True)
    archive_folder.mkdir(parents=True, exist_ok=True)
    markdown_path = output_folder / "portfolio_analysis.md"
    json_path = output_folder / "portfolio_analysis.json"
    markdown_path.write_text(result["markdown"], encoding="utf-8")
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    shutil.copy2(markdown_path, archive_folder / f"{stamp}_portfolio_analysis.md")
    shutil.copy2(json_path, archive_folder / f"{stamp}_portfolio_analysis.json")
    return markdown_path, json_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a portfolio analysis report.")
    parser.add_argument("--config", default="portfolio_config.json", help="Path to the JSON configuration file.")
    parser.add_argument("--investment-engine", action="store_true", help="Run the modular investment engine.")
    parser.add_argument("--generate-full-report", action="store_true", help="Kept for compatibility; reports are always written.")
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

    try:
        settings = EngineSettings.from_mapping(raw_settings)
        assets = [asset for asset in config.get("assets", []) if asset.get("enabled", True)]
        if not assets:
            raise ValueError("No enabled assets were found in the configuration.")
        logger.info("Starting report for %d enabled assets; provider=%s, model=%s", len(assets), settings.provider, settings.lm_studio_model)
        result = run_engine(
            assets,
            portfolio_context={"config_path": str(config_path), "language": raw_settings.get("language", "English")},
            settings=settings,
        )
        markdown_path, json_path = write_reports(
            result,
            ROOT / raw_settings.get("output_folder", "reports"),
            ROOT / raw_settings.get("archive_folder", "reports/archive"),
        )
        logger.info("Report written: %s", markdown_path)
        logger.info("Structured data written: %s", json_path)
        failures = ["portfolio_report"] if any(marker in result.get("markdown", "").lower() for marker in ("lm studio error:", "returned no choices", "empty response")) else []
        if failures:
            logger.error("Model stages failed: %s", ", ".join(failures))
            print(f"\nERROR: Report was saved, but model stages failed: {', '.join(failures)}", file=sys.stderr)
            print(f"Log file: {run_log}", file=sys.stderr)
            return 1
        print(f"\nSUCCESS: Report saved to {markdown_path}")
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


if __name__ == "__main__":
    raise SystemExit(main())
