"""LLM-as-Judge Evaluation — CLI entry point.

Runs structured evaluation scenarios against pre-produced coverage gap reports.
This script is completely independent of coverage_gap_analyzer.py — it reads
already-produced gap reports from disk and does not re-run the analyzer.

Usage
-----
Scenario 1 — Multi-model comparison (runs analyzer per subject model, then scores all):
    python run_evaluation.py scenario1 \\
        --wps HSS \\
        --report inputs/data_16-05-2026_08~32~57.report

Scenario 2 — KG ablation (runs analyzer with KG on then off, then scores both):
    python run_evaluation.py scenario2 \\
        --wps HSS \\
        --report inputs/data_16-05-2026_08~32~57.report

Scenario 3 — Judge self-consistency (N repeated evaluations of the same report):
    python run_evaluation.py scenario3 \\
        --gap-report outputs/coverage-gap-reports/HSS_..._gap_report.json \\
        --report inputs/data_16-05-2026_08~32~57.report

Scenario 4 — Cross vs coverpoint quality (score existing report, split by type):
    python run_evaluation.py scenario4 \\
        --gap-report outputs/coverage-gap-reports/HSS_..._gap_report.json

Single gap report (quick one-shot evaluation with default judge):
    python run_evaluation.py evaluate \\
        --gap-report outputs/coverage-gap-reports/HSS_..._gap_report.json \\
        --report inputs/data_16-05-2026_08~32~57.report
"""

from __future__ import annotations

import argparse
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


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _resolve_path(raw: str) -> Path:
    """Accept absolute or workspace-relative paths."""
    p = Path(raw)
    if not p.is_absolute():
        p = Path.cwd() / p
    return p


def _latest_report() -> Path | None:
    candidates = sorted(Path("inputs").glob("*.report"))
    return candidates[-1] if candidates else None


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_evaluate(args: argparse.Namespace) -> None:
    """Single gap report evaluation with the default judge."""
    from src.evaluation.evaluator import Evaluator, load_eval_config

    config = load_eval_config()
    evaluator = Evaluator(config)

    gap_report_path = _resolve_path(args.gap_report)
    report_path = _resolve_path(args.report) if args.report else _latest_report()
    judge_key = args.judge or config.judge.default
    force = args.force_judge
    delay = args.delay

    logger.info(
        "Evaluating: %s (judge=%s, force=%s, delay=%.1fs)",
        gap_report_path.name,
        judge_key,
        force,
        delay if delay is not None else config.judge.rate_limit_delay_s,
    )
    result, summary = evaluator.evaluate(
        gap_report_path=gap_report_path,
        report_path=report_path,
        judge_key=judge_key,
        force_judge_key=force,
        delay_s=delay,
    )
    print(f"\nEvaluation complete.")
    print(f"  WP: {result.wp}")
    print(f"  Judge model: {result.judge_model}")
    print(f"  Gap groups scored: {len(result.gap_groups)}")
    if summary.aggregate:
        mean = summary.aggregate.get("mean_composite", "?")
        print(f"  Mean composite score: {mean}")
    print(f"\nOutputs written to: outputs/evaluation/")


def _cmd_scenario1(args: argparse.Namespace) -> None:
    """Multi-model comparison — run analyzer per subject model, then score all outputs."""
    from src.evaluation.batch_evaluator import run_scenario_1

    report_path = (
        str(_resolve_path(args.report)) if args.report else str(_latest_report())
    )

    logger.info(
        "[Scenario 1] Running analyzer for each subject model in eval_config.yaml"
    )
    out = run_scenario_1(wps=args.wps, report_path=report_path)
    print(f"\nScenario 1 complete. Comparison table: {out}")


def _cmd_scenario2(args: argparse.Namespace) -> None:
    """KG ablation — run analyzer with KG on and off, then score both outputs."""
    from src.evaluation.batch_evaluator import run_scenario_2

    report_path = (
        str(_resolve_path(args.report)) if args.report else str(_latest_report())
    )
    out = run_scenario_2(wps=args.wps, report_path=report_path)
    print(f"\nScenario 2 complete. Comparison table: {out}")


def _cmd_scenario3(args: argparse.Namespace) -> None:
    """Judge self-consistency — repeated evaluations of the same report."""
    from src.evaluation.batch_evaluator import run_scenario_3

    report_path = _resolve_path(args.report) if args.report else _latest_report()
    out = run_scenario_3(
        gap_report_path=_resolve_path(args.gap_report),
        report_path=report_path,
        judge_key=args.judge or None,
        force_judge_key=args.force_judge,
        delay_s=args.delay,
    )
    print(f"\nScenario 3 complete. Consistency report: {out}")


def _cmd_scenario4(args: argparse.Namespace) -> None:
    """Cross vs coverpoint quality — score an existing report and split by parent_type."""
    from src.evaluation.batch_evaluator import run_scenario_4

    report_path = _resolve_path(args.report) if args.report else _latest_report()
    out = run_scenario_4(
        gap_report_path=_resolve_path(args.gap_report),
        report_path=report_path,
        judge_key=args.judge or None,
        force_judge_key=args.force_judge,
        delay_s=args.delay,
    )
    print(f"\nScenario 4 complete. Type breakdown report: {out}")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_evaluation.py",
        description=(
            "LLM-as-Judge evaluation for Coverage Gap Analyzer output. "
            "Reads pre-produced gap reports and scores them against a structured rubric."
        ),
    )

    sub = p.add_subparsers(dest="command", required=True)

    # ---- evaluate (single report) ----------------------------------------
    ev = sub.add_parser(
        "evaluate", help="Score a single gap report with the default judge."
    )
    ev.add_argument(
        "--gap-report",
        required=True,
        metavar="PATH",
        help="Path to the *_gap_report.json file to evaluate.",
    )
    ev.add_argument(
        "--report",
        metavar="PATH",
        help="Path to the vManager .report file. Defaults to the latest file in inputs/.",
    )
    ev.add_argument(
        "--judge",
        metavar="KEY",
        default=None,
        help="Judge key from eval_config.yaml (e.g. 'primary', 'secondary'). Defaults to judge.default.",
    )
    ev.add_argument(
        "--force-judge",
        action="store_true",
        default=False,
        help="Skip anti-self-favoritism logic and use the specified judge as-is.",
    )
    ev.add_argument(
        "--delay",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Seconds to wait between judge API calls (overrides eval_config rate_limit_delay_s).",
    )
    ev.set_defaults(func=_cmd_evaluate)

    # ---- scenario1 --------------------------------------------------------
    s1 = sub.add_parser(
        "scenario1",
        help=(
            "Multi-model comparison: run the analyzer for every subject model in "
            "eval_config.yaml, then score all gap reports and produce a comparison table."
        ),
    )
    s1.add_argument(
        "--wps",
        nargs="+",
        required=True,
        metavar="WP",
        help="Work packages to analyse (e.g. HSS CAN). Must match entries in config.yaml.",
    )
    s1.add_argument(
        "--report",
        metavar="PATH",
        help="Path to the vManager .report file. Defaults to the latest in inputs/.",
    )
    s1.set_defaults(func=_cmd_scenario1)

    # ---- scenario2 --------------------------------------------------------
    s2 = sub.add_parser(
        "scenario2",
        help=(
            "KG ablation: run the analyzer twice (KG on + KG off) for the same WP "
            "and compare scores."
        ),
    )
    s2.add_argument(
        "--wps",
        nargs="+",
        required=True,
        metavar="WP",
        help="Work packages to analyse (e.g. HSS). Must match entries in config.yaml.",
    )
    s2.add_argument(
        "--report",
        metavar="PATH",
        help="Path to the vManager .report file. Defaults to the latest in inputs/.",
    )
    s2.set_defaults(func=_cmd_scenario2)

    # ---- scenario3 --------------------------------------------------------
    s3 = sub.add_parser(
        "scenario3",
        help="Judge self-consistency: submit the same report to the judge N times.",
    )
    s3.add_argument(
        "--gap-report",
        required=True,
        metavar="PATH",
        help="Path to the *_gap_report.json file to test for consistency.",
    )
    s3.add_argument(
        "--report",
        metavar="PATH",
        help="Path to the vManager .report file. Defaults to the latest in inputs/.",
    )
    s3.add_argument(
        "--judge",
        metavar="KEY",
        default=None,
        help="Judge key from eval_config.yaml (e.g. 'primary'). Defaults to judge.default.",
    )
    s3.add_argument(
        "--force-judge",
        action="store_true",
        default=False,
        help="Skip anti-self-favoritism logic.",
    )
    s3.add_argument(
        "--delay",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Seconds between judge API calls (overrides eval_config rate_limit_delay_s).",
    )
    s3.set_defaults(func=_cmd_scenario3)

    # ---- scenario4 --------------------------------------------------------
    s4 = sub.add_parser(
        "scenario4",
        help=(
            "Cross vs coverpoint quality: score an existing gap report and produce "
            "per-type aggregate stats (coverpoint vs cross)."
        ),
    )
    s4.add_argument(
        "--gap-report",
        required=True,
        metavar="PATH",
        help="Path to the *_gap_report.json file to evaluate.",
    )
    s4.add_argument(
        "--report",
        metavar="PATH",
        help="Path to the vManager .report file. Defaults to the latest in inputs/.",
    )
    s4.add_argument(
        "--judge",
        metavar="KEY",
        default=None,
        help="Judge key from eval_config.yaml (e.g. 'primary'). Defaults to judge.default.",
    )
    s4.add_argument(
        "--force-judge",
        action="store_true",
        default=False,
        help="Skip anti-self-favoritism logic.",
    )
    s4.add_argument(
        "--delay",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Seconds between judge API calls (overrides eval_config rate_limit_delay_s).",
    )
    s4.set_defaults(func=_cmd_scenario4)

    return p


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    load_dotenv()
    parser = _build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
