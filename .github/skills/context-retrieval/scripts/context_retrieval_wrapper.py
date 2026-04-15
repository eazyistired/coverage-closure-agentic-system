#!/usr/bin/env python3
"""CLI wrapper around ContextRetrievalAnalyzer for agent workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from context_retrieval_analyzer import ContextRetrievalAnalyzer


def _emit(payload: Dict[str, Any], pretty: bool = True) -> None:
    if pretty:
        print(json.dumps(payload, indent=2, ensure_ascii=True))
    else:
        print(json.dumps(payload, separators=(",", ":"), ensure_ascii=True))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Context retrieval for a specific covergroup from coverage and golden model files."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser(
        "list-covergroups", help="List covergroup names from a coverage file."
    )
    list_cmd.add_argument(
        "--coverage-file", type=Path, required=True, help="Path to coverage .svh file."
    )
    list_cmd.add_argument(
        "--compact-json",
        action="store_true",
        help="Print compact one-line JSON output.",
    )

    ctx_cmd = sub.add_parser(
        "get-covergroup-context", help="Extract context for a specific covergroup."
    )
    ctx_cmd.add_argument(
        "--coverage-file", type=Path, required=True, help="Path to coverage .svh file."
    )
    ctx_cmd.add_argument(
        "--golden-model-file",
        type=Path,
        required=True,
        help="Path to golden model .svh file.",
    )
    ctx_cmd.add_argument(
        "--covergroup-name", required=True, help="Exact covergroup name."
    )
    ctx_cmd.add_argument(
        "--wp", required=False, help="Optional work package marker (for metadata)."
    )
    ctx_cmd.add_argument(
        "--compact-json",
        action="store_true",
        help="Print compact one-line JSON output.",
    )

    return parser


def _validate_file(path: Path, label: str) -> Path:
    if not path.exists():
        raise ValueError(f"{label} does not exist: {path}")
    return path


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        compact = bool(getattr(args, "compact_json", False))

        if args.command == "list-covergroups":
            coverage_file = _validate_file(args.coverage_file, "Coverage file")
            analyzer = ContextRetrievalAnalyzer(coverage_file=coverage_file)
            entries = analyzer.list_covergroups()
            _emit(
                {
                    "command": "list-covergroups",
                    "coverage_file": str(coverage_file),
                    "count": len(entries),
                    "entries": entries,
                },
                pretty=not compact,
            )
            return 0

        if args.command == "get-covergroup-context":
            coverage_file = _validate_file(args.coverage_file, "Coverage file")
            golden_model_file = _validate_file(
                args.golden_model_file, "Golden model file"
            )
            analyzer = ContextRetrievalAnalyzer(
                coverage_file=coverage_file,
                golden_model_file=golden_model_file,
            )
            payload = analyzer.get_covergroup_context(
                covergroup_name=args.covergroup_name,
                wp=getattr(args, "wp", None),
            )
            payload["command"] = "get-covergroup-context"
            _emit(payload, pretty=not compact)
            return 0

        raise ValueError(f"Unsupported command: {args.command}")

    except (ValueError, OSError, RuntimeError) as exc:
        _emit(
            {
                "ok": False,
                "command": getattr(args, "command", None),
                "error": str(exc),
            },
            pretty=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
