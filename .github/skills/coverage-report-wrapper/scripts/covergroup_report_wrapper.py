#!/usr/bin/env python3
"""CLI wrapper around CovergroupReportAnalyzer for agent-driven method calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from covergroup_report_analyzer import CovergroupReportAnalyzer


def _emit(payload: Dict[str, Any], pretty: bool = True) -> None:
    if pretty:
        print(json.dumps(payload, indent=2, ensure_ascii=True))
    else:
        print(json.dumps(payload, separators=(",", ":"), ensure_ascii=True))


def _default_report() -> Path | None:
    script_path = Path(__file__).resolve()
    # Search upward to find the first repository-like root containing reports/.
    for candidate_root in script_path.parents:
        reports_dir = candidate_root / "reports"
        if reports_dir.is_dir():
            return CovergroupReportAnalyzer.newest_report(reports_dir)
    return None


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--report",
        type=Path,
        default=_default_report(),
        help="Path to .report file. Defaults to newest report under reports/.",
    )
    parser.add_argument(
        "--compact-json",
        action="store_true",
        help="Print compact one-line JSON output.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Wrapper over CovergroupReportAnalyzer exposing parse/export/query "
            "methods for AI skill integrations."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    parse_cmd = sub.add_parser("parse", help="Parse report and return WP summary.")
    _add_common_args(parse_cmd)

    wps_cmd = sub.add_parser("get-available-wps", help="List all WPs available in report.")
    _add_common_args(wps_cmd)

    export_cmd = sub.add_parser("export-wp-json", help="Parse and export selected WP to JSON.")
    _add_common_args(export_cmd)
    export_cmd.add_argument("--wp", required=False, help="WP name (for example HSS).")
    export_cmd.add_argument("--output", type=Path, default=None, help="Output JSON path.")

    all_cg_cmd = sub.add_parser("get-all-covergroups", help="Get all covergroups (optionally filtered by WP).")
    _add_common_args(all_cg_cmd)
    all_cg_cmd.add_argument("--wp", required=False, help="Optional WP filter.")

    covered_cmd = sub.add_parser("get-covered-covergroups", help="Get fully covered covergroups.")
    _add_common_args(covered_cmd)
    covered_cmd.add_argument("--wp", required=False, help="Optional WP filter.")

    uncovered_cmd = sub.add_parser("get-uncovered-covergroups", help="Get not fully covered covergroups.")
    _add_common_args(uncovered_cmd)
    uncovered_cmd.add_argument("--wp", required=False, help="Optional WP filter.")

    unc_items_cmd = sub.add_parser(
        "get-uncovered-coverpoints-crosses",
        help="Get uncovered coverpoints/crosses for one covergroup.",
    )
    _add_common_args(unc_items_cmd)
    unc_items_cmd.add_argument("--covergroup-name", required=True, help="Exact covergroup name.")
    unc_items_cmd.add_argument("--wp", required=False, help="Optional WP filter.")

    unc_bins_cmd = sub.add_parser(
        "get-uncovered-bins",
        help="Get uncovered bins inside uncovered coverpoints/crosses for one covergroup.",
    )
    _add_common_args(unc_bins_cmd)
    unc_bins_cmd.add_argument("--covergroup-name", required=True, help="Exact covergroup name.")
    unc_bins_cmd.add_argument("--wp", required=False, help="Optional WP filter.")

    bin_detail_cmd = sub.add_parser(
        "get-uncovered-bin-details",
        help="Get full details for one specific uncovered bin.",
    )
    _add_common_args(bin_detail_cmd)
    bin_detail_cmd.add_argument("--covergroup-name", required=True, help="Exact covergroup name.")
    bin_detail_cmd.add_argument("--parent-name", required=True, help="Coverpoint/cross name containing the bin.")
    bin_detail_cmd.add_argument("--bin-name", required=True, help="Exact bin name.")
    bin_detail_cmd.add_argument("--wp", required=False, help="Optional WP filter.")

    return parser


def _choose_wp_interactive(analyzer: CovergroupReportAnalyzer) -> str:
    wp_names = analyzer.get_available_wps()
    if not wp_names:
        raise ValueError("No WPs found in report")

    print("Available WPs:")
    for idx, wp_name in enumerate(wp_names, start=1):
        print(f"  {idx}. {wp_name}")

    while True:
        try:
            raw = input("Select WP by index or name: ").strip()
        except EOFError:
            return wp_names[0]

        if not raw:
            print("Please enter a value.")
            continue

        if raw.isdigit():
            num = int(raw)
            if 1 <= num <= len(wp_names):
                return wp_names[num - 1]
            print("Index out of range.")
            continue

        upper = raw.upper()
        if upper in wp_names:
            return upper
        print("Invalid WP selection.")


def _validate_report(report: Path | None) -> Path:
    if report is None:
        raise ValueError("No report found. Pass --report explicitly.")
    if not report.exists():
        raise ValueError(f"Report does not exist: {report}")
    return report


def _response_list(command: str, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "command": command,
        "count": len(entries),
        "entries": entries,
    }


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        report_path = _validate_report(args.report)
        analyzer = CovergroupReportAnalyzer(report_path)
        compact = bool(args.compact_json)

        if args.command == "parse":
            parsed = analyzer.parse_report()
            payload = {
                "command": "parse",
                "report": str(report_path.resolve()),
                "wp_count": len(parsed),
                "wps": {name: len(entries) for name, entries in sorted(parsed.items())},
            }
            _emit(payload, pretty=not compact)
            return 0

        if args.command == "get-available-wps":
            wps = analyzer.get_available_wps()
            _emit({"command": "get-available-wps", "count": len(wps), "wps": wps}, pretty=not compact)
            return 0

        if args.command == "export-wp-json":
            wp = args.wp.upper().strip() if args.wp else _choose_wp_interactive(analyzer)
            payload = analyzer.export_wp_json(wp=wp, output_path=args.output)
            out_payload = {
                "command": "export-wp-json",
                "selected_wp": payload.get("meta", {}).get("selected_wp", wp),
                "covergroup_count": payload.get("meta", {}).get("covergroup_count", 0),
                "meta": payload.get("meta", {}),
            }
            _emit(out_payload, pretty=not compact)
            return 0

        if args.command == "get-all-covergroups":
            entries = analyzer.get_all_covergroups(wp=getattr(args, "wp", None))
            _emit(_response_list("get-all-covergroups", entries), pretty=not compact)
            return 0

        if args.command == "get-covered-covergroups":
            entries = analyzer.get_covered_covergroups(wp=getattr(args, "wp", None))
            _emit(_response_list("get-covered-covergroups", entries), pretty=not compact)
            return 0

        if args.command == "get-uncovered-covergroups":
            entries = analyzer.get_uncovered_covergroups(wp=getattr(args, "wp", None))
            _emit(_response_list("get-uncovered-covergroups", entries), pretty=not compact)
            return 0

        if args.command == "get-uncovered-coverpoints-crosses":
            entries = analyzer.get_uncovered_coverpoints_crosses_inside_covergroup(
                covergroup_name=args.covergroup_name,
                wp=getattr(args, "wp", None),
            )
            payload = _response_list("get-uncovered-coverpoints-crosses", entries)
            payload["covergroup_name"] = args.covergroup_name
            _emit(payload, pretty=not compact)
            return 0

        if args.command == "get-uncovered-bins":
            entries = analyzer.get_uncovered_bins_inside_coverpoints_crosses(
                covergroup_name=args.covergroup_name,
                wp=getattr(args, "wp", None),
            )
            payload = _response_list("get-uncovered-bins", entries)
            payload["covergroup_name"] = args.covergroup_name
            _emit(payload, pretty=not compact)
            return 0

        if args.command == "get-uncovered-bin-details":
            details = analyzer.get_uncovered_bin_details(
                covergroup_name=args.covergroup_name,
                parent_name=args.parent_name,
                bin_name=args.bin_name,
                wp=getattr(args, "wp", None),
            )
            payload = {
                "command": "get-uncovered-bin-details",
                "covergroup_name": args.covergroup_name,
                "parent_name": args.parent_name,
                "bin_name": args.bin_name,
                "found": details is not None,
                "details": details,
            }
            _emit(payload, pretty=not compact)
            return 0

        raise ValueError(f"Unsupported command: {args.command}")

    except Exception as exc:  # noqa: BLE001 - wrapper should never crash silently.
        _emit(
            {
                "ok": False,
                "error": str(exc),
                "command": getattr(args, "command", None),
            },
            pretty=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
