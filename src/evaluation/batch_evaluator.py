"""Batch evaluator — runs evaluation scenarios across multiple gap reports/models.

Implements Scenarios 1, 2, 3, and 4 from the LLM-as-Judge evaluation plan.

Scenario 1 — Multi-model comparison
    Runs the analyzer for every subject model in eval_config, then scores all
    produced gap reports and writes a flat comparison table.

Scenario 2 — KG ablation
    Runs the analyzer twice for the same WP (KG on / KG off) and scores both
    outputs, adding a ``kg_context`` column to the comparison table.

Scenario 3 — Judge self-consistency
    Submits the same gap report to the judge N times (N = eval_config
    self_consistency.repetitions) using temperature=0.0 and computes the
    standard deviation of scores per dimension.

Scenario 4 — Cross vs Coverpoint Quality
    Scores a pre-produced gap report and splits the results by parent_type
    (coverpoint vs cross), producing per-type aggregate stats and a
    type-breakdown report.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.evaluation.evaluator import Evaluator, _output_dir, load_eval_config
from src.evaluation.models import (
    ComparisonRow,
    EvalConfig,
    EvalResult,
    EvalSummary,
    GapGroupScores,
    JudgeModelConfig,
)
from src.evaluation.rubric import DIMENSION_ORDER

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _save_comparison(rows: List[Dict[str, Any]], config: EvalConfig) -> Path:
    out_dir = _output_dir(config)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = out_dir / f"eval_comparison_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    logger.info("Comparison table saved: %s", out_path)
    return out_path


def _save_consistency(report: Dict[str, Any], config: EvalConfig, wp: str) -> Path:
    out_dir = _output_dir(config)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = out_dir / f"{wp}_{ts}_consistency_report.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    logger.info("Consistency report saved: %s", out_path)
    return out_path


def _save_type_breakdown(report: Dict[str, Any], config: EvalConfig, wp: str) -> Path:
    out_dir = _output_dir(config)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = out_dir / f"{wp}_{ts}_type_breakdown.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    logger.info("Type breakdown report saved: %s", out_path)
    return out_path


def _gg_scores_to_row(
    gg: GapGroupScores,
    wp: str,
    coverage_analyser_llm: str,
    judge_model: str,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "wp": wp,
        "covergroup_name": gg.covergroup_name,
        "parent_name": gg.parent_name,
        "coverage_analyser_llm": coverage_analyser_llm,
        "judge_model": judge_model,
        "composite_score": gg.composite_score,
    }
    for dim in DIMENSION_ORDER:
        score_obj = gg.scores.get(dim)
        row[dim] = score_obj.score if score_obj else None
    return row


# ---------------------------------------------------------------------------
# Scenario 1 — Multi-model comparison
# ---------------------------------------------------------------------------


def _run_analyzer_for_model(
    model_cfg: Any,
    wps: List[str],
    report_path: str,
    use_kg: bool = True,
) -> List[Path]:
    """Run middleware.run_analysis() with LLM_MODEL set to model_cfg.id.

    The model ID is injected via the environment variable ``LLM_MODEL`` so
    that BaseAgent picks it up without any code changes.
    """
    from src.middleware import run_analysis

    # Override LLM_MODEL for this subject run
    original = os.environ.get("LLM_MODEL")
    os.environ["LLM_MODEL"] = model_cfg.id
    logger.info(
        "[Scenario] Running analyzer with model '%s' (label: %s) on WPs: %s | kg_context=%s",
        model_cfg.id,
        model_cfg.label,
        wps,
        use_kg,
    )
    try:
        saved_paths = asyncio.run(
            run_analysis(wps=wps, report_path=report_path, use_kg=use_kg)
        )
    finally:
        # Restore original value
        if original is None:
            os.environ.pop("LLM_MODEL", None)
        else:
            os.environ["LLM_MODEL"] = original
    return saved_paths


def run_scenario_1(
    wps: List[str],
    report_path: str,
    config_path: Optional[Path] = None,
) -> Path:
    """Run the analyzer for every subject model in eval_config, then score all outputs.

    For each model in ``eval_config.yaml → experiment.models``:
    1. Runs ``middleware.run_analysis()`` with ``LLM_MODEL`` set to that model's ID.
    2. Scores each produced gap report with the default judge (anti-self-favoritism applies).
    3. Writes a flat ``eval_comparison_<ts>.json`` table.

    Parameters
    ----------
    wps:
        Work package names to analyse (e.g. ["HSS"]).
    report_path:
        Path to the vManager .report file.
    config_path:
        Optional override for eval_config.yaml location.

    Returns
    -------
    Path
        Path to the saved ``eval_comparison_<ts>.json`` file.
    """
    config = load_eval_config(config_path)
    evaluator = Evaluator(config)
    report_path_obj = Path(report_path) if report_path else None
    all_rows: List[Dict[str, Any]] = []

    subject_models = config.experiment.get("models", [])
    if not subject_models:
        raise ValueError("No experiment.models defined in eval_config.yaml")

    for model_cfg in subject_models:
        # Step 1: produce gap reports
        gap_report_paths = _run_analyzer_for_model(model_cfg, wps, report_path)

        if not gap_report_paths:
            logger.warning(
                "[Scenario 1] No gap reports produced for model '%s' — skipping evaluation",
                model_cfg.label,
            )
            continue

        # Step 2: score each produced report
        for grp in gap_report_paths:
            logger.info("[Scenario 1] Evaluating %s", grp.name)
            try:
                result, _ = evaluator.evaluate(
                    gap_report_path=grp,
                    report_path=report_path_obj,
                    judge_key=config.judge.default,
                )
            except RuntimeError as exc:
                logger.error(
                    "[Scenario 1] Judge failed for %s — skipping: %s", grp.name, exc
                )
                continue
            for gg in result.gap_groups:
                all_rows.append(
                    _gg_scores_to_row(
                        gg, result.wp, result.coverage_analyser_llm, result.judge_model
                    )
                )

    return _save_comparison(all_rows, config)


# ---------------------------------------------------------------------------
# Scenario 2 — KG ablation
# ---------------------------------------------------------------------------


def run_scenario_2(
    wps: List[str],
    report_path: str,
    config_path: Optional[Path] = None,
) -> Path:
    """Run the analyzer twice for the same WP — once with KG, once without — then score both.

    Both runs use the first (primary) subject model from ``experiment.models``.
    The ``kg_context`` values to sweep are read from
    ``eval_config.yaml → ablation.kg_context.enabled_values``.
    The produced gap reports carry a ``kg_context`` metadata field, which is
    propagated into every comparison table row.

    Parameters
    ----------
    wps:
        Work package names to analyse (e.g. ["HSS"]).
    report_path:
        Path to the vManager .report file.
    config_path:
        Optional override for eval_config.yaml location.

    Returns
    -------
    Path
        Path to the saved comparison JSON with a ``kg_context`` column.
    """
    config = load_eval_config(config_path)
    evaluator = Evaluator(config)
    report_path_obj = Path(report_path) if report_path else None
    all_rows: List[Dict[str, Any]] = []

    subject_models = config.experiment.get("models", [])
    if not subject_models:
        raise ValueError("No experiment.models defined in eval_config.yaml")

    # Use the first (primary) subject model for both ablation runs
    model_cfg = subject_models[0]
    logger.info(
        "[Scenario 2] KG ablation using model '%s' on WPs: %s",
        model_cfg.label,
        wps,
    )

    kg_values: List[bool] = config.ablation.kg_context.get(
        "enabled_values", [True, False]
    )
    for use_kg in kg_values:
        gap_report_paths = _run_analyzer_for_model(
            model_cfg, wps, report_path, use_kg=use_kg
        )
        if not gap_report_paths:
            logger.warning(
                "[Scenario 2] No gap reports produced for kg_context=%s — skipping",
                use_kg,
            )
            continue
        for grp in gap_report_paths:
            logger.info("[Scenario 2] Evaluating %s (kg_context=%s)", grp.name, use_kg)
            result, _ = evaluator.evaluate(
                gap_report_path=grp,
                report_path=report_path_obj,
                judge_key=config.judge.default,
            )
            for gg in result.gap_groups:
                row = _gg_scores_to_row(
                    gg, result.wp, result.coverage_analyser_llm, result.judge_model
                )
                row["kg_context"] = use_kg
                all_rows.append(row)

    return _save_comparison(all_rows, config)


# ---------------------------------------------------------------------------
# Scenario 3 — Judge self-consistency
# ---------------------------------------------------------------------------


def run_scenario_3(
    gap_report_path: Path,
    report_path: Optional[Path] = None,
    config_path: Optional[Path] = None,
    judge_key: Optional[str] = None,
    force_judge_key: bool = False,
    delay_s: Optional[float] = None,
) -> Path:
    """Submit the same gap report to the judge N times and compute score variance.

    Returns
    -------
    Path
        Path to the saved ``*_consistency_report.json`` file.
    """
    config = load_eval_config(config_path)
    n = config.self_consistency.repetitions
    std_threshold = config.self_consistency.std_threshold
    resolved_judge_key = judge_key or config.judge.default

    logger.info(
        "[Scenario 3] Self-consistency test: %d repetitions | threshold=%.2f",
        n,
        std_threshold,
    )

    evaluator = Evaluator(config)

    # Collect per-run scored lists
    all_runs: List[List[GapGroupScores]] = []
    for i in range(1, n + 1):
        logger.info("[Scenario 3] Run %d/%d", i, n)
        result, _ = evaluator.evaluate(
            gap_report_path=gap_report_path,
            report_path=report_path,
            judge_key=resolved_judge_key,
            force_judge_key=force_judge_key,
            delay_s=delay_s,
        )
        all_runs.append(result.gap_groups)

    # Use the actual judge id from the last result for the report
    judge_id = all_runs[0][0].scores and resolved_judge_key  # fallback
    if all_runs:
        judge_id = config.judge.models[resolved_judge_key].id

    # Compute per-dimension std across runs (per gap group)
    # Organise: {parent_name → {dimension → [scores across runs]}}
    per_group: Dict[str, Dict[str, List[Optional[int]]]] = {}
    for run_results in all_runs:
        for gg in run_results:
            key = f"{gg.covergroup_name}/{gg.parent_name}"
            if key not in per_group:
                per_group[key] = {d: [] for d in DIMENSION_ORDER}
            for dim in DIMENSION_ORDER:
                s = gg.scores.get(dim)
                per_group[key][dim].append(s.score if s else None)

    # Aggregate std per dimension
    dim_stds: Dict[str, List[float]] = {d: [] for d in DIMENSION_ORDER}
    group_reports = []
    for group_key, dims in per_group.items():
        group_entry: Dict[str, Any] = {"group": group_key, "dimensions": {}}
        for dim, scores in dims.items():
            non_null = [s for s in scores if s is not None]
            std = round(statistics.stdev(non_null), 3) if len(non_null) > 1 else 0.0
            reliable = std < std_threshold
            group_entry["dimensions"][dim] = {
                "scores_across_runs": scores,
                "std": std,
                "reliable": reliable,
            }
            if non_null:
                dim_stds[dim].append(std)
        group_reports.append(group_entry)

    # Overall per-dimension reliability
    overall: Dict[str, Any] = {}
    for dim, stds in dim_stds.items():
        mean_std = round(sum(stds) / len(stds), 3) if stds else 0.0
        overall[dim] = {
            "mean_std": mean_std,
            "reliable": mean_std < std_threshold,
        }

    # Peek at wp from the last result
    wp = (
        all_runs[0][0].covergroup_name.split("_")[1]
        if all_runs and all_runs[0]
        else "UNKNOWN"
    )

    report: Dict[str, Any] = {
        "scenario": "self_consistency",
        "source_gap_report": gap_report_path.name,
        "judge_model": judge_id,
        "repetitions": n,
        "std_threshold": std_threshold,
        "overall_dimension_reliability": overall,
        "per_group_detail": group_reports,
    }

    return _save_consistency(report, config, wp)


# ---------------------------------------------------------------------------
# Scenario 4 — Cross vs Coverpoint Quality
# ---------------------------------------------------------------------------


def run_scenario_4(
    gap_report_path: Path,
    report_path: Optional[Path] = None,
    config_path: Optional[Path] = None,
    judge_key: Optional[str] = None,
    force_judge_key: bool = False,
    delay_s: Optional[float] = None,
) -> Path:
    """Score a gap report and split results by parent_type (coverpoint vs cross).

    No analyzer run is needed — this scenario operates on a pre-produced gap
    report.  The judge scores every gap group, then the results are partitioned
    by ``parent_type`` and per-type aggregate stats are computed per dimension.

    Parameters
    ----------
    gap_report_path:
        Path to the ``*_gap_report.json`` file to evaluate.
    report_path:
        Optional path to the vManager .report file for coverage excerpts.
    config_path:
        Optional override for eval_config.yaml location.
    judge_key:
        Judge key from eval_config.yaml. Defaults to ``judge.default``.
    force_judge_key:
        Skip anti-self-favoritism logic.
    delay_s:
        Seconds between judge API calls.

    Returns
    -------
    Path
        Path to the saved ``*_type_breakdown.json`` file.
    """
    config = load_eval_config(config_path)
    evaluator = Evaluator(config)
    resolved_key = judge_key or config.judge.default

    logger.info(
        "[Scenario 4] Scoring %s for cross vs coverpoint breakdown",
        gap_report_path.name,
    )
    result, _ = evaluator.evaluate(
        gap_report_path=gap_report_path,
        report_path=report_path,
        judge_key=resolved_key,
        force_judge_key=force_judge_key,
        delay_s=delay_s,
    )

    # Partition scored gap groups by type
    by_type: Dict[str, List[GapGroupScores]] = {"coverpoint": [], "cross": []}
    for gg in result.gap_groups:
        bucket = "cross" if gg.parent_type == "cross" else "coverpoint"
        by_type[bucket].append(gg)

    def _type_stats(groups: List[GapGroupScores]) -> Dict[str, Any]:
        if not groups:
            return {"count": 0, "mean_composite": None, "by_dimension": {}}
        composites = [
            g.composite_score for g in groups if g.composite_score is not None
        ]
        mean_composite = (
            round(sum(composites) / len(composites), 2) if composites else None
        )
        by_dim: Dict[str, Any] = {}
        for dim in DIMENSION_ORDER:
            vals = [
                g.scores[dim].score
                for g in groups
                if dim in g.scores and g.scores[dim].score is not None
            ]
            if vals:
                mean = round(sum(vals) / len(vals), 2)
                std = round(statistics.stdev(vals), 2) if len(vals) > 1 else 0.0
            else:
                mean, std = None, None
            by_dim[dim] = {"mean": mean, "std": std, "n": len(vals)}
        return {
            "count": len(groups),
            "mean_composite": mean_composite,
            "by_dimension": by_dim,
        }

    # Per-group detail rows (reuse _gg_scores_to_row format, add parent_type)
    detail_rows: List[Dict[str, Any]] = []
    for gg in result.gap_groups:
        row = _gg_scores_to_row(
            gg, result.wp, result.coverage_analyser_llm, result.judge_model
        )
        row["parent_type"] = gg.parent_type
        detail_rows.append(row)

    report_data: Dict[str, Any] = {
        "scenario": "cross_vs_coverpoint",
        "source_gap_report": gap_report_path.name,
        "coverage_analyser_llm": result.coverage_analyser_llm,
        "judge_model": result.judge_model,
        "evaluated_at": result.evaluated_at,
        "aggregate": {
            "coverpoint": _type_stats(by_type["coverpoint"]),
            "cross": _type_stats(by_type["cross"]),
        },
        "detail": detail_rows,
    }

    return _save_type_breakdown(report_data, config, result.wp)
