"""AI enricher for the Knowledge Graph.

Sends batches of nodes (variables, tasks, events, covergroups) to the LLM
and writes back `semantic_summary` fields in-place on the graph dict.

One LLM call per batch (default 20 nodes). Uses the BaseAgent LLM config so
all SSL / auth settings are inherited automatically.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from src.contexts.context_paths import CONTEXT_PATHS

logger = logging.getLogger(__name__)

_BATCH_SIZE = 20
_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# LLM factory (mirrors BaseAgent setup)
# ---------------------------------------------------------------------------


# Models that reject the temperature parameter (fixed temperature on server side).
_NO_TEMPERATURE_MODELS = frozenset(
    {
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-5.3-chat",
    }
)


def _make_llm() -> ChatOpenAI:
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    model = os.getenv("LLM_MODEL", "gpt-4o")

    ssl_env = os.getenv("VERIFY_SSL", "").lower()
    ca_bundle = os.getenv("SSL_CA_BUNDLE", "")
    if ssl_env == "false":
        ssl_verify: bool | str = False
    elif ca_bundle:
        ssl_verify = str(_ROOT / ca_bundle)
    else:
        ssl_verify = True

    llm_kwargs: dict = {
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "default_headers": {"Authorization": f"Bearer {api_key}"},
        "http_client": httpx.Client(verify=ssl_verify),
        "http_async_client": httpx.AsyncClient(verify=ssl_verify),
    }
    if model not in _NO_TEMPERATURE_MODELS:
        llm_kwargs["temperature"] = 0

    return ChatOpenAI(**llm_kwargs)


# ---------------------------------------------------------------------------
# JSON extraction helper
# ---------------------------------------------------------------------------


def _extract_json_array(raw: str) -> list[dict]:
    """Extract the first JSON array from the LLM response."""
    start = raw.find("[")
    if start < 0:
        return []
    end = raw.rfind("]") + 1
    if end <= start:
        return []
    try:
        return json.loads(raw[start:end])
    except json.JSONDecodeError:
        # Try stripping markdown code fences
        cleaned = re.sub(r"```(?:json)?", "", raw[start:end]).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return []


# ---------------------------------------------------------------------------
# Core enrichment
# ---------------------------------------------------------------------------


def _enrich_batch(
    nodes: list[dict[str, Any]],
    system_prompt: str,
    llm: ChatOpenAI,
    wp_name: str,
) -> None:
    """Send one batch to the LLM and write semantic_summary back in-place."""
    # Build a minimal representation for the prompt
    slim = [
        {
            "id": n["id"],
            "type": n.get("type") or n.get("kind") or "node",
            "name": n["name"],
            "comment": n.get("comment", ""),
            "variable_group": n.get("variable_group", ""),
            "register_trace": n.get("register_trace", ""),
            "updates_variables": n.get("updates_variables", []),
            "sampled_variables": n.get("sampled_variables", []),
            "sampling_trigger_expression": n.get("sampling_trigger_expression", ""),
        }
        for n in nodes
    ]

    user_msg = (
        f"Work Package: {wp_name}\n\n"
        f"Enrich the following {len(slim)} node(s):\n\n"
        f"```json\n{json.dumps(slim, indent=2)}\n```\n\n"
        "Return a JSON array with one object per node: "
        '[ { "id": "...", "semantic_summary": "..." }, ... ]'
    )

    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        response = llm.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_msg)]
        )
        results = _extract_json_array(response.content)

        # Write back summaries by id
        summary_by_id = {r["id"]: r.get("semantic_summary", "") for r in results}
        for node in nodes:
            node_id = node["id"]
            if node_id in summary_by_id:
                node["semantic_summary"] = summary_by_id[node_id]
            elif not node.get("semantic_summary"):
                node["semantic_summary"] = (
                    "Insufficient context — static analysis only."
                )
    except Exception as exc:
        logger.warning("[%s] Enrichment batch failed: %s", wp_name, exc)
        for node in nodes:
            if not node.get("semantic_summary"):
                node["semantic_summary"] = "Enrichment skipped."


def enrich_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """Enrich all nodes in the graph with semantic_summary (in-place).

    Returns the same graph dict (mutated).
    """
    wp_name = graph.get("wp", "unknown")
    system_prompt = CONTEXT_PATHS["kg_enricher"].read_text(encoding="utf-8")
    llm = _make_llm()

    node_sections = ["variables", "events", "tasks", "covergroups"]
    for section in node_sections:
        nodes = graph.get("nodes", {}).get(section, [])
        if not nodes:
            continue
        # Split into batches
        batches = [
            nodes[i : i + _BATCH_SIZE] for i in range(0, len(nodes), _BATCH_SIZE)
        ]
        logger.info(
            "[%s] Enriching %s: %d node(s) in %d batch(es)",
            wp_name,
            section,
            len(nodes),
            len(batches),
        )
        for idx, batch in enumerate(batches, 1):
            logger.info("[%s] %s batch %d/%d", wp_name, section, idx, len(batches))
            _enrich_batch(batch, system_prompt, llm, wp_name)

    return graph
