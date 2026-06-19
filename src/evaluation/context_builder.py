"""Context builder — assembles the judge prompt input for a single gap group.

For each gap_group in the gap report, this module:
1. Extracts the relevant KG nodes (variables referenced in ``sampled_on``).
2. Extracts the relevant lines from the coverage report (by covergroup name).
3. Returns a ``GapGroupContext`` ready to be serialised into a judge prompt.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.evaluation.models import GapGroupContext

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# KG excerpt extraction
# ---------------------------------------------------------------------------


def _extract_kg_excerpt(
    kg: Dict[str, Any],
    sampled_on: List[str],
    covergroup_name: str,
) -> str:
    """Return a compact text representation of KG nodes relevant to this gap group.

    Strategy:
    - Include the covergroup entry matching ``covergroup_name`` if present.
    - Include variable entries whose ``name`` appears in ``sampled_on``.
    """
    lines: List[str] = []

    # 1. Covergroup node
    for cg in kg.get("covergroups", []):
        if (
            cg.get("name") == covergroup_name
            or cg.get("covergroup_name") == covergroup_name
        ):
            lines.append(f"[Covergroup] {covergroup_name}")
            for cp in cg.get("coverpoints", []):
                lines.append(f"  Coverpoint: {cp.get('name', '?')}")
                for b in cp.get("bins", []):
                    lines.append(
                        f"    bin {b.get('name', '?')}: {b.get('condition', '')}"
                    )
            for cr in cg.get("crosses", []):
                lines.append(
                    f"  Cross: {cr.get('name', '?')} over {cr.get('coverpoints', [])}"
                )
            break

    # 2. Variable nodes referenced in sampled_on
    for var in kg.get("variables", []):
        if var.get("name") in sampled_on:
            vtype = var.get("type", "?")
            comment = var.get("comment", "")
            lines.append(
                f"[Variable] {var['name']} : {vtype}"
                + (f" — {comment}" if comment else "")
            )
            if "enum_values" in var:
                lines.append(f"  enum values: {', '.join(var['enum_values'])}")

    # 3. Enum type definitions that appear in sampled_on signal types
    for enum_def in kg.get("enums", []):
        if any(enum_def.get("name", "") in v for v in sampled_on):
            lines.append(
                f"[Enum] {enum_def['name']}: {', '.join(enum_def.get('values', []))}"
            )

    return "\n".join(lines) if lines else "(No KG entries found for this gap group)"


# ---------------------------------------------------------------------------
# Coverage report excerpt extraction
# ---------------------------------------------------------------------------


def _extract_coverage_excerpt(report_path: Path, covergroup_name: str) -> str:
    """Extract lines from the raw .report file that belong to ``covergroup_name``."""
    if not report_path.exists():
        return "(Coverage report not available)"
    try:
        text = report_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Cannot read coverage report: %s", exc)
        return "(Coverage report not available)"

    # Find the block for this covergroup (heuristic line-based scan)
    lines = text.splitlines()
    in_block = False
    block_lines: List[str] = []
    for line in lines:
        if covergroup_name in line:
            in_block = True
        if in_block:
            block_lines.append(line)
            # Stop after 40 lines or when a new covergroup block starts
            if len(block_lines) > 1 and re.match(
                r"^\s*(covergroup|CG|GROUP)", line, re.IGNORECASE
            ):
                block_lines.pop()
                break
            if len(block_lines) >= 40:
                break
    return "\n".join(block_lines) if block_lines else "(Covergroup not found in report)"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_gap_group_context(
    gap_group: Dict[str, Any],
    covergroup_name: str,
    kg: Dict[str, Any],
    report_path: Optional[Path],
) -> GapGroupContext:
    """Build a ``GapGroupContext`` for a single gap_group dict.

    Parameters
    ----------
    gap_group:
        One element from ``gap_report["covergroups"][i]["gap_groups"]``.
    covergroup_name:
        The parent covergroup name (one level up from gap_group).
    kg:
        Parsed knowledge graph dict for the WP.
    report_path:
        Path to the vManager .report file (used for coverage excerpt).
    """
    sampled_on: List[str] = gap_group.get("sampled_on", [])

    kg_excerpt = _extract_kg_excerpt(kg, sampled_on, covergroup_name)
    coverage_excerpt = (
        _extract_coverage_excerpt(report_path, covergroup_name)
        if report_path
        else "(No report path provided)"
    )

    return GapGroupContext(
        covergroup_name=covergroup_name,
        parent_name=gap_group.get("parent_name", ""),
        parent_type=gap_group.get("parent_type", "coverpoint"),
        uncovered_bins=gap_group.get("uncovered_bins", []),
        sampled_on=sampled_on,
        scenario_description=gap_group.get("scenario_description", ""),
        likely_root_cause=gap_group.get("likely_root_cause", ""),
        kg_excerpt=kg_excerpt,
        coverage_excerpt=coverage_excerpt,
    )


def load_gap_report(gap_report_path: Path) -> Dict[str, Any]:
    with open(gap_report_path, encoding="utf-8") as f:
        return json.load(f)


def load_kg(wp: str, kg_dir: Optional[Path] = None) -> Dict[str, Any]:
    base = kg_dir or (_ROOT / "outputs" / "knowledge-graphs")
    kg_path = base / f"{wp}_knowledge_graph.json"
    if not kg_path.exists():
        logger.warning("KG not found at %s — returning empty graph", kg_path)
        return {}
    with open(kg_path, encoding="utf-8") as f:
        return json.load(f)


def iter_gap_group_contexts(
    gap_report: Dict[str, Any],
    kg: Dict[str, Any],
    report_path: Optional[Path],
) -> List[GapGroupContext]:
    """Yield one GapGroupContext per gap_group across all covergroups in the report."""
    contexts: List[GapGroupContext] = []
    for cg_entry in gap_report.get("covergroups", []):
        cg_name = cg_entry.get("covergroup_name", "unknown")
        for gg in cg_entry.get("gap_groups", []):
            ctx = build_gap_group_context(gg, cg_name, kg, report_path)
            contexts.append(ctx)
    return contexts
