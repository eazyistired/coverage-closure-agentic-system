"""Coverage Gap Analyzer — CLI entry point.

Usage
-----
    python coverage_gap_analyzer.py --report inputs/my.report --wps CAN SPI
    python coverage_gap_analyzer.py                          # uses latest .report, all WPs
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _latest_report() -> str | None:
    reports = sorted(Path("inputs").glob("*.report"))
    return str(reports[-1]) if reports else None


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Analyze uncovered functional coverage bins using AI agents."
    )
    p.add_argument(
        "--report",
        help="Path to the vManager .report file. "
        "Defaults to the most recent *.report in inputs/.",
    )
    p.add_argument(
        "--wps",
        nargs="+",
        metavar="WP",
        help="Work packages to analyze (e.g. CAN SPI). "
        "Defaults to all WPs defined in config.yaml.",
    )
    p.add_argument(
        "--cgs",
        nargs="+",
        type=int,
        metavar="IDX",
        help="0-based indexes of covergroups to analyze (e.g. 0 1 4). "
        "Defaults to all uncovered covergroups. "
        "Most useful when a single WP is specified.",
    )
    p.add_argument(
        "--generate-kg",
        action="store_true",
        help="Build (or rebuild) the Knowledge Graph for each requested WP "
        "and exit. Does not run gap analysis.",
    )
    p.add_argument(
        "--skip-enrichment",
        action="store_true",
        help="When used with --generate-kg, skip the AI enrichment step "
        "and save a static-only graph (faster, no LLM cost).",
    )
    return p


def main() -> int:
    load_dotenv()
    args = _build_parser().parse_args()

    report_path = args.report or _latest_report()
    if not report_path:
        logger.error(
            "No .report file found. Use --report <path> or place a *.report in inputs/."
        )
        return 1
    if not Path(report_path).exists():
        logger.error("Report file not found: %s", report_path)
        return 1

    from src.middleware import get_all_wps, run_analysis, generate_knowledge_graphs

    wps = args.wps or get_all_wps()
    if not wps:
        logger.error("No work packages found. Check config.yaml or pass --wps.")
        return 1

    # ------------------------------------------------------------------ KG mode
    if args.generate_kg:
        logger.info("Knowledge Graph generation for WP(s): %s", wps)
        saved_kgs = generate_knowledge_graphs(wps, skip_enrichment=args.skip_enrichment)
        if not saved_kgs:
            logger.error("Knowledge Graph generation failed for all WP(s).")
            return 1
        logger.info("%d knowledge graph(s) written:", len(saved_kgs))
        for p in saved_kgs:
            logger.info("  -> %s", p)
        return 0

    # --------------------------------------------------------------- analysis mode
    logger.info("Report : %s", report_path)
    logger.info("WPs    : %s", wps)
    if args.cgs:
        logger.info("CG idx : %s", args.cgs)

    saved = asyncio.run(run_analysis(wps, report_path, cg_indices=args.cgs))

    if not saved:
        logger.error("All WP analyses failed. Check logs above.")
        return 1

    logger.info("%d gap report(s) written:", len(saved))
    for p in saved:
        logger.info("  -> %s", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
