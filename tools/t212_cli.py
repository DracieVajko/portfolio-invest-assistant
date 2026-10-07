"""Manual Trading 212 CLI home (Phase 2).

Thin wrapper over the canonical trading212 package for operator use:
health checks, AI analysis, and portfolio export (the commands used by
run_t212_analysis.bat / run_t212_export.bat via the root compatibility shims).

Read-only. Never places orders. Requires api.env with live credentials.
"""

from __future__ import annotations

from trading212.integration import Trading212Integration, create_integration

__all__ = ["Trading212Integration", "create_integration", "main"]


def main(argv: list[str] | None = None) -> int:
    """Manual entry: health / analyze / export. Returns process exit code."""
    import argparse

    parser = argparse.ArgumentParser(description="Manual read-only Trading 212 CLI.")
    parser.add_argument("--config", default="api.env")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--export", default=None)
    args = parser.parse_args(argv)

    t = create_integration(config_file=args.config)
    if t is None:
        print("ERROR: Trading212 integration unavailable (check api.env).")
        return 2
    if args.health:
        ok = t.health_check() if hasattr(t, "health_check") else t.get_account_summary()
        print("OK" if ok else "FAILED")
        return 0 if ok else 1
    if args.analyze:
        print(t.analyze_portfolio())
        return 0
    if args.export:
        ok = t.export_portfolio(args.export)
        return 0 if ok else 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
