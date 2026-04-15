#!/usr/bin/env python3
"""Deterministic context extraction for SystemVerilog covergroup workflows."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


_COVERGROUP_START_RE = re.compile(
    r"^\s*covergroup\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b"
)
_SAMPLE_TASK_START_RE = re.compile(
    r"^\s*task\s+sample_(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("
)
_COVERPOINT_RE = re.compile(
    r"^\s*(?P<cp>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*coverpoint\s+(?P<expr>[^\{;]+)"
)
_CROSS_RE = re.compile(
    r"^\s*(?P<crs>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*cross\s+(?P<expr>[^\{;]+)"
)
_EVENT_RE = re.compile(r"@\s*\((?P<expr>[^\)]*)\)")
_TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\b")
_DECL_RE = re.compile(
    r"^\s*(?P<type>[A-Za-z_][A-Za-z0-9_:\s\[\]]*[A-Za-z0-9_\]])\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?P<init>=\s*[^;]+)?;\s*(?://\s*(?P<comment>.*))?$"
)
_ASSIGN_RE_TEMPLATE = r"\b{name}\b\s*(?:<=|=)\s*"

_KEYWORDS = {
    "if",
    "else",
    "begin",
    "end",
    "case",
    "endcase",
    "for",
    "foreach",
    "while",
    "forever",
    "do",
    "wait",
    "return",
    "task",
    "endtask",
    "function",
    "endfunction",
    "class",
    "endclass",
    "covergroup",
    "endgroup",
    "coverpoint",
    "cross",
    "bins",
    "ignore_bins",
    "wildcard",
    "option",
    "with",
    "inside",
    "iff",
    "fork",
    "join",
    "join_any",
    "join_none",
    "default",
    "new",
    "super",
}

_MACRO_PREFIX = "`"
_SV_BUILTIN_TYPES = {
    "bit",
    "int",
    "real",
    "string",
    "event",
    "logic",
    "integer",
    "time",
    "uvm_verbosity",
}


@dataclass
class Span:
    start: int  # 1-based
    end: int  # 1-based


class ContextRetrievalAnalyzer:
    def __init__(
        self, coverage_file: Path | str, golden_model_file: Path | str | None = None
    ) -> None:
        self.coverage_file = Path(coverage_file)
        self.golden_model_file = (
            Path(golden_model_file) if golden_model_file is not None else None
        )
        self._coverage_lines = self.coverage_file.read_text(
            encoding="utf-8", errors="ignore"
        ).splitlines()
        self._golden_lines = (
            self.golden_model_file.read_text(
                encoding="utf-8", errors="ignore"
            ).splitlines()
            if self.golden_model_file is not None
            else []
        )
        self._declarations: Optional[Dict[str, Dict[str, Any]]] = None

    def list_covergroups(self) -> List[str]:
        names: List[str] = []
        for line in self._coverage_lines:
            match = _COVERGROUP_START_RE.match(line)
            if match:
                names.append(match.group("name"))
        return names

    def get_covergroup_context(
        self, covergroup_name: str, wp: Optional[str] = None
    ) -> Dict[str, Any]:
        cg_span = self._find_named_block(
            _COVERGROUP_START_RE, "endgroup", covergroup_name
        )
        if cg_span is None:
            raise ValueError(
                f"Covergroup '{covergroup_name}' not found in coverage file"
            )

        doc_span = self._find_preceding_doc_block(cg_span.start)
        task_span = self._find_named_block(
            _SAMPLE_TASK_START_RE, "endtask", covergroup_name, prefix="sample_"
        )

        doc_text = self._slice_lines(self._coverage_lines, doc_span) if doc_span else ""
        covergroup_text = self._slice_lines(self._coverage_lines, cg_span)
        sample_text = (
            self._slice_lines(self._coverage_lines, task_span) if task_span else ""
        )

        entities = self._parse_entities(covergroup_text, sample_text)
        symbol_info = self._resolve_symbols(covergroup_text, sample_text)

        return {
            "ok": True,
            "wp": wp,
            "covergroup_name": covergroup_name,
            "files": {
                "coverage_file": str(self.coverage_file),
                "golden_model_file": str(self.golden_model_file),
            },
            "line_spans": {
                "documentation": self._span_to_dict(doc_span),
                "covergroup": self._span_to_dict(cg_span),
                "sample_task": self._span_to_dict(task_span),
            },
            "blocks": {
                "documentation": doc_text,
                "covergroup": covergroup_text,
                "sample_task": sample_text,
            },
            "entities": entities,
            "variable_context": symbol_info["resolved"],
            "unresolved_symbols": symbol_info["unresolved"],
            "warnings": self._build_warnings(doc_text, task_span, symbol_info),
        }

    def _collect_golden_declarations(self) -> Dict[str, Dict[str, Any]]:
        declarations: Dict[str, Dict[str, Any]] = {}
        for idx, line in enumerate(self._golden_lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            match = _DECL_RE.match(line)
            if not match:
                continue
            type_name = " ".join(match.group("type").split())
            if type_name.split()[0] in {
                "task",
                "function",
                "class",
                "typedef",
                "virtual",
            }:
                continue
            name = match.group("name")
            if name in declarations:
                continue
            init_raw = (match.group("init") or "").strip()
            comment = (match.group("comment") or "").strip()
            declarations[name] = {
                "declared_type": type_name,
                "declaration_line": idx,
                "declaration_snippet": line.strip(),
                "initial_value": (
                    init_raw[1:].strip() if init_raw.startswith("=") else None
                ),
                "comment": comment or None,
            }
        return declarations

    def _find_named_block(
        self,
        start_re: re.Pattern[str],
        end_keyword: str,
        target_name: str,
        prefix: str = "",
    ) -> Optional[Span]:
        start_line: Optional[int] = None
        for idx, line in enumerate(self._coverage_lines, start=1):
            match = start_re.match(line)
            if not match:
                continue
            name = match.group("name")
            if prefix and name.startswith(prefix):
                name = name[len(prefix) :]
            if name == target_name:
                start_line = idx
                break

        if start_line is None:
            return None

        for idx in range(start_line, len(self._coverage_lines) + 1):
            if self._coverage_lines[idx - 1].strip().startswith(end_keyword):
                return Span(start=start_line, end=idx)
        raise ValueError(
            f"Unterminated block for '{target_name}' (missing {end_keyword})"
        )

    def _find_preceding_doc_block(self, covergroup_start_line: int) -> Optional[Span]:
        idx = covergroup_start_line - 2  # zero-based for line above covergroup
        while idx >= 0 and not self._coverage_lines[idx].strip():
            idx -= 1

        if idx < 0 or "*/" not in self._coverage_lines[idx]:
            return None

        end_line = idx + 1
        while idx >= 0:
            if "/*" in self._coverage_lines[idx]:
                return Span(start=idx + 1, end=end_line)
            idx -= 1
        return None

    def _slice_lines(self, lines: List[str], span: Optional[Span]) -> str:
        if span is None:
            return ""
        return "\n".join(lines[span.start - 1 : span.end])

    def _parse_entities(self, covergroup_text: str, sample_text: str) -> Dict[str, Any]:
        coverpoints: List[Dict[str, str]] = []
        crosses: List[Dict[str, str]] = []

        for line in covergroup_text.splitlines():
            cp_match = _COVERPOINT_RE.match(line)
            if cp_match:
                coverpoints.append(
                    {
                        "name": cp_match.group("cp"),
                        "expression": cp_match.group("expr").strip(),
                    }
                )
            crs_match = _CROSS_RE.match(line)
            if crs_match:
                crosses.append(
                    {
                        "name": crs_match.group("crs"),
                        "expression": crs_match.group("expr").strip(),
                    }
                )

        triggers: List[str] = []
        for match in _EVENT_RE.finditer(sample_text):
            expr = " ".join(match.group("expr").split())
            if expr:
                triggers.append(expr)

        sample_call = None
        sample_match = re.search(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\.sample\s*\((?P<args>.*?)\)\s*;",
            sample_text,
            re.DOTALL,
        )
        if sample_match:
            sample_call = {
                "instance": sample_match.group(1),
                "arguments": " ".join(sample_match.group("args").split()),
            }

        return {
            "coverpoints": coverpoints,
            "crosses": crosses,
            "sample_triggers": triggers,
            "sample_call": sample_call,
        }

    def _resolve_symbols(
        self, covergroup_text: str, sample_text: str
    ) -> Dict[str, Any]:
        if self.golden_model_file is None:
            raise ValueError("Golden model file is required for symbol resolution")

        if self._declarations is None:
            self._declarations = self._collect_golden_declarations()

        raw_tokens = self._extract_tokens(covergroup_text) | self._extract_tokens(
            sample_text
        )
        local_decls = self._extract_local_declared_names(
            covergroup_text + "\n" + sample_text
        )
        skip_roots = set(local_decls)

        resolved: Dict[str, Dict[str, Any]] = {}
        unresolved_map: Dict[str, Dict[str, Any]] = {}

        for token in sorted(raw_tokens):
            root = token.split(".", 1)[0]
            if root in skip_roots:
                continue
            if root in _KEYWORDS:
                continue
            if root in _SV_BUILTIN_TYPES:
                continue
            if root.endswith("_cp") or root.endswith("_crs"):
                continue
            if root.startswith("dscov_") or root.startswith("dcov_"):
                continue
            if root.startswith("sample_"):
                continue
            if root.startswith("ignore_"):
                continue
            if root in resolved:
                continue
            decl = self._declarations.get(root)
            if decl is None:
                unresolved_map[root] = {
                    "symbol": root,
                    "occurrence_example": token,
                    "reason": "No declaration found in golden model file",
                }
                continue

            updates = self._find_update_context(root)
            resolved[root] = {
                "symbol": root,
                "declared_type": decl["declared_type"],
                "declaration_line": decl["declaration_line"],
                "declaration_snippet": decl["declaration_snippet"],
                "initial_value": decl["initial_value"],
                "inferred_role": self._infer_role(root, decl.get("comment")),
                "comment": decl.get("comment"),
                "update_context": updates,
                "confidence": "high" if decl.get("comment") else "medium",
            }

        unresolved_sorted = [unresolved_map[key] for key in sorted(unresolved_map)]
        return {"resolved": resolved, "unresolved": unresolved_sorted}

    def _extract_tokens(self, text: str) -> Set[str]:
        tokens: Set[str] = set()
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            if stripped.startswith(_MACRO_PREFIX):
                continue
            for token in _TOKEN_RE.findall(line):
                if token in _KEYWORDS:
                    continue
                if token.isupper():
                    continue
                if token.endswith("_cp") or token.endswith("_crs"):
                    continue
                if token in {"sample", "name", "uvm_info", "sformatf"}:
                    continue
                tokens.add(token)
        return tokens

    def _extract_local_declared_names(self, text: str) -> Set[str]:
        local_names: Set[str] = set()
        local_decl_re = re.compile(
            r"^\s*(?:const\s+)?[A-Za-z_][A-Za-z0-9_:\s\[\]]*\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*[^;]+)?;"
        )
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            match = local_decl_re.match(line)
            if match:
                local_names.add(match.group(1))
        return local_names

    def _find_update_context(self, symbol: str) -> List[Dict[str, Any]]:
        assign_re = re.compile(_ASSIGN_RE_TEMPLATE.format(name=re.escape(symbol)))
        updates: List[Dict[str, Any]] = []
        for idx, line in enumerate(self._golden_lines, start=1):
            if line.strip().startswith("//"):
                continue
            if assign_re.search(line):
                updates.append({"line": idx, "snippet": line.strip()})
            if len(updates) >= 5:
                break
        return updates

    def _infer_role(self, symbol: str, comment: Optional[str]) -> str:
        lowered = symbol.lower()
        if comment:
            return comment
        if "prev_mode" in lowered:
            return "Previous HSS mode value used for transition-aware logic"
        if lowered.endswith("_mode"):
            return "Current HSS operating mode"
        if lowered.startswith("field_"):
            return "Mirrored register field value"
        if lowered.endswith("_filtered"):
            return "Filtered diagnostic/status signal"
        if lowered.endswith("_cond_b") or lowered.endswith("_condition_b"):
            return "Boolean condition flag used in control logic"
        if lowered.startswith("timer") or lowered.startswith("pwm"):
            return "Timer/PWM related model variable"
        return "Golden-model variable used by coverage logic"

    def _build_warnings(
        self,
        doc_text: str,
        task_span: Optional[Span],
        symbol_info: Dict[str, Any],
    ) -> List[str]:
        warnings: List[str] = []
        if not doc_text:
            warnings.append(
                "Documentation block was not found immediately above the covergroup"
            )
        if task_span is None:
            warnings.append("Sample task block sample_<covergroup_name> was not found")
        if symbol_info["unresolved"]:
            warnings.append(
                "Some referenced symbols were not resolved in golden model declarations"
            )
        if "TODO" in doc_text or "FIXME" in doc_text:
            warnings.append("Documentation block contains TODO/FIXME markers")
        return warnings

    @staticmethod
    def _span_to_dict(span: Optional[Span]) -> Optional[Dict[str, int]]:
        if span is None:
            return None
        return {"start_line": span.start, "end_line": span.end}
