"""Knowledge Graph builder.

Assembles the full WP knowledge graph from:
1. GoldenModelAST (gm_parser)
2. List[CovergroupNode] (cov_parser)
3. Cross-model resolution: looks up referenced WP golden models in config.yaml
4. Type resolution: inlines typedef and register-enum definitions into variable
   nodes from the shared common.svh and sfr_enums.svh files (config.yaml)

Output schema
-------------
{
  "wp":              str,
  "generated_at":   ISO timestamp,
  "source_files":   { "golden_model": str, "coverage": str },
  "reference_model":{ "class_name": str },
  "nodes": {
    "variables": [
      {
        ...base fields...,
        "type_definition": { "kind": "enum", "members": [...], "member_comments": {...} },
        "register_enum":   { "type": str, "values": {...}, "comments": {...} },
        "semantic_summary": str
      }
    ],
    "events":       [ EventNode dict + semantic_summary ],
    "tasks":        [ TaskNode dict + semantic_summary ],
    "covergroups":  [ CovergroupNode dict + semantic_summary ],
  },
  "edges": [
    { "from": id, "rel": rel_type, "to": id_or_label }
  ]
}
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src.knowledge_graph.gm_parser import GoldenModelParser, GoldenModelAST, ast_to_dict
from src.knowledge_graph.cov_parser import CoverageParser, covergroups_to_dicts
from src.knowledge_graph.type_resolver import TypeResolver

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _load_sett() -> dict:
    with open(_ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _shared_files(sett: dict) -> dict[str, str]:
    """Return {file_type: file_path} for shared_context_files in config.yaml."""
    return {
        cf["file_type"]: cf["file_path"] for cf in sett.get("shared_context_files", [])
    }


def _files_for_wp(wp_name: str, sett: dict) -> dict[str, str]:
    """Return {file_type: file_path} for the given WP."""
    for wp in sett.get("workpackages", []):
        if wp["name"].upper() == wp_name.upper():
            return {
                cf["file_type"]: cf["file_path"] for cf in wp.get("context_files", [])
            }
    return {}


def _build_type_resolver(sett: dict) -> TypeResolver:
    common = next(
        (
            cf["file_path"]
            for cf in sett.get("shared_context_files", [])
            if cf["file_type"] == "type_definitions"
        ),
        None,
    )
    # Collect ALL register_enums entries (sett.yaml may list more than one)
    sfr_paths = [
        _ROOT / cf["file_path"]
        for cf in sett.get("shared_context_files", [])
        if cf["file_type"] == "register_enums"
    ]
    common_path = (_ROOT / common) if common else None
    resolver = TypeResolver(
        common_file=common_path if common_path and common_path.exists() else None,
        sfr_enums_files=[p for p in sfr_paths if p.exists()] or None,
    )
    logger.info(
        "TypeResolver: %d type defs, %d register enums",
        resolver.type_def_count,
        resolver.register_enum_count,
    )
    return resolver


def _apply_type_resolution(
    variables: list[dict[str, Any]], resolver: TypeResolver
) -> None:
    """Enrich each variable node with type_definition and register_enum in-place."""
    for var in variables:
        sv_type = var.get("type", "").strip()
        if not sv_type:
            continue
        # Try common.svh typedef first
        td = resolver.resolve_type(sv_type)
        if td:
            var["type_definition"] = td
        # Try sfr_enums.svh register enum
        re_def = resolver.resolve_register_enum(sv_type)
        if re_def:
            var["register_enum"] = re_def


def _files_for_wp(wp_name: str, sett: dict) -> dict[str, str]:
    """Return {file_type: file_path} for the given WP."""
    for wp in sett.get("workpackages", []):
        if wp["name"].upper() == wp_name.upper():
            return {
                cf["file_type"]: cf["file_path"] for cf in wp.get("context_files", [])
            }
    return {}


# ---------------------------------------------------------------------------
# Cross-model resolver
# ---------------------------------------------------------------------------


def _resolve_cross_refs(ast: GoldenModelAST, sett: dict) -> dict[str, dict[str, Any]]:
    """
    For each p_scb.XXX_gm.var reference, look up the variable declaration in
    that WP's golden model and return an enriched variable dict.
    """
    resolved: dict[str, dict[str, Any]] = {}

    for cr in ast.cross_refs:
        key = cr.full_path
        if key in resolved:
            continue

        wp_token = cr.wp_token.upper()
        files = _files_for_wp(wp_token, sett)
        gm_path = files.get("golden_model")
        if not gm_path:
            resolved[key] = {
                "id": f"var:{key}",
                "name": cr.var_name,
                "type": "unknown",
                "comment": "",
                "variable_group": f"cross_model:{wp_token}",
                "register_trace": "",
                "assigned_by_tasks": [],
                "declaration_line": 0,
                "resolved_from_wp": wp_token,
                "semantic_summary": "",
            }
            continue

        full_path = _ROOT / gm_path
        if not full_path.exists():
            logger.warning("Cross-model GM file not found: %s", full_path)
            resolved[key] = {
                "id": f"var:{key}",
                "name": cr.var_name,
                "type": "unknown",
                "comment": f"Referenced from WP {wp_token}",
                "variable_group": f"cross_model:{wp_token}",
                "register_trace": "",
                "assigned_by_tasks": [],
                "declaration_line": 0,
                "resolved_from_wp": wp_token,
                "semantic_summary": "",
            }
            continue

        try:
            cross_ast = GoldenModelParser(full_path).parse()
            match = next(
                (v for v in cross_ast.variables if v.name == cr.var_name), None
            )
            if match:
                resolved[key] = {
                    "id": f"var:{key}",
                    "name": cr.var_name,
                    "type": match.sv_type,
                    "comment": match.comment,
                    "variable_group": f"cross_model:{wp_token}",
                    "register_trace": match.register_trace,
                    "assigned_by_tasks": match.assigned_by_tasks,
                    "declaration_line": match.declaration_line,
                    "resolved_from_wp": wp_token,
                    "semantic_summary": "",
                }
            else:
                resolved[key] = {
                    "id": f"var:{key}",
                    "name": cr.var_name,
                    "type": "unknown",
                    "comment": f"Declaration not found in {wp_token} golden model",
                    "variable_group": f"cross_model:{wp_token}",
                    "register_trace": "",
                    "assigned_by_tasks": [],
                    "declaration_line": 0,
                    "resolved_from_wp": wp_token,
                    "semantic_summary": "",
                }
        except Exception as exc:
            logger.warning("Failed to parse cross-model GM %s: %s", full_path, exc)

    return resolved


# ---------------------------------------------------------------------------
# Edge builder
# ---------------------------------------------------------------------------


def _build_edges(
    gm_dict: dict[str, Any],
    cg_dicts: list[dict[str, Any]],
) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []

    # task --updates--> variable
    for task in gm_dict.get("tasks", []):
        for var_id in task.get("updates_variables", []):
            edges.append({"from": task["id"], "rel": "updates", "to": var_id})

    # variable --mirrors_register--> REGISTER.FIELD
    for var in gm_dict.get("variables", []):
        if var.get("register_trace"):
            edges.append(
                {
                    "from": var["id"],
                    "rel": "mirrors_register",
                    "to": var["register_trace"],
                }
            )

    # covergroup --samples--> variable  (via coverpoint expressions)
    # covergroup --sampled_on--> variable  (via trigger event list)
    for cg in cg_dicts:
        cg_id = cg["id"]
        for sv in cg.get("sampled_variables", []):
            edges.append({"from": cg_id, "rel": "samples", "to": f"var:{sv}"})
        for tv in cg.get("trigger_variables", []):
            edges.append({"from": cg_id, "rel": "sampled_on", "to": f"var:{tv}"})

    # Deduplicate while preserving order
    seen: set[tuple] = set()
    unique: list[dict[str, str]] = []
    for e in edges:
        key = (e["from"], e["rel"], e["to"])
        if key not in seen:
            seen.add(key)
            unique.append(e)

    return unique


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


def build_knowledge_graph(wp_name: str) -> dict[str, Any]:
    """Build and return the full knowledge graph dict for a WP."""
    sett = _load_sett()
    files = _files_for_wp(wp_name, sett)

    gm_path = files.get("golden_model")
    cov_path = files.get("coverage")

    if not gm_path:
        raise ValueError(
            f"No golden_model file configured for WP '{wp_name}' in config.yaml"
        )
    if not cov_path:
        raise ValueError(f"No coverage file configured for WP '{wp_name}' in config.yaml")

    gm_full = _ROOT / gm_path
    cov_full = _ROOT / cov_path

    if not gm_full.exists():
        raise FileNotFoundError(f"Golden model file not found: {gm_full}")
    if not cov_full.exists():
        raise FileNotFoundError(f"Coverage file not found: {cov_full}")

    logger.info("[%s] Parsing golden model: %s", wp_name, gm_full.name)
    ast = GoldenModelParser(gm_full).parse()
    gm_dict = ast_to_dict(ast)

    logger.info("[%s] Parsing coverage file: %s", wp_name, cov_full.name)
    covergroups = CoverageParser(cov_full).parse()
    cg_dicts = covergroups_to_dicts(covergroups)
    logger.info("[%s] Found %d covergroup(s)", wp_name, len(cg_dicts))

    # Resolve cross-model references
    cross_vars = _resolve_cross_refs(ast, sett)
    if cross_vars:
        logger.info(
            "[%s] Resolved %d cross-model variable(s)", wp_name, len(cross_vars)
        )
        gm_dict["variables"].extend(cross_vars.values())

    # Inline type resolution from common.svh + sfr_enums.svh
    resolver = _build_type_resolver(sett)
    _apply_type_resolution(gm_dict["variables"], resolver)
    logger.info(
        "[%s] Type resolution applied to %d variable(s)",
        wp_name,
        len(gm_dict["variables"]),
    )

    edges = _build_edges(gm_dict, cg_dicts)

    return {
        "wp": wp_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_files": {
            "golden_model": str(gm_path),
            "coverage": str(cov_path),
        },
        "reference_model": {
            "class_name": gm_dict["class_name"],
        },
        "nodes": {
            "variables": gm_dict["variables"],
            "events": gm_dict["events"],
            "tasks": gm_dict["tasks"],
            "covergroups": cg_dicts,
        },
        "edges": edges,
    }
