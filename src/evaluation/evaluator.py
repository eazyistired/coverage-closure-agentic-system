"""Evaluator — orchestrates one full evaluation run for a single gap report.

Usage
-----
    from src.evaluation.evaluator import Evaluator, load_eval_config

    config = load_eval_config()
    ev = Evaluator(config)
    result, summary = ev.evaluate(
        gap_report_path=Path("outputs/coverage-gap-reports/HSS_..._gap_report.json"),
        report_path=Path("inputs/data_16-05-2026_08~32~57.report"),
        judge_key="primary",   # key in eval_config.yaml → judge.models
    )
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from dotenv import load_dotenv

from src.evaluation.context_builder import (
    iter_gap_group_contexts,
    load_gap_report,
    load_kg,
)
from src.evaluation.judge_agent import JudgeAgent
from src.evaluation.models import (
    ByTypeAggregate,
    DimensionAggregate,
    EvalConfig,
    EvalResult,
    EvalSummary,
    GapGroupScores,
)
from src.evaluation.rubric import DIMENSION_ORDER

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def load_eval_config(config_path: Optional[Path] = None) -> EvalConfig:
    """Load and validate eval_config.yaml."""
    path = config_path or (_ROOT / "eval_config.yaml")
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return EvalConfig(**raw)


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


def _aggregate_scores(gap_groups: List[GapGroupScores]) -> Dict[str, Any]:
    """Compute mean/std per dimension and by type from a list of scored gap groups."""
    by_dim: Dict[str, List[int]] = {d: [] for d in DIMENSION_ORDER}
    composites_cp: List[float] = []
    composites_cross: List[float] = []

    for gg in gap_groups:
        for dim in DIMENSION_ORDER:
            score_obj = gg.scores.get(dim)
            if score_obj and score_obj.score is not None:
                by_dim[dim].append(score_obj.score)
        if gg.composite_score is not None:
            if gg.parent_type == "cross":
                composites_cross.append(gg.composite_score)
            else:
                composites_cp.append(gg.composite_score)

    all_composites = composites_cp + composites_cross
    mean_composite = (
        round(sum(all_composites) / len(all_composites), 2) if all_composites else 0.0
    )

    dim_stats: Dict[str, Any] = {}
    for dim, vals in by_dim.items():
        if vals:
            mean = round(sum(vals) / len(vals), 2)
            std = round(statistics.stdev(vals), 2) if len(vals) > 1 else 0.0
        else:
            mean, std = 0.0, 0.0
        dim_stats[dim] = {"mean": mean, "std": std}

    return {
        "mean_composite": mean_composite,
        "by_dimension": dim_stats,
        "by_type": {
            "coverpoint": {
                "mean_composite": (
                    round(sum(composites_cp) / len(composites_cp), 2)
                    if composites_cp
                    else 0.0
                ),
                "count": len(composites_cp),
            },
            "cross": {
                "mean_composite": (
                    round(sum(composites_cross) / len(composites_cross), 2)
                    if composites_cross
                    else 0.0
                ),
                "count": len(composites_cross),
            },
        },
    }


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def _output_dir(config: EvalConfig) -> Path:
    d = _ROOT / config.output.directory
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_eval_result(result: EvalResult, config: EvalConfig) -> Path:
    out_dir = _output_dir(config)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = out_dir / f"{result.wp}_{ts}_eval_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result.model_dump(), f, indent=2)
    logger.info("Eval result saved: %s", out_path)
    return out_path


def _save_eval_summary(summary: EvalSummary, config: EvalConfig) -> Path:
    out_dir = _output_dir(config)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = out_dir / f"{summary.wp}_{ts}_eval_summary.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary.model_dump(), f, indent=2)
    logger.info("Eval summary saved: %s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# Main Evaluator
# ---------------------------------------------------------------------------


class Evaluator:
    """Runs a full LLM-as-Judge evaluation for one gap report."""

    def __init__(self, config: EvalConfig) -> None:
        self._config = config

    def _resolve_judge_model(
        self,
        judge_key: str,
        coverage_analyser_llm: str,
    ) -> Tuple[str, Any]:
        """Resolve judge config key, promoting secondary if subject == default judge."""
        judge_cfg = self._config.judge.models[judge_key]
        # Anti-self-favoritism: if the subject model is the same as the default judge, swap
        if (
            judge_key == self._config.judge.default
            and judge_cfg.id == coverage_analyser_llm
        ):
            other_keys = [k for k in self._config.judge.models if k != judge_key]
            if other_keys:
                alt_key = other_keys[0]
                logger.info(
                    "Subject model %s matches default judge — promoting '%s' judge to avoid self-favoritism",
                    coverage_analyser_llm,
                    alt_key,
                )
                judge_key = alt_key
                judge_cfg = self._config.judge.models[alt_key]
        return judge_key, judge_cfg

    def evaluate(
        self,
        gap_report_path: Path,
        report_path: Optional[Path] = None,
        judge_key: Optional[str] = None,
        kg_dir: Optional[Path] = None,
        force_judge_key: bool = False,
        delay_s: Optional[float] = None,
    ) -> Tuple[EvalResult, EvalSummary]:
        """Score all gap groups in one gap report.

        Parameters
        ----------
        gap_report_path:
            Path to the ``*_gap_report.json`` file to evaluate.
        report_path:
            Optional path to the vManager .report file for coverage excerpts.
        judge_key:
            Key in ``eval_config.yaml → judge.models``. Defaults to ``judge.default``.
        kg_dir:
            Optional override for the knowledge-graphs directory.
        force_judge_key:
            When ``True``, use ``judge_key`` as-is and skip anti-self-favoritism
            logic.  Useful when the secondary judge is inaccessible.
        delay_s:
            Seconds to sleep between consecutive judge API calls.  Overrides
            ``eval_config.yaml → judge.rate_limit_delay_s``.  Set to 0 to
            disable throttling.

        Returns
        -------
        Tuple[EvalResult, EvalSummary]
            Both objects are also written to ``outputs/evaluation/``.
        """
        load_dotenv()

        judge_key = judge_key or self._config.judge.default
        gap_report = load_gap_report(gap_report_path)
        wp = gap_report.get("wp", gap_report_path.stem.split("_")[0])
        coverage_analyser_llm = gap_report.get("coverage_analyser_llm", "unknown")

        # Resolve judge — optionally skip anti-self-favoritism
        if force_judge_key:
            judge_cfg = self._config.judge.models[judge_key]
            logger.info(
                "[Evaluator] Using judge '%s' (%s) — anti-self-favoritism bypassed",
                judge_key,
                judge_cfg.id,
            )
        else:
            _, judge_cfg = self._resolve_judge_model(judge_key, coverage_analyser_llm)
        judge = JudgeAgent(judge_cfg)

        # Resolve inter-call delay
        if delay_s is None:
            effective_delay = self._config.judge.rate_limit_delay_s
        else:
            effective_delay = delay_s

        kg = load_kg(wp, kg_dir)
        contexts = iter_gap_group_contexts(gap_report, kg, report_path)

        logger.info(
            "[Evaluator] WP=%s | gap_report=%s | judge=%s | %d gap group(s) — batching %d per request",
            wp,
            gap_report_path.name,
            judge_cfg.id,
            len(contexts),
            10,
        )

        scored: List[GapGroupScores] = judge.score_batch(contexts, batch_size=10)
        failed = sum(
            1 for s in scored if all(d.score is None for d in s.scores.values())
        )

        if failed:
            logger.warning(
                "[Evaluator] %d/%d gap group(s) could not be scored (judge API errors)",
                failed,
                len(contexts),
            )
        if failed == len(contexts):
            raise RuntimeError(
                f"Judge '{judge_cfg.id}' failed to score all {len(contexts)} gap groups. "
                "Check API access for this model."
            )

        now = datetime.now().isoformat(timespec="seconds")
        result = EvalResult(
            wp=wp,
            source_gap_report=gap_report_path.name,
            coverage_analyser_llm=coverage_analyser_llm,
            judge_model=judge_cfg.id,
            evaluated_at=now,
            gap_groups=scored,
        )

        aggregate = _aggregate_scores(scored)
        summary = EvalSummary(
            wp=wp,
            judge_model=judge_cfg.id,
            source_gap_report=gap_report_path.name,
            aggregate=aggregate,
        )

        _save_eval_result(result, self._config)
        _save_eval_summary(summary, self._config)

        return result, summary
