"""Rubric definition for the LLM-as-Judge evaluation.

Each DIMENSION entry contains:
  - name: short key used in output JSON
  - label: human-readable label for prompts and reports
  - description: what is being scored
  - poor (1): description of a score-1 response
  - excellent (5): description of a score-5 response
  - prompt_instruction: sentence injected into the judge prompt for this dimension

DIMENSION_ORDER defines the canonical scoring order.
COVERPOINT_ONLY_DIMENSIONS are excluded (set to null) for cross gap groups.
CROSS_ONLY_DIMENSIONS are excluded for coverpoint gap groups.
"""

from __future__ import annotations

from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Dimension definitions
# ---------------------------------------------------------------------------

DIMENSIONS: List[Dict[str, Any]] = [
    {
        "name": "factual_grounding",
        "label": "Factual Grounding",
        "description": (
            "Do all signal names, coverpoint names, and enum values in the explanation "
            "actually exist in the knowledge graph?"
        ),
        "poor": "Names are invented or wrong.",
        "excellent": "Every referenced identifier is verified in the KG.",
        "prompt_instruction": (
            "You MUST cite the exact KG identifier (variable name or enum value) when "
            "marking a claim as factually grounded. If an identifier in the output does "
            "not appear in the KG excerpt, score this dimension 1 or 2."
        ),
    },
    {
        "name": "bin_coverage",
        "label": "Bin Coverage",
        "description": (
            "Are all uncovered bins in the gap group addressed by the explanation?"
        ),
        "poor": "Only some bins mentioned.",
        "excellent": "All bins accounted for, including cross combinations.",
        "prompt_instruction": (
            "You MUST cite the specific uncovered bin name when assessing whether a bin "
            "is addressed. If any uncovered bin from the list is not addressed, "
            "reduce this score accordingly."
        ),
    },
    {
        "name": "root_cause_specificity",
        "label": "Root Cause Specificity",
        "description": ("Is the root cause precise and non-tautological?"),
        "poor": '"The mode was never exercised" (restates the bin without adding insight).',
        "excellent": (
            "Explains *why* the mode was not exercised — references a missing sequence, "
            "gating condition, or register dependency."
        ),
        "prompt_instruction": (
            "A tautological root cause simply restates that the bin was not covered "
            "without explaining the underlying mechanism. Such explanations must receive "
            "a score of 1 or 2."
        ),
    },
    {
        "name": "actionability",
        "label": "Actionability",
        "description": (
            "Could a verification engineer write a targeted test from this output alone?"
        ),
        "poor": "No direction given.",
        "excellent": (
            "Specific scenario implied: which mode, which condition, which sequence to create."
        ),
        "prompt_instruction": (
            "Score high only if the explanation specifies enough detail that a verification "
            "engineer could construct a concrete directed test without additional analysis."
        ),
    },
    {
        "name": "cross_coherence",
        "label": "Cross Coherence",
        "description": (
            "For cross coverage gaps, does the explanation address the *combination*, "
            "not just each individual signal?"
        ),
        "poor": "Treats each signal independently.",
        "excellent": "Correctly identifies the joint scenario that was never sampled.",
        "prompt_instruction": (
            "This dimension applies ONLY to cross coverage gaps (parent_type == 'cross'). "
            "For coverpoint gaps, set score to null and justification to 'N/A - coverpoint'. "
            "Score high only when the explanation explicitly addresses the *joint* condition "
            "formed by the cross, not merely its constituent coverpoints."
        ),
    },
]

DIMENSION_ORDER: List[str] = [d["name"] for d in DIMENSIONS]
CROSS_ONLY_DIMENSIONS: List[str] = ["cross_coherence"]


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

_RUBRIC_BLOCK_TEMPLATE = """\
## Scoring Rubric

Score each dimension from 1 (poor) to 5 (excellent).
For each dimension, you MUST provide a one-sentence justification.

{dimension_blocks}

## Output Format

Respond ONLY with a valid JSON object matching this schema:
{{
  "factual_grounding":       {{"score": <int 1-5>, "justification": "<one sentence>"}},
  "bin_coverage":            {{"score": <int 1-5>, "justification": "<one sentence>"}},
  "root_cause_specificity":  {{"score": <int 1-5>, "justification": "<one sentence>"}},
  "actionability":           {{"score": <int 1-5>, "justification": "<one sentence>"}},
  "cross_coherence":         {{"score": <int|null>, "justification": "<one sentence or N/A>"}}
}}

Do not include any text outside the JSON object.
"""

_DIMENSION_BLOCK_TEMPLATE = """\
### {label}
{description}
- Score 1: {poor}
- Score 5: {excellent}
Note: {prompt_instruction}
"""


def build_rubric_prompt_block() -> str:
    """Return the rubric section of the judge prompt."""
    blocks = "\n".join(_DIMENSION_BLOCK_TEMPLATE.format(**dim) for dim in DIMENSIONS)
    return _RUBRIC_BLOCK_TEMPLATE.format(dimension_blocks=blocks)
