"""LangChain tools that give the CoverageGapAgent access to Knowledge Graphs.

Two tools are provided:

query_kg_covergroups
    Direct lookup — returns the full KG node (including raw SV source and
    sample task source) for each requested covergroup, plus all variable nodes
    for their sampled variables.  Cross-model variable references are
    automatically resolved from the referenced WP's KG.

search_kg_variables
    RAG-based semantic search over all variable, task, and event nodes in the
    WP knowledge graph.  Use this for exploratory queries such as
    "what controls UV shutdown?" or "which task triggers the OC flag?".
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
_KG_DIR = _ROOT / "outputs" / "knowledge-graphs"

# Cache: (wp_name) → kg dict so we don't re-load on every tool call
_KG_CACHE: dict[str, dict[str, Any]] = {}

# Cache: (wp_name) → InMemoryVectorStore
_RAG_CACHE: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_kg(wp_name: str) -> dict[str, Any]:
    key = wp_name.upper()
    if key not in _KG_CACHE:
        path = _KG_DIR / f"{key}_knowledge_graph.json"
        if not path.exists():
            raise FileNotFoundError(
                f"Knowledge graph not found for WP {key}: {path}. "
                f"Run: python coverage_gap_analyzer.py --generate-kg --wps {key}"
            )
        with open(path, encoding="utf-8") as f:
            _KG_CACHE[key] = json.load(f)
        logger.debug(
            "KG loaded for WP %s (%d nodes)",
            key,
            sum(len(v) for v in _KG_CACHE[key].get("nodes", {}).values()),
        )
    return _KG_CACHE[key]


def _get_covergroup_node(kg: dict[str, Any], cg_name: str) -> dict[str, Any] | None:
    for cg in kg.get("nodes", {}).get("covergroups", []):
        if cg["name"] == cg_name:
            return cg
    return None


def _get_variable_node(kg: dict[str, Any], var_name: str) -> dict[str, Any] | None:
    for v in kg.get("nodes", {}).get("variables", []):
        if v["name"] == var_name:
            return v
    return None


def _get_rag_store(wp_name: str):
    """Build (or return cached) RAG store for the given WP."""
    key = wp_name.upper()
    if key not in _RAG_CACHE:
        from src.rag.kg_rag import build_kg_retriever

        kg = _load_kg(key)
        _RAG_CACHE[key] = build_kg_retriever(kg)
        logger.debug("RAG store built for WP %s", key)
    return _RAG_CACHE[key]


# ---------------------------------------------------------------------------
# Tool 1: Direct lookup
# ---------------------------------------------------------------------------


@tool
def query_kg_covergroups(wp_name: str, covergroup_names: list[str]) -> str:
    """Look up knowledge graph nodes for specific covergroups in a work package.

    Returns the full covergroup specification (Jama description, covergroup SV
    source, sample task SV source, coverpoints, crosses) and the variable nodes
    for every sampled variable.  Cross-model variables (e.g. fsm_current_state
    from the FSM golden model) are already resolved and included.

    Args:
        wp_name: Work package identifier (e.g. "HSS", "CAN", "FSM").
        covergroup_names: List of exact covergroup names to look up
            (e.g. ["dscov_HSS_04_undervoltage", "dscov_HSS_02_timer_valid_config"]).

    Returns:
        JSON string with keys:
          - "covergroups": list of full CG nodes including covergroup_sv_source
            and sample_task_sv_source
          - "variables": dict mapping variable name → variable node (includes
            cross-model variables already resolved from other WP golden models)
    """
    try:
        kg = _load_kg(wp_name)
    except FileNotFoundError as e:
        return json.dumps({"error": str(e)})

    cg_nodes: list[dict[str, Any]] = []
    all_sampled_vars: set[str] = set()

    for cg_name in covergroup_names:
        node = _get_covergroup_node(kg, cg_name)
        if node:
            cg_nodes.append(node)
            all_sampled_vars.update(node.get("sampled_variables", []))
        else:
            logger.warning("Covergroup '%s' not found in WP %s KG", cg_name, wp_name)

    # Collect variable nodes (cross-model vars already resolved inline in the KG)
    var_nodes: dict[str, dict[str, Any]] = {}
    for vname in all_sampled_vars:
        node = _get_variable_node(kg, vname)
        if node:
            var_nodes[vname] = node

    result = {
        "covergroups": cg_nodes,
        "variables": var_nodes,
    }
    return json.dumps(result, indent=2, default=str)


# ---------------------------------------------------------------------------
# Tool 2: RAG semantic search
# ---------------------------------------------------------------------------


@tool
def search_kg_variables(wp_name: str, query: str, k: int = 6) -> str:
    """Semantic search over all variable, task, and event nodes in a WP knowledge graph.

    Use this for exploratory questions such as:
    - "what controls UV shutdown behaviour?"
    - "which task updates the overcurrent flag?"
    - "what event triggers when VSSBC UV is detected?"

    Args:
        wp_name: Work package identifier (e.g. "HSS", "CAN", "FSM").
        query:   Natural-language description of what you are looking for.
        k:       Number of top results to return (default 6).

    Returns:
        JSON string with a list of matching nodes, each with:
          - "id": node identifier
          - "kind": "variable" | "task" | "event" | "covergroup"
          - "name": node name
          - "content": text summary used for embedding
    """
    try:
        store = _get_rag_store(wp_name)
    except FileNotFoundError as e:
        return json.dumps({"error": str(e)})

    from src.rag.kg_rag import search_kg

    results = search_kg(store, query, k=k)
    return json.dumps(results, indent=2, default=str)
