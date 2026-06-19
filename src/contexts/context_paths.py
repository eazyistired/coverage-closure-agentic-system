"""Centralised map of context file keys to workspace-relative paths.

Import this module and use ``CONTEXT_PATHS[key]`` to get a ``Path`` object.
Never hardcode context file paths outside this module.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]  # workspace root


def _p(rel: str) -> Path:
    return _ROOT / rel


CONTEXT_PATHS: dict[str, Path] = {
    # ------------------------------------------------------------------ agents
    "coverage_gap_agent": _p("src/contexts/agents/coverage_gap_agent_context.md"),
    "kg_enricher": _p("src/contexts/agents/kg_enricher_context.md"),
    # ------------------------------------------------------------------ common
    "uvm_knowledge": _p("src/contexts/common/uvm_knowledge.md"),
    "tb_phases": _p("src/contexts/common/tb_phases.md"),
    "coverage_guideline": _p("src/contexts/common/coverage_coding_guideline.md"),
}


def get_context(key: str) -> str:
    """Return the text content of the context file identified by *key*."""
    path = CONTEXT_PATHS[key]
    if not path.exists():
        raise FileNotFoundError(f"Context file not found: {path}")
    return path.read_text(encoding="utf-8")
