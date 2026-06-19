#!/usr/bin/env python3
"""Class-based parser and analyzer for vManager covergroup report files."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


TAG_RE = re.compile(
    r"^(?P<domain>ds|d|s)cov_(?P<feature>[A-Za-z0-9]+)_(?P<index>\d+)_(?P<short>[A-Za-z0-9_]+)$"
)
SOURCE_CG_RE = re.compile(r"covergroup\s+([A-Za-z0-9_]+)")
SOURCE_CROSS_RE = re.compile(r"\bcross\b")
SOURCE_COVERPOINT_RE = re.compile(r"\bcoverpoint\b")
SOURCE_COVERPOINT_EXPR_RE = re.compile(r"\bcoverpoint\s+(?P<expr>.+?)(?=\s*(?:\{|;|$))")
SOURCE_CROSS_EXPR_RE = re.compile(r"\bcross\s+(?P<expr>.+?)(?=\s*(?:\{|;|$))")
PERCENT_RE = re.compile(r"\((?P<pct>n/a|[0-9]+(?:\.[0-9]+)?)%\)")


@dataclass(frozen=True)
class CovergroupTag:
    domain: str
    feature: str
    index: int
    short_description: str


class CovergroupReportAnalyzer:
    def __init__(self, report_path: Path | str) -> None:
        self.report_path = Path(report_path)
        self._parsed_by_wp: Optional[Dict[str, List[Dict[str, Any]]]] = None
        self._last_export_payload: Optional[Dict[str, Any]] = None

    @staticmethod
    def newest_report(reports_dir: Path | str) -> Optional[Path]:
        reports_path = Path(reports_dir)
        if not reports_path.is_dir():
            return None
        candidates = list(reports_path.glob("*.report"))
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    @staticmethod
    def _extract_covergroup_name(item: Dict[str, Any]) -> str:
        source = str(item.get("Source", ""))
        match = SOURCE_CG_RE.search(source)
        if match:
            return match.group(1)

        title = str(item.get("title", "")).strip()
        if "::" in title:
            return title.split("::")[-1].strip()
        return title

    @staticmethod
    def _parse_covergroup_tag(covergroup_name: str) -> Optional[CovergroupTag]:
        match = TAG_RE.match(covergroup_name)
        if not match:
            return None
        return CovergroupTag(
            domain=match.group("domain"),
            feature=match.group("feature"),
            index=int(match.group("index")),
            short_description=match.group("short"),
        )

    @staticmethod
    def _classify_item_type(item: Dict[str, Any]) -> str:
        source = str(item.get("Source", "")).lower()
        title = str(item.get("title", ""))

        if SOURCE_CROSS_RE.search(source) or title.endswith("_crs"):
            return "cross"
        if SOURCE_COVERPOINT_RE.search(source) or title.endswith("_cp"):
            return "coverpoint"
        return "unknown"

    @staticmethod
    def _normalize_ws(value: Any) -> str:
        return " ".join(str(value).split())

    @staticmethod
    def _build_bin_entry(bin_item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": bin_item.get("title", ""),
            "all_average_grd": bin_item.get("All Average Grd", ""),
            "all_cov": bin_item.get("All Cov", ""),
            "score": bin_item.get("Score", ""),
            "at_least": bin_item.get("At Least", ""),
            "source": bin_item.get("Source", ""),
        }

    @staticmethod
    def _extract_sampling_expression(item: Dict[str, Any]) -> str:
        source = str(item.get("source", "") or item.get("Source", ""))
        if not source:
            return ""

        coverpoint_match = SOURCE_COVERPOINT_EXPR_RE.search(source)
        if coverpoint_match:
            return coverpoint_match.group("expr").strip()

        cross_match = SOURCE_CROSS_EXPR_RE.search(source)
        if cross_match:
            return cross_match.group("expr").strip()

        return ""

    @staticmethod
    def _split_cross_expression(expression: str) -> List[str]:
        return [term.strip() for term in expression.split(",") if term.strip()]

    @classmethod
    def _resolve_sampled_on_logic(
        cls,
        item_type: str,
        sampling_expression: str,
        coverpoint_logic_by_name: Dict[str, str],
    ) -> List[str]:
        if not sampling_expression:
            return []

        if item_type == "coverpoint":
            return [sampling_expression]

        if item_type == "cross":
            resolved: List[str] = []
            for term in cls._split_cross_expression(sampling_expression):
                resolved_term = coverpoint_logic_by_name.get(term, term)
                if resolved_term not in resolved:
                    resolved.append(resolved_term)
            return resolved

        return [sampling_expression]

    @classmethod
    def _build_subitem_entry(cls, item: Dict[str, Any]) -> Dict[str, Any]:
        bins = [
            cls._build_bin_entry(candidate)
            for candidate in item.get("subitems", [])
            if isinstance(candidate, dict)
        ]
        return {
            "name": item.get("title", ""),
            "type": cls._classify_item_type(item),
            "all_average_grd": item.get("All Average Grd", ""),
            "all_cov": item.get("All Cov", ""),
            "at_least": item.get("At Least", ""),
            "columns": item.get("columns", ""),
            "source": item.get("Source", ""),
            "bins": bins,
        }

    @classmethod
    def _build_covergroup_entry(cls, cg_item: Dict[str, Any], tag: CovergroupTag) -> Dict[str, Any]:
        coverpoints: List[Dict[str, Any]] = []
        crosses: List[Dict[str, Any]] = []
        unknown_items: List[Dict[str, Any]] = []

        for subitem in cg_item.get("subitems", []):
            if not isinstance(subitem, dict):
                continue
            entry = cls._build_subitem_entry(subitem)
            if entry["type"] == "coverpoint":
                coverpoints.append(entry)
            elif entry["type"] == "cross":
                crosses.append(entry)
            else:
                unknown_items.append(entry)

        return {
            "covergroup_name": cls._extract_covergroup_name(cg_item),
            "tag": {
                "domain": tag.domain,
                "feature_name": tag.feature,
                "index": tag.index,
                "short_description": tag.short_description,
            },
            "title": cg_item.get("title", ""),
            "all_average_grd": cg_item.get("All Average Grd", ""),
            "all_cov": cg_item.get("All Cov", ""),
            "enclosing_entity": cg_item.get("Enclosing Entity", ""),
            "source": cg_item.get("Source", ""),
            "coverpoints": coverpoints,
            "crosses": crosses,
            "unknown_items": unknown_items,
        }

    @staticmethod
    def _extract_balanced_object(text: str, start_idx: int) -> Tuple[Optional[str], int]:
        depth = 0
        in_string = False
        escaped = False

        for index in range(start_idx, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start_idx : index + 1], index + 1

        return None, start_idx + 1

    @classmethod
    def _iter_covergroup_blocks(cls, report_text: str) -> Iterable[Dict[str, Any]]:
        anchor = '"cover_group_items"'
        search_index = 0

        while True:
            key_pos = report_text.find(anchor, search_index)
            if key_pos < 0:
                return

            start_idx = report_text.rfind("{", 0, key_pos)
            if start_idx < 0:
                search_index = key_pos + len(anchor)
                continue

            fragment, next_index = cls._extract_balanced_object(report_text, start_idx)
            search_index = max(next_index, key_pos + len(anchor))
            if not fragment:
                continue

            try:
                obj = json.loads(fragment)
            except json.JSONDecodeError:
                continue

            if isinstance(obj, dict) and isinstance(obj.get("cover_group_items"), list):
                yield obj

    @staticmethod
    def _coverage_percentage(entry: Dict[str, Any]) -> Optional[float]:
        all_cov = str(entry.get("all_cov", "") or entry.get("All Cov", ""))
        match = PERCENT_RE.search(all_cov)
        if match:
            pct = match.group("pct")
            if pct != "n/a":
                return float(pct)

        average = str(entry.get("all_average_grd", "") or entry.get("All Average Grd", "")).strip()
        if average.endswith("%"):
            try:
                return float(average[:-1])
            except ValueError:
                return None
        return None

    @classmethod
    def _is_fully_covered(cls, entry: Dict[str, Any]) -> bool:
        percentage = cls._coverage_percentage(entry)
        return percentage is not None and percentage >= 100.0

    @classmethod
    def _is_not_fully_covered(cls, entry: Dict[str, Any]) -> bool:
        percentage = cls._coverage_percentage(entry)
        return percentage is not None and percentage < 100.0

    def parse_report(self, force: bool = False) -> Dict[str, List[Dict[str, Any]]]:
        if self._parsed_by_wp is not None and not force:
            return self._parsed_by_wp

        text = self.report_path.read_text(encoding="utf-8", errors="ignore")
        parsed: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        seen = set()

        for block in self._iter_covergroup_blocks(text):
            for cg_item in block.get("cover_group_items", []):
                if not isinstance(cg_item, dict):
                    continue

                covergroup_name = self._extract_covergroup_name(cg_item)
                tag = self._parse_covergroup_tag(covergroup_name)
                if tag is None:
                    continue

                dedupe_key = (
                    covergroup_name,
                    self._normalize_ws(cg_item.get("title", "")),
                    self._normalize_ws(cg_item.get("Enclosing Entity", "")),
                    self._normalize_ws(cg_item.get("Source", "")),
                )
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                parsed[tag.feature.upper()].append(self._build_covergroup_entry(cg_item, tag))

        self._parsed_by_wp = dict(parsed)
        return self._parsed_by_wp

    def get_available_wps(self) -> List[str]:
        return sorted(self.parse_report())

    def get_wp_covergroups(self, wp: str) -> List[Dict[str, Any]]:
        wp_key = wp.upper().strip()
        return list(self.parse_report().get(wp_key, []))

    def export_wp_json(self, wp: str, output_path: Optional[Path | str] = None) -> Dict[str, Any]:
        wp_key = wp.upper().strip()
        wp_covergroups = self.get_wp_covergroups(wp_key)
        if not wp_covergroups:
            raise ValueError(f"WP '{wp_key}' not found in parsed report")

        if output_path is None:
            output_path = self._default_output_path(wp_key)
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "meta": {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "report_file": str(self.report_path.resolve()),
                "selected_wp": wp_key,
                "covergroup_count": len(wp_covergroups),
            },
            "covergroups": wp_covergroups,
        }

        output_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._last_export_payload = payload
        return payload

    def load_exported_json(self, json_path: Path | str) -> Dict[str, Any]:
        payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
        self._last_export_payload = payload
        return payload

    def get_all_covergroups(self, wp: Optional[str] = None) -> List[Dict[str, Any]]:
        if wp is not None:
            return self.get_wp_covergroups(wp)

        all_covergroups: List[Dict[str, Any]] = []
        for wp_covergroups in self.parse_report().values():
            all_covergroups.extend(wp_covergroups)
        return all_covergroups

    def get_covered_covergroups(self, wp: Optional[str] = None) -> List[Dict[str, Any]]:
        return [entry for entry in self.get_all_covergroups(wp) if self._is_fully_covered(entry)]

    def get_uncovered_covergroups(self, wp: Optional[str] = None) -> List[Dict[str, Any]]:
        # Keep response minimal for agent workflows: names only.
        seen_names = set()
        out: List[Dict[str, Any]] = []
        for entry in self.get_all_covergroups(wp):
            if not self._is_not_fully_covered(entry):
                continue
            name = str(entry.get("covergroup_name", ""))
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            out.append({"covergroup_name": name})
        return out

    def _get_uncovered_covergroups_detailed(self, wp: Optional[str] = None) -> List[Dict[str, Any]]:
        return [entry for entry in self.get_all_covergroups(wp) if self._is_not_fully_covered(entry)]

    def get_covergroup(self, covergroup_name: str, wp: Optional[str] = None) -> Optional[Dict[str, Any]]:
        for covergroup in self.get_all_covergroups(wp):
            if covergroup.get("covergroup_name") == covergroup_name:
                return covergroup
        return None

    def get_uncovered_coverpoints_crosses(self, covergroup_name: str, wp: Optional[str] = None) -> List[Dict[str, Any]]:
        covergroup = self.get_covergroup(covergroup_name, wp)
        if covergroup is None:
            return []

        subitems = list(covergroup.get("coverpoints", [])) + list(covergroup.get("crosses", []))
        return [
            {
                "name": entry.get("name", ""),
                "type": entry.get("type", "unknown"),
            }
            for entry in subitems
            if self._is_not_fully_covered(entry)
        ]

    def _get_uncovered_coverpoints_crosses_detailed(
        self, covergroup_name: str, wp: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        covergroup = self.get_covergroup(covergroup_name, wp)
        if covergroup is None:
            return []

        subitems = list(covergroup.get("coverpoints", [])) + list(covergroup.get("crosses", []))
        return [entry for entry in subitems if self._is_not_fully_covered(entry)]

    def get_uncovered_coverpoints_crosses_inside_covergroup(
        self, covergroup_name: str, wp: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        return self.get_uncovered_coverpoints_crosses(covergroup_name, wp)

    def get_uncovered_bins(self, covergroup_name: str, wp: Optional[str] = None) -> List[Dict[str, Any]]:
        covergroup = self.get_covergroup(covergroup_name, wp)
        if covergroup is None:
            return []

        coverpoint_logic_by_name: Dict[str, str] = {}
        for coverpoint in covergroup.get("coverpoints", []):
            if not isinstance(coverpoint, dict):
                continue
            coverpoint_name = str(coverpoint.get("name", "")).strip()
            sampling_expression = self._extract_sampling_expression(coverpoint)
            if coverpoint_name and sampling_expression:
                coverpoint_logic_by_name[coverpoint_name] = sampling_expression

        grouped_uncovered_bins: List[Dict[str, Any]] = []
        for subitem in self._get_uncovered_coverpoints_crosses_detailed(covergroup_name, wp):
            uncovered_bin_names = [
                bin_entry.get("name", "")
                for bin_entry in subitem.get("bins", [])
                if self._is_not_fully_covered(bin_entry)
            ]
            if not uncovered_bin_names:
                continue

            parent_type = str(subitem.get("type", "unknown"))
            sampling_expression = self._extract_sampling_expression(subitem)
            sampled_on_logic = self._resolve_sampled_on_logic(
                item_type=parent_type,
                sampling_expression=sampling_expression,
                coverpoint_logic_by_name=coverpoint_logic_by_name,
            )

            grouped_uncovered_bins.append(
                {
                    "parent_name": subitem.get("name", ""),
                    "parent_type": parent_type,
                    "sampling_expression": sampling_expression,
                    "sampled_on": sampled_on_logic,
                    "bin_names": uncovered_bin_names,
                }
            )

        return grouped_uncovered_bins

    def get_uncovered_bins_inside_coverpoints_crosses(
        self, covergroup_name: str, wp: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        return self.get_uncovered_bins(covergroup_name, wp)

    def get_uncovered_bin_details(
        self,
        covergroup_name: str,
        parent_name: str,
        bin_name: str,
        wp: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return full details for one uncovered bin identified by names."""
        covergroup = self.get_covergroup(covergroup_name, wp)
        if covergroup is None:
            return None

        subitems = list(covergroup.get("coverpoints", [])) + list(covergroup.get("crosses", []))
        for subitem in subitems:
            if subitem.get("name") != parent_name:
                continue

            for bin_entry in subitem.get("bins", []):
                if bin_entry.get("name") != bin_name:
                    continue
                if not self._is_not_fully_covered(bin_entry):
                    return None

                return {
                    "covergroup": {
                        "covergroup_name": covergroup.get("covergroup_name", ""),
                        "title": covergroup.get("title", ""),
                        "all_average_grd": covergroup.get("all_average_grd", ""),
                        "all_cov": covergroup.get("all_cov", ""),
                    },
                    "parent": {
                        "name": subitem.get("name", ""),
                        "type": subitem.get("type", "unknown"),
                        "all_average_grd": subitem.get("all_average_grd", ""),
                        "all_cov": subitem.get("all_cov", ""),
                        "columns": subitem.get("columns", ""),
                        "source": subitem.get("source", ""),
                    },
                    "bin": bin_entry,
                }

        return None

    def _default_output_path(self, wp: str) -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = self.report_path.resolve().parent.parent / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / f"parsed_covergroups_{wp}_{ts}.json"


if __name__ == "__main__":
    default_report = CovergroupReportAnalyzer.newest_report(Path(__file__).resolve().parent / "reports")
    if default_report is None:
        raise SystemExit("No report file found under reports/")

    analyzer = CovergroupReportAnalyzer(default_report)
    wp_names = analyzer.get_available_wps()
    print("Available WPs:")
    for wp_name in wp_names:
        print(f"- {wp_name}")