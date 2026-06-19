"""Judge agent — calls the LLM judge and parses structured score output.

The judge receives a fully assembled prompt (built by ``context_builder`` +
``rubric``) and must return a JSON object with one key per scoring dimension.

All model parameters come from the ``EvalConfig`` loaded from ``eval_config.yaml``.
LLM connection parameters (API key, base URL) come from environment variables.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from src.evaluation.models import (
    DimensionScore,
    EvalConfig,
    GapGroupContext,
    GapGroupScores,
    JudgeModelConfig,
)
from src.evaluation.rubric import DIMENSION_ORDER, build_rubric_prompt_block

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a rigorous functional-verification expert acting as an automated evaluation judge.
Your task is to score the output of an AI coverage-gap analyzer against a structured rubric.

Rules:
- You have access to the knowledge graph (KG) excerpt and the coverage report excerpt.
- For every factual claim in the system output, you must verify it against the KG.
  If an identifier does not appear in the KG excerpt, treat it as potentially hallucinated.
- Never award a high score by default. High scores must be explicitly justified.
- Your justification for each dimension MUST cite a specific KG identifier, bin name,
  or signal name as evidence.
- Respond ONLY with the required JSON object. No additional text.
"""


def _build_judge_prompt(ctx: GapGroupContext) -> str:
    """Assemble the full judge prompt for one gap group."""
    uncovered = ", ".join(ctx.uncovered_bins) if ctx.uncovered_bins else "(none)"
    sampled = ", ".join(ctx.sampled_on) if ctx.sampled_on else "(none)"

    return f"""\
[CONTEXT: COVERAGE REPORT EXCERPT]
Covergroup: {ctx.covergroup_name}
Coverpoint/Cross: {ctx.parent_name} (type: {ctx.parent_type})
Uncovered bins: {uncovered}
Sampled on: {sampled}

[CONTEXT: KNOWLEDGE GRAPH EXCERPT]
{ctx.kg_excerpt}

[CONTEXT: RAW COVERAGE REPORT LINES]
{ctx.coverage_excerpt}

[SYSTEM OUTPUT TO EVALUATE]
scenario_description: "{ctx.scenario_description}"
likely_root_cause: "{ctx.likely_root_cause}"

[TASK]
{build_rubric_prompt_block()}
"""


def _parse_scores(raw: str, ctx: GapGroupContext) -> Dict[str, DimensionScore]:
    """Extract dimension scores from the raw LLM response string."""
    # Strip markdown code fences if present
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
    try:
        data: Dict[str, Any] = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error(
            "Failed to parse judge response as JSON: %s\nRaw: %s", exc, raw[:500]
        )
        # Return all-null scores on parse failure
        return {
            dim: DimensionScore(score=None, justification="Parse error — see logs")
            for dim in DIMENSION_ORDER
        }

    return _parse_scores_from_dict(data, ctx)


def _parse_scores_from_dict(
    data: Dict[str, Any], ctx: GapGroupContext
) -> Dict[str, DimensionScore]:
    """Build dimension scores from an already-parsed dict."""
    scores: Dict[str, DimensionScore] = {}
    for dim in DIMENSION_ORDER:
        raw_dim = data.get(dim, {})
        score_val = raw_dim.get("score")
        just_val = raw_dim.get("justification", "")

        # Null score for cross_coherence on coverpoints
        if dim == "cross_coherence" and ctx.parent_type != "cross":
            score_val = None
            just_val = "N/A - coverpoint"

        scores[dim] = DimensionScore(score=score_val, justification=just_val)
    return scores


def _build_batch_prompt(ctxs: List[GapGroupContext]) -> str:
    """Assemble a single judge prompt that scores N gap groups at once."""
    parts = []
    for i, ctx in enumerate(ctxs):
        uncovered = ", ".join(ctx.uncovered_bins) if ctx.uncovered_bins else "(none)"
        sampled = ", ".join(ctx.sampled_on) if ctx.sampled_on else "(none)"
        parts.append(f"""--- Gap Group {i} ---
[CONTEXT: COVERAGE REPORT EXCERPT]
Covergroup: {ctx.covergroup_name}
Coverpoint/Cross: {ctx.parent_name} (type: {ctx.parent_type})
Uncovered bins: {uncovered}
Sampled on: {sampled}

[CONTEXT: KNOWLEDGE GRAPH EXCERPT]
{ctx.kg_excerpt}

[CONTEXT: RAW COVERAGE REPORT LINES]
{ctx.coverage_excerpt}

[SYSTEM OUTPUT TO EVALUATE]
scenario_description: "{ctx.scenario_description}"
likely_root_cause: "{ctx.likely_root_cause}"
""")
    groups_block = "\n".join(parts)
    return f"""\
{groups_block}
[TASK]
Score each of the {len(ctxs)} gap groups above using the rubric below.
Return a single JSON object with integer string keys "0" through "{len(ctxs) - 1}".
Each value must be the standard per-dimension scoring object.

{build_rubric_prompt_block()}
"""


# Models that reject the temperature parameter (fixed temperature on server side).
_NO_TEMPERATURE_MODELS = frozenset(
    {
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-5.3-chat",
    }
)


class JudgeAgent:
    """Calls the LLM judge and returns parsed ``GapGroupScores``."""

    def __init__(self, judge_model_cfg: JudgeModelConfig) -> None:
        load_dotenv()
        self._model_id = judge_model_cfg.id

        api_key = os.environ.get("OPENAI_API_KEY", "")
        api_base = os.environ.get("OPENAI_API_BASE", "")
        verify_ssl = os.environ.get("VERIFY_SSL", "true").lower() != "false"
        ssl_ca = os.environ.get("SSL_CA_BUNDLE", "")

        http_client_kwargs: Dict[str, Any] = {}
        if not verify_ssl:
            import httpx

            http_client_kwargs["http_client"] = httpx.Client(verify=False)
        elif ssl_ca:
            import httpx

            http_client_kwargs["http_client"] = httpx.Client(verify=ssl_ca)

        llm_kwargs: Dict[str, Any] = {
            "model": self._model_id,
            "openai_api_key": api_key,
            "openai_api_base": api_base or None,
            "model_kwargs": {"response_format": {"type": "json_object"}},
            **http_client_kwargs,
        }
        # Only pass temperature for models that accept it
        if self._model_id not in _NO_TEMPERATURE_MODELS:
            llm_kwargs["temperature"] = judge_model_cfg.temperature

        self._llm = ChatOpenAI(**llm_kwargs)

    def score(self, ctx: GapGroupContext) -> GapGroupScores:
        """Score a single gap group. Returns ``GapGroupScores`` with composite.

        On API errors (e.g. 403 Forbidden, rate-limit, network failure) the
        method logs the error and returns a ``GapGroupScores`` with all
        dimension scores set to ``None`` so the caller can continue.
        """
        prompt = _build_judge_prompt(ctx)
        logger.debug(
            "[JudgeAgent] Scoring %s / %s with model %s",
            ctx.covergroup_name,
            ctx.parent_name,
            self._model_id,
        )
        try:
            response = self._llm.invoke(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ]
            )
            raw = response.content if hasattr(response, "content") else str(response)
            scores = _parse_scores(raw, ctx)
        except Exception as exc:
            logger.error(
                "[JudgeAgent] Scoring failed for %s / %s with model %s: %s",
                ctx.covergroup_name,
                ctx.parent_name,
                self._model_id,
                exc,
            )
            scores = {
                dim: DimensionScore(
                    score=None, justification=f"Judge API error: {type(exc).__name__}"
                )
                for dim in DIMENSION_ORDER
            }

        result = GapGroupScores(
            covergroup_name=ctx.covergroup_name,
            parent_name=ctx.parent_name,
            parent_type=ctx.parent_type,
            scores=scores,
        )
        result.compute_composite()
        return result

    def _score_chunk(self, ctxs: List[GapGroupContext]) -> List[GapGroupScores]:
        """Send one batch request for a chunk of gap groups."""
        prompt = _build_batch_prompt(ctxs)
        logger.info(
            "[JudgeAgent] Batch scoring %d gap group(s) with model %s",
            len(ctxs),
            self._model_id,
        )
        null_scores = [
            self._null_result(ctx, "Judge API error: batch call failed") for ctx in ctxs
        ]
        try:
            response = self._llm.invoke(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ]
            )
            raw = response.content if hasattr(response, "content") else str(response)
            cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
            data: Dict[str, Any] = json.loads(cleaned)
        except Exception as exc:
            logger.error("[JudgeAgent] Batch call failed: %s", exc)
            return null_scores

        results: List[GapGroupScores] = []
        for i, ctx in enumerate(ctxs):
            raw_group = data.get(str(i), {})
            if not raw_group:
                logger.warning(
                    "[JudgeAgent] No scores returned for group %d (%s / %s)",
                    i,
                    ctx.covergroup_name,
                    ctx.parent_name,
                )
                results.append(self._null_result(ctx, "Missing in batch response"))
                continue
            scores = _parse_scores_from_dict(raw_group, ctx)
            gg = GapGroupScores(
                covergroup_name=ctx.covergroup_name,
                parent_name=ctx.parent_name,
                parent_type=ctx.parent_type,
                scores=scores,
            )
            gg.compute_composite()
            results.append(gg)
        return results

    def _null_result(self, ctx: GapGroupContext, reason: str) -> GapGroupScores:
        gg = GapGroupScores(
            covergroup_name=ctx.covergroup_name,
            parent_name=ctx.parent_name,
            parent_type=ctx.parent_type,
            scores={
                dim: DimensionScore(score=None, justification=reason)
                for dim in DIMENSION_ORDER
            },
        )
        gg.compute_composite()
        return gg

    def score_batch(
        self, ctxs: List[GapGroupContext], batch_size: int = 10
    ) -> List[GapGroupScores]:
        """Score all gap groups using batched LLM calls (batch_size groups per request)."""
        all_results: List[GapGroupScores] = []
        for start in range(0, len(ctxs), batch_size):
            chunk = ctxs[start : start + batch_size]
            all_results.extend(self._score_chunk(chunk))
        return all_results
