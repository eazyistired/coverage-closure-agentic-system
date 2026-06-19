"""Static parser for UVM coverage SVH files.

Extracts per covergroup:
- Name and line location
- Jama documentation block (preceding comment)
- Sample function arguments (explicit parameters, if any)
- Sampling trigger: the @(...) event expression from the sample task
- Trigger variables (individual tokens from the @(...) list)
- Sampled variables: coverpoint expressions + explicit sample args
- Cross declarations
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_CG_START_RE = re.compile(
    r"^\s*covergroup\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s+with\s+function\s+sample\s*\((?P<args>[^\)]*)\))?"
)
_CP_RE = re.compile(
    r"^\s*(?P<cp>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*coverpoint\s+(?P<expr>[^{\s;][^{;]*)"
)
_CROSS_RE = re.compile(
    r"^\s*(?P<crs>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*cross\s+(?P<cps>[^{\s;][^{;]*)"
)
_SAMPLE_TASK_RE = re.compile(r"^\s*task\s+sample_(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(")
_EVENT_TRIGGER_RE = re.compile(r"forever\s+@\s*\((?P<expr>[^\)]+)\)")
_DOC_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

_SV_TYPE_KEYWORDS = {
    "int",
    "bit",
    "logic",
    "real",
    "string",
    "event",
    "byte",
    "integer",
    "shortint",
    "longint",
    "time",
    "chandle",
    "void",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CoverpointNode:
    name: str
    expression: str  # raw SV expression being sampled
    sampled_variables: list[str]  # variable names extracted from expression


@dataclass
class CrossNode:
    name: str
    coverpoints: list[str]  # coverpoint names being crossed


@dataclass
class CovergroupNode:
    name: str
    declaration_line: int
    documentation: str = ""  # raw Jama comment block
    sample_args: list[str] = field(
        default_factory=list
    )  # explicit sample() param names
    sampling_trigger_expression: str = ""  # @(...)
    trigger_variables: list[str] = field(default_factory=list)
    sampled_variables: list[str] = field(default_factory=list)
    coverpoints: list[CoverpointNode] = field(default_factory=list)
    crosses: list[CrossNode] = field(default_factory=list)
    covergroup_sv_source: str = ""  # raw SV: covergroup...endgroup
    sample_task_sv_source: str = ""  # raw SV: task sample_XXX...endtask


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class CoverageParser:
    """Parse a UVM coverage SVH file into a list of CovergroupNode objects."""

    def __init__(self, coverage_file: str | Path) -> None:
        self._path = Path(coverage_file)
        self._text = self._path.read_text(encoding="utf-8", errors="ignore")
        self._lines = self._text.splitlines()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self) -> list[CovergroupNode]:
        covergroups = self._parse_covergroups()
        self._attach_sample_tasks(covergroups)
        return covergroups

    # ------------------------------------------------------------------
    # Covergroup block parsing
    # ------------------------------------------------------------------

    def _parse_covergroups(self) -> list[CovergroupNode]:
        cgs: list[CovergroupNode] = []
        depth = 0
        current: CovergroupNode | None = None
        skip_until_lineno: int = 0  # lines consumed by multi-line sample() look-ahead
        cg_start_lineno: int = 0  # 1-based line where covergroup keyword appears

        for lineno, raw in enumerate(self._lines, start=1):
            if lineno <= skip_until_lineno:
                continue

            line = raw.strip()

            # Detect covergroup start
            m = _CG_START_RE.match(raw)
            if m and depth == 0:
                args_raw = m.group("args") or ""
                # Multi-line `with function sample(\n  arg1,\n  arg2\n)` case:
                # the regex can't capture args when ')' is on a later line.
                if args_raw == "" and "sample(" in raw:
                    args_raw, skip_until_lineno = self._collect_multiline_args(
                        raw, lineno
                    )
                args = self._parse_sample_args(args_raw)
                doc = self._find_preceding_doc(lineno - 1)
                current = CovergroupNode(
                    name=m.group("name"),
                    declaration_line=lineno,
                    documentation=doc,
                    sample_args=args,
                )
                cg_start_lineno = lineno
                depth = 1
                continue

            if current is None:
                continue

            # Track nesting
            if "covergroup" in line or line.startswith("fork"):
                depth += 1
            if line == "endgroup":
                depth -= 1
                if depth == 0:
                    # Capture raw SV source from covergroup line to endgroup (inclusive)
                    current.covergroup_sv_source = "\n".join(
                        self._lines[cg_start_lineno - 1 : lineno]
                    )
                    self._finalise_sampled_vars(current)
                    cgs.append(current)
                    current = None
                continue

            # Parse coverpoint
            cp_m = _CP_RE.match(raw)
            if cp_m and depth == 1:
                expr = cp_m.group("expr").strip().rstrip("{").strip()
                svars = self._extract_tokens(expr)
                current.coverpoints.append(
                    CoverpointNode(
                        name=cp_m.group("cp"),
                        expression=expr,
                        sampled_variables=svars,
                    )
                )
                continue

            # Parse cross
            crs_m = _CROSS_RE.match(raw)
            if crs_m and depth == 1:
                cps = [s.strip() for s in crs_m.group("cps").split(",")]
                current.crosses.append(
                    CrossNode(name=crs_m.group("crs"), coverpoints=cps)
                )

        return cgs

    # ------------------------------------------------------------------
    # Sample task parsing (attaches trigger to covergroup)
    # ------------------------------------------------------------------

    def _attach_sample_tasks(self, covergroups: list[CovergroupNode]) -> None:
        """Find each sample_XXX task and extract the @(...) trigger and raw SV source."""
        cg_by_name = {cg.name: cg for cg in covergroups}
        in_task: CovergroupNode | None = None
        task_start_lineno: int = 0

        for lineno, raw in enumerate(self._lines, start=1):
            # Detect task start
            tm = _SAMPLE_TASK_RE.match(raw)
            if tm:
                in_task = cg_by_name.get(tm.group("name"))
                task_start_lineno = lineno
                continue

            if in_task is None:
                continue

            if raw.strip() == "endtask":
                # Capture full task source
                in_task.sample_task_sv_source = "\n".join(
                    self._lines[task_start_lineno - 1 : lineno]
                )
                in_task = None
                continue

            # Look for the forever @(...) trigger
            ev_m = _EVENT_TRIGGER_RE.search(raw)
            if ev_m and not in_task.sampling_trigger_expression:
                expr = ev_m.group("expr").strip()
                in_task.sampling_trigger_expression = f"@({expr})"
                in_task.trigger_variables = self._extract_event_vars(expr)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_sample_args(raw_args: str) -> list[str]:
        """Extract parameter names from a sample() signature string."""
        if not raw_args.strip():
            return []
        args = []
        for part in raw_args.split(","):
            part = part.strip()
            if not part:
                continue
            # last token is the parameter name
            tokens = part.split()
            if tokens:
                args.append(tokens[-1].strip("[]"))
        return args

    def _collect_multiline_args(
        self, first_line: str, first_lineno: int
    ) -> tuple[str, int]:
        """Collect the full content of a `sample(...)` that spans multiple lines.

        Returns ``(args_string, last_consumed_lineno)`` where *args_string* is the
        text between the opening ``(`` and closing ``)`` and
        *last_consumed_lineno* is the 1-based index of the line on which the ``)``
        was found (caller should skip lines up to and including this index).
        """
        # Locate the opening '(' after 'sample'
        open_pos = first_line.find("sample(")
        if open_pos == -1:
            return "", first_lineno

        collected = first_line[open_pos + len("sample(") :]  # text after the '('
        depth = 1  # we consumed one '('
        depth += collected.count("(") - collected.count(")")

        last_lineno = first_lineno
        lines = self._lines  # 0-indexed list

        while depth > 0:
            next_idx = last_lineno  # 0-based index of the *next* line
            if next_idx >= len(lines):
                break
            next_raw = lines[next_idx]
            collected += " " + next_raw
            depth += next_raw.count("(") - next_raw.count(")")
            last_lineno += 1

        # Strip everything from the closing ')' onward
        close_pos = collected.rfind(")")
        if close_pos != -1:
            collected = collected[:close_pos]

        return collected.strip(), last_lineno

    @staticmethod
    def _extract_tokens(expr: str) -> list[str]:
        """Extract meaningful SV variable/path tokens from an expression."""
        _skip = {
            "inside",
            "with",
            "iff",
            "bins",
            "ignore_bins",
            "wildcard",
            "option",
            "coverpoint",
            "cross",
        } | _SV_TYPE_KEYWORDS
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", expr)
        return [t for t in tokens if t.lower() not in _skip and not t[0].isupper()]

    @staticmethod
    def _extract_event_vars(trigger_expr: str) -> list[str]:
        """Extract variable paths from @(a, b.c.d, e) style expression."""
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", trigger_expr)
        return [t for t in tokens if t.lower() not in {"posedge", "negedge", "edge"}]

    def _find_preceding_doc(self, start_lineno_0based: int) -> str:
        """Find the last /* ... */ comment block ending just before start_lineno."""
        preceding = "\n".join(self._lines[:start_lineno_0based])
        matches = list(_DOC_BLOCK_RE.finditer(preceding))
        if not matches:
            return ""
        last = matches[-1].group(0)
        # Accept only if it ends within 3 lines of the covergroup
        end_line = preceding[: matches[-1].end()].count("\n")
        if start_lineno_0based - end_line <= 3:
            return last
        return ""

    @staticmethod
    def _finalise_sampled_vars(cg: CovergroupNode) -> None:
        """Merge coverpoint expressions + sample args into sampled_variables."""
        seen: set[str] = set()
        result: list[str] = []

        def _add(v: str) -> None:
            if v and v not in seen:
                seen.add(v)
                result.append(v)

        for arg in cg.sample_args:
            _add(arg)
        for cp in cg.coverpoints:
            for sv in cp.sampled_variables:
                _add(sv)
        # Also include trigger variables (they are observable when sampling)
        for tv in cg.trigger_variables:
            _add(tv)

        cg.sampled_variables = result


# ---------------------------------------------------------------------------
# Helper: serialise to plain dicts for JSON
# ---------------------------------------------------------------------------


def covergroups_to_dicts(covergroups: list[CovergroupNode]) -> list[dict[str, Any]]:
    result = []
    for cg in covergroups:
        result.append(
            {
                "id": f"cg:{cg.name}",
                "name": cg.name,
                "declaration_line": cg.declaration_line,
                "documentation": cg.documentation,
                "sample_args": cg.sample_args,
                "sampling_trigger_expression": cg.sampling_trigger_expression,
                "trigger_variables": cg.trigger_variables,
                "sampled_variables": cg.sampled_variables,
                "coverpoints": [
                    {
                        "name": cp.name,
                        "expression": cp.expression,
                        "sampled_variables": cp.sampled_variables,
                    }
                    for cp in cg.coverpoints
                ],
                "crosses": [
                    {"name": crs.name, "coverpoints": crs.coverpoints}
                    for crs in cg.crosses
                ],
                "covergroup_sv_source": cg.covergroup_sv_source,
                "sample_task_sv_source": cg.sample_task_sv_source,
                "semantic_summary": "",
            }
        )
    return result
