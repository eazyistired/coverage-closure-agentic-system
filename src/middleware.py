"""Middleware — orchestrates parallel WP analysis sessions.

Entry point for the analysis pipeline:
  1. Loads config.yaml to discover work packages.
  2. For each requested WP, pre-fetches uncovered covergroup names via REST,
     splits them into batches of BATCH_SIZE, and runs one CoverageGapAgent
     per batch (sequentially within a WP).
  3. Runs all WP sessions concurrently via asyncio.gather().
  4. Persists each result as a timestamped JSON gap report.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient

from src.agents.coverage_gap_agent import CoverageGapAgent, build_kg_tools

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_OUTPUT_DIR = _ROOT / "outputs" / "coverage-gap-reports"
_KG_DIR = _ROOT / "outputs" / "knowledge-graphs"
_CG_BATCH_SIZE = 5


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _load_config() -> dict:
    with open(_ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_all_wps() -> list[str]:
    """Return all WP names defined in config.yaml."""
    return [wp["name"] for wp in _load_config().get("workpackages", [])]


# ---------------------------------------------------------------------------
# Per-WP analysis
# ---------------------------------------------------------------------------


async def _fetch_covergroup_names(
    report_path: str, wp_name: str, api_base_url: str
) -> list[str]:
    """Call the MCP server REST endpoint directly to get uncovered covergroup names."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{api_base_url}/uncovered-covergroups",
            json={"report_path": report_path, "wp": wp_name},
        )
        resp.raise_for_status()
        return [cg["covergroup_name"] for cg in resp.json().get("covergroups", [])]


async def _analyze_batch(
    batch: list[str],
    wp_name: str,
    report_path: str,
    tools: list,
    use_kg: bool = True,
) -> str:
    """Run one CoverageGapAgent for a specific batch of covergroups."""
    cg_list = "\n".join(f"- {cg}" for cg in batch)
    if use_kg:
        kg_instruction = (
            "1. Call query_kg_covergroups with the WP name and the covergroup names to get "
            "the full SV source, sample task, and variable context.\n"
        )
    else:
        kg_instruction = (
            "1. Knowledge graph tools are NOT available for this run. Reason about the "
            "covergroup and bins using only the coverage report data and your general "
            "verification knowledge.\n"
        )
    prompt = (
        f"Analyze coverage gaps for work package: {wp_name}\n"
        f"Coverage report path: {report_path}\n\n"
        f"Analyze ONLY these {len(batch)} covergroup(s):\n{cg_list}\n\n"
        "For each covergroup:\n"
        + kg_instruction
        + "2. Use the get_uncovered_bins MCP tool to fetch all uncovered bins.\n"
        "3. Group the uncovered bins by parent_name (coverpoint or cross).\n"
        "4. For coverpoints: produce ONE gap_group entry per coverpoint listing all its "
        "uncovered bins with a scenario_description of what design behaviour is missing.\n"
        "5. For crosses: group bins by shared root cause. Emit one entry per cause cluster; "
        "prefer consolidation — only split when the uncovered scenarios are genuinely independent.\n"
        "6. Focus on describing WHAT is not covered (the design scenario/configuration), "
        "not on how to fix it. Include a brief likely_root_cause only if it adds useful context.\n\n"
        "Return a JSON array — one object per covergroup — matching your output format."
    )
    extra_tools = build_kg_tools() if use_kg else []
    agent = CoverageGapAgent(tools=tools + extra_tools)
    return await agent.chat(prompt)


def _merge_batch_responses(wp_name: str, responses: list[str]) -> dict:
    """Merge per-batch agent responses into a single report dict."""
    all_covergroups: list[dict] = []
    for raw in responses:
        parsed = _extract_json(raw, wp_name)
        if isinstance(parsed, list):
            all_covergroups.extend(parsed)
        elif isinstance(parsed, dict):
            # Single-CG response or wrapper — unwrap if needed
            if "covergroup_name" in parsed:
                all_covergroups.append(parsed)
            elif "covergroups" in parsed:
                all_covergroups.extend(parsed["covergroups"])
            else:
                all_covergroups.append(parsed)
    return {"wp": wp_name, "covergroups": all_covergroups}


async def _analyze_wp(
    wp_name: str,
    report_path: str,
    mcp_server_url: str,
    cg_indices: list[int] | None = None,
    use_kg: bool = True,
) -> dict[str, Any]:
    """Pre-fetch covergroups, optionally filter by index, batch, and analyze."""
    logger.info("[%s] Starting analysis", wp_name)

    # Guard: knowledge graph must exist before analysis (skip guard when KG is disabled)
    kg_path = _KG_DIR / f"{wp_name}_knowledge_graph.json"
    if use_kg:
        if not kg_path.exists():
            raise FileNotFoundError(
                f"[{wp_name}] Knowledge graph not found at {kg_path}. "
                f"Run: python coverage_gap_analyzer.py --generate-kg --wps {wp_name}"
            )
        logger.info("[%s] Knowledge graph found: %s", wp_name, kg_path.name)
    else:
        logger.info("[%s] KG context disabled for this run", wp_name)

    # Derive REST base URL (strip /mcp suffix)
    api_base = mcp_server_url.rsplit("/mcp", 1)[0]

    # Step 1: fetch all uncovered covergroup names via REST
    covergroup_names = await _fetch_covergroup_names(report_path, wp_name, api_base)
    logger.info("[%s] %d uncovered covergroup(s) found", wp_name, len(covergroup_names))

    # Step 1b: filter by index if requested
    if cg_indices is not None:
        max_idx = len(covergroup_names) - 1
        invalid = [i for i in cg_indices if i < 0 or i > max_idx]
        if invalid:
            logger.warning(
                "[%s] Ignoring out-of-range covergroup index(es): %s (valid range 0–%d)",
                wp_name,
                invalid,
                max_idx,
            )
        covergroup_names = [
            covergroup_names[i] for i in cg_indices if 0 <= i <= max_idx
        ]
        logger.info(
            "[%s] Filtered to %d covergroup(s) by index: %s",
            wp_name,
            len(covergroup_names),
            covergroup_names,
        )

    if not covergroup_names:
        logger.info("[%s] Nothing to analyze", wp_name)
        return {
            "wp": wp_name,
            "raw_response": json.dumps({"wp": wp_name, "covergroups": []}),
        }

    # Step 2: get MCP tools once — shared across all batch agents
    client = MultiServerMCPClient(
        {"coverage-gap-analyzer": {"transport": "sse", "url": mcp_server_url}}
    )
    tools = await client.get_tools()
    logger.debug("[%s] MCP tools: %s", wp_name, [t.name for t in tools])

    # Step 3: split into batches and analyze sequentially
    batches = [
        covergroup_names[i : i + _CG_BATCH_SIZE]
        for i in range(0, len(covergroup_names), _CG_BATCH_SIZE)
    ]
    logger.info(
        "[%s] %d batch(es) of up to %d covergroup(s)",
        wp_name,
        len(batches),
        _CG_BATCH_SIZE,
    )

    batch_responses: list[str] = []
    for idx, batch in enumerate(batches, 1):
        logger.info("[%s] Batch %d/%d: %s", wp_name, idx, len(batches), batch)
        response = await _analyze_batch(
            batch, wp_name, report_path, tools, use_kg=use_kg
        )
        batch_responses.append(response)

    # Step 4: merge
    merged = _merge_batch_responses(wp_name, batch_responses)
    logger.info(
        "[%s] Analysis complete — %d covergroup(s) in report",
        wp_name,
        len(merged["covergroups"]),
    )
    return {"wp": wp_name, "raw_response": json.dumps(merged), "use_kg": use_kg}


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _extract_json(raw: str, wp_name: str) -> dict | list:
    """Try to parse a JSON object or array from the agent's response text."""
    try:
        # Try array first
        start = raw.find("[")
        obj_start = raw.find("{")
        if start >= 0 and (obj_start < 0 or start < obj_start):
            end = raw.rfind("]") + 1
            if end > start:
                return json.loads(raw[start:end])
        # Fall back to object
        if obj_start >= 0:
            end = raw.rfind("}") + 1
            if end > obj_start:
                return json.loads(raw[obj_start:end])
    except (json.JSONDecodeError, ValueError):
        pass
    return {"wp": wp_name, "raw_response": raw}


def _save_report(result: dict[str, Any], timestamp: str) -> Path:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    wp = result["wp"]
    use_kg = result.get("use_kg", True)
    kg_label = "kg_on" if use_kg else "kg_off"
    out_path = _OUTPUT_DIR / f"{wp}_{timestamp}_{kg_label}_gap_report.json"
    data = _extract_json(result.get("raw_response", ""), wp)
    if isinstance(data, dict):
        # Inject metadata fields required by the evaluation pipeline.
        # "wp" ensures the evaluator can derive the WP without parsing the filename.
        # "coverage_analyser_llm" records which model produced this report so the
        # evaluator can implement anti-self-favouritism logic automatically.
        # "kg_context" records whether KG tools were available during this run.
        data["wp"] = wp
        data["coverage_analyser_llm"] = os.getenv("LLM_MODEL", "unknown")
        data["generated_at"] = timestamp
        data["kg_context"] = use_kg
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    logger.info("[%s] Report saved: %s", wp, out_path)
    return out_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_analysis(
    wps: list[str],
    report_path: str,
    cg_indices: list[int] | None = None,
    use_kg: bool = True,
) -> list[Path]:
    """Run coverage gap analysis for the given WPs in parallel.

    Parameters
    ----------
    wps:
        Work package names to analyse (e.g. ["CAN", "SPI"]).
    report_path:
        Path to the vManager .report file (absolute or relative to cwd).
    cg_indices:
        Optional 0-based indices of covergroups to analyse (from the uncovered
        list returned by the MCP server).  ``None`` means analyse all.
        When multiple WPs are specified, the same indices are applied to each.
    use_kg:
        When ``False``, KG tools are withheld from the agent and the KG
        existence guard is skipped.  Used by Scenario 2 (KG ablation).

    Returns
    -------
    list[Path]
        Paths to the saved gap report JSON files (one per successful WP).
    """
    load_dotenv()
    mcp_server_url = os.getenv("MCP_SERVER_URL", "http://localhost:8080/mcp")
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    logger.info(
        "Running analysis for %d WP(s) against %s (kg_context=%s)",
        len(wps),
        mcp_server_url,
        use_kg,
    )
    if cg_indices is not None:
        logger.info("Covergroup index filter: %s", cg_indices)

    tasks = [
        _analyze_wp(wp, report_path, mcp_server_url, cg_indices, use_kg=use_kg)
        for wp in wps
    ]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    saved: list[Path] = []
    for wp, result in zip(wps, raw_results):
        if isinstance(result, Exception):
            logger.error("[%s] Analysis failed: %s", wp, result)
        else:
            saved.append(_save_report(result, timestamp))

    return saved


def generate_knowledge_graphs(
    wps: list[str],
    skip_enrichment: bool = False,
) -> list[Path]:
    """Build (and optionally AI-enrich) the knowledge graph for each WP.

    Outputs one JSON file per WP to outputs/knowledge-graphs/.
    Returns paths to saved files.
    """
    import json as _json

    from src.knowledge_graph.kg_builder import build_knowledge_graph
    from src.knowledge_graph.kg_enricher import enrich_graph

    _KG_DIR.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    for wp in wps:
        try:
            logger.info("[%s] Building knowledge graph...", wp)
            graph = build_knowledge_graph(wp)

            if not skip_enrichment:
                logger.info("[%s] Enriching knowledge graph with AI summaries...", wp)
                graph = enrich_graph(graph)
            else:
                logger.info("[%s] Skipping AI enrichment (--skip-enrichment).", wp)

            out_path = _KG_DIR / f"{wp}_knowledge_graph.json"
            with open(out_path, "w", encoding="utf-8") as f:
                _json.dump(graph, f, indent=2)
            logger.info("[%s] Knowledge graph saved: %s", wp, out_path)
            saved.append(out_path)

        except Exception as exc:
            logger.error("[%s] Knowledge graph generation failed: %s", wp, exc)

    return saved
