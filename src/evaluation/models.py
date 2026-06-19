"""Pydantic models for the LLM-as-Judge evaluation pipeline."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Config models (mirrors eval_config.yaml structure)
# ---------------------------------------------------------------------------


class JudgeModelConfig(BaseModel):
    id: str
    provider: str
    temperature: Optional[float] = 0.0


class JudgeConfig(BaseModel):
    models: Dict[str, JudgeModelConfig]
    default: str
    rate_limit_delay_s: float = 2.0


class ExperimentModelConfig(BaseModel):
    label: str
    id: str
    provider: str
    temperature: Optional[float] = 0.1


class AblationConfig(BaseModel):
    kg_context: Dict[str, List[bool]]
    rag_chunk_sizes: List[int]


class SelfConsistencyConfig(BaseModel):
    repetitions: int = 3
    std_threshold: float = 0.5


class OutputConfig(BaseModel):
    directory: str = "outputs/evaluation"
    timestamped: bool = True


class EvalConfig(BaseModel):
    judge: JudgeConfig
    experiment: Dict[str, List[ExperimentModelConfig]]
    ablation: AblationConfig
    self_consistency: SelfConsistencyConfig
    output: OutputConfig


# ---------------------------------------------------------------------------
# Judge input / output models
# ---------------------------------------------------------------------------


class GapGroupContext(BaseModel):
    """Structured context passed to the judge for a single gap group."""

    covergroup_name: str
    parent_name: str
    parent_type: str  # "coverpoint" | "cross"
    uncovered_bins: List[str]
    sampled_on: List[str]

    # From gap report
    scenario_description: str
    likely_root_cause: str

    # Extracted from KG — only the nodes relevant to this gap group
    kg_excerpt: str = Field(
        default="", description="Relevant KG nodes as formatted text."
    )

    # Extracted from coverage report — bin counts for this coverpoint/cross
    coverage_excerpt: str = Field(
        default="", description="Coverage report lines for this group."
    )


class DimensionScore(BaseModel):
    score: Optional[int] = Field(
        default=None,
        description="Score 1–5. Null when dimension is not applicable (e.g. cross_coherence for coverpoints).",
    )
    justification: str = Field(
        default="N/A",
        description="One-sentence justification citing a specific KG identifier or bin name.",
    )


class GapGroupScores(BaseModel):
    covergroup_name: str
    parent_name: str
    parent_type: str
    scores: Dict[str, DimensionScore]
    composite_score: Optional[float] = None

    def compute_composite(self) -> float:
        """Compute mean of non-null dimension scores and set composite_score."""
        values = [d.score for d in self.scores.values() if d.score is not None]
        self.composite_score = round(sum(values) / len(values), 2) if values else 0.0
        return self.composite_score


class EvalResult(BaseModel):
    """Raw scored output for a single gap report file."""

    wp: str
    source_gap_report: str
    coverage_analyser_llm: str
    judge_model: str
    evaluated_at: str
    gap_groups: List[GapGroupScores] = Field(default_factory=list)


class DimensionAggregate(BaseModel):
    mean: float
    std: float


class ByTypeAggregate(BaseModel):
    mean_composite: float
    count: int


class EvalSummary(BaseModel):
    """Aggregated scores per WP from one or more EvalResult files."""

    wp: str
    judge_model: str
    source_gap_report: str
    aggregate: Dict[str, Any] = Field(default_factory=dict)


class ComparisonRow(BaseModel):
    """One row in the multi-model comparison table."""

    wp: str
    covergroup_name: str
    parent_name: str
    coverage_analyser_llm: str
    judge_model: str
    factual_grounding: Optional[int] = None
    bin_coverage: Optional[int] = None
    root_cause_specificity: Optional[int] = None
    actionability: Optional[int] = None
    cross_coherence: Optional[int] = None
    composite_score: Optional[float] = None
