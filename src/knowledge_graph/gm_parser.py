"""Static parser for UVM golden-model SVH files.

Extracts:
- Variable declarations (type, name, comment, register trace, variable group)
- Task / function signatures (name, line)
- Events (name, comment)
- Variable assignment sites (which task/context writes to which variable)
- Cross-model reference tokens  (p_scb.XXX_gm.var patterns)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_VAR_DECL_RE = re.compile(
    r"^\s*"
    r"(?P<type>[A-Za-z_][A-Za-z0-9_:\s\[\]]*?)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P<arr>\s*\[[^\]]*\])?"
    r"\s*(?P<init>=\s*[^;]+)?;"
    r"\s*(?://\s*(?P<comment>.*))?$"
)

_EVENT_DECL_RE = re.compile(
    r"^\s*event\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;" r"\s*(?://\s*(?P<comment>.*))?$"
)

_TASK_RE = re.compile(
    r"^\s*(?:virtual\s+)?task\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*" r"(?:\(|;)"
)
_FUNC_RE = re.compile(
    r"^\s*(?:virtual\s+)?function\s+\S+\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("
)

# Register trace: "value of the field REG.FIELD" or "mirrors REG.FIELD"
_REG_TRACE_RE = re.compile(
    r"(?:value\s+of\s+the\s+field|field)\s+(?P<reg>[A-Z][A-Z0-9_]+\.[A-Z][A-Z0-9_]+)",
    re.IGNORECASE,
)

# Cross-model refs like p_scb.fsm_gm.var  or  p_scb.can_gm.something
_CROSS_REF_RE = re.compile(
    r"\bp_scb\.(?P<wp>[A-Za-z0-9]+)_gm\.(?P<var>[A-Za-z_][A-Za-z0-9_]*)\b"
)

# Assignment: varname <= expr  or  varname = expr  (not ==)
_ASSIGN_RE_TMPL = r"\b{name}\s*(?:<=|(?<!=)=(?!=))"

_SECTION_COMMENT_RE = re.compile(r"^\s*/{3,}\s*(?P<section>[^/].+?)\s*/{0,3}\s*$")

_SKIP_TYPES = {
    "task",
    "function",
    "class",
    "typedef",
    "virtual",
    "module",
    "endmodule",
    "`include",
    "`define",
}

_SV_KEYWORDS = {
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
    "fork",
    "join",
    "join_any",
    "join_none",
    "default",
    "new",
    "super",
    "null",
    "this",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class VariableNode:
    name: str
    sv_type: str
    comment: str = ""
    variable_group: str = ""  # logical group inferred from section comment
    register_trace: str = ""  # e.g. "HW_CTRL2.WK1_HS1_CFG"
    assigned_by_tasks: list[str] = field(default_factory=list)
    declaration_line: int = 0


@dataclass
class EventNode:
    name: str
    comment: str = ""
    declaration_line: int = 0


@dataclass
class TaskNode:
    name: str
    kind: str = "task"  # "task" | "function"
    updates_variables: list[str] = field(default_factory=list)
    declaration_line: int = 0


@dataclass
class CrossRef:
    """Cross-model variable reference: p_scb.XXX_gm.var"""

    wp_token: str  # e.g. "fsm"
    var_name: str  # e.g. "fsm_current_state"
    full_path: str  # e.g. "p_scb.fsm_gm.fsm_current_state"


@dataclass
class GoldenModelAST:
    class_name: str
    source_file: str
    variables: list[VariableNode] = field(default_factory=list)
    events: list[EventNode] = field(default_factory=list)
    tasks: list[TaskNode] = field(default_factory=list)
    cross_refs: list[CrossRef] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class GoldenModelParser:
    """Parse a UVM golden-model SVH file into a GoldenModelAST."""

    def __init__(self, golden_model_file: str | Path) -> None:
        self._path = Path(golden_model_file)
        self._lines = self._path.read_text(
            encoding="utf-8", errors="ignore"
        ).splitlines()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self) -> GoldenModelAST:
        class_name = self._detect_class_name()
        variables = self._parse_variables()
        events = self._parse_events()
        tasks = self._parse_tasks()
        self._annotate_assignments(variables, tasks)
        cross_refs = self._collect_cross_refs()
        return GoldenModelAST(
            class_name=class_name,
            source_file=str(self._path),
            variables=variables,
            events=events,
            tasks=tasks,
            cross_refs=cross_refs,
        )

    # ------------------------------------------------------------------
    # Class name detection
    # ------------------------------------------------------------------

    def _detect_class_name(self) -> str:
        for line in self._lines:
            m = re.search(r"\bclass\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)", line)
            if m:
                return m.group("name")
        return self._path.stem

    # ------------------------------------------------------------------
    # Variable parsing
    # ------------------------------------------------------------------

    def _parse_variables(self) -> list[VariableNode]:
        variables: list[VariableNode] = []
        current_section = ""

        for lineno, raw in enumerate(self._lines, start=1):
            line = raw.strip()

            # Track section comments like "/////// HSS modes ///////"
            sec_m = _SECTION_COMMENT_RE.match(raw)
            if sec_m:
                current_section = sec_m.group("section").strip("/ ").strip()
                continue

            if not line or line.startswith("//"):
                continue

            # Skip event lines (handled separately)
            if _EVENT_DECL_RE.match(raw):
                continue

            m = _VAR_DECL_RE.match(raw)
            if not m:
                continue

            type_raw = m.group("type").strip()
            # Skip keywords that look like declarations but aren't variables
            if type_raw.split()[0] in _SKIP_TYPES:
                continue
            if type_raw.split()[-1] in {"task", "function", "class"}:
                continue

            name = m.group("name")
            if name in _SV_KEYWORDS:
                continue

            comment = (m.group("comment") or "").strip()
            reg_trace = self._extract_register_trace(comment)

            variables.append(
                VariableNode(
                    name=name,
                    sv_type=type_raw,
                    comment=comment,
                    variable_group=current_section,
                    register_trace=reg_trace,
                    declaration_line=lineno,
                )
            )

        return variables

    @staticmethod
    def _extract_register_trace(comment: str) -> str:
        """Extract register.field from a comment like 'value of the field HW_CTRL2.WK1_HS1_CFG'."""
        m = _REG_TRACE_RE.search(comment)
        return m.group("reg") if m else ""

    # ------------------------------------------------------------------
    # Event parsing
    # ------------------------------------------------------------------

    def _parse_events(self) -> list[EventNode]:
        events: list[EventNode] = []
        for lineno, raw in enumerate(self._lines, start=1):
            m = _EVENT_DECL_RE.match(raw)
            if m:
                events.append(
                    EventNode(
                        name=m.group("name"),
                        comment=(m.group("comment") or "").strip(),
                        declaration_line=lineno,
                    )
                )
        return events

    # ------------------------------------------------------------------
    # Task / function parsing
    # ------------------------------------------------------------------

    def _parse_tasks(self) -> list[TaskNode]:
        tasks: list[TaskNode] = []
        for lineno, raw in enumerate(self._lines, start=1):
            m = _TASK_RE.match(raw)
            if m:
                tasks.append(
                    TaskNode(name=m.group("name"), kind="task", declaration_line=lineno)
                )
                continue
            m = _FUNC_RE.match(raw)
            if m:
                tasks.append(
                    TaskNode(
                        name=m.group("name"), kind="function", declaration_line=lineno
                    )
                )
        return tasks

    # ------------------------------------------------------------------
    # Assignment annotation  (which task writes to which variable)
    # ------------------------------------------------------------------

    def _annotate_assignments(
        self, variables: list[VariableNode], tasks: list[TaskNode]
    ) -> None:
        """For each variable, find which tasks contain an assignment to it."""
        if not tasks or not variables:
            return

        # Build sorted task spans (start_line → task)
        task_starts = sorted(t.declaration_line for t in tasks)
        task_by_start: dict[int, TaskNode] = {t.declaration_line: t for t in tasks}

        full_text = "\n".join(self._lines)

        for var in variables:
            pattern = re.compile(_ASSIGN_RE_TMPL.format(name=re.escape(var.name)))
            for m in pattern.finditer(full_text):
                # Determine which line number this match is on (1-based)
                lineno = full_text[: m.start()].count("\n") + 1
                # Find the enclosing task (nearest task start ≤ lineno)
                enclosing_start = None
                for ts in task_starts:
                    if ts <= lineno:
                        enclosing_start = ts
                    else:
                        break
                if enclosing_start is not None:
                    task_name = task_by_start[enclosing_start].name
                    task_id = f"task:{task_name}"
                    if task_id not in var.assigned_by_tasks:
                        var.assigned_by_tasks.append(task_id)
                    # Also record the reverse: task updates this variable
                    task = task_by_start[enclosing_start]
                    var_id = f"var:{var.name}"
                    if var_id not in task.updates_variables:
                        task.updates_variables.append(var_id)

    # ------------------------------------------------------------------
    # Cross-model reference collection
    # ------------------------------------------------------------------

    def _collect_cross_refs(self) -> list[CrossRef]:
        refs: list[CrossRef] = []
        seen: set[str] = set()
        full_text = "\n".join(self._lines)
        for m in _CROSS_REF_RE.finditer(full_text):
            full_path = m.group(0)
            if full_path in seen:
                continue
            seen.add(full_path)
            refs.append(
                CrossRef(
                    wp_token=m.group("wp"),
                    var_name=m.group("var"),
                    full_path=full_path,
                )
            )
        return refs


# ---------------------------------------------------------------------------
# Helper: serialise to plain dicts for JSON
# ---------------------------------------------------------------------------


def ast_to_dict(ast: GoldenModelAST) -> dict[str, Any]:
    return {
        "class_name": ast.class_name,
        "source_file": ast.source_file,
        "variables": [
            {
                "id": f"var:{v.name}",
                "name": v.name,
                "type": v.sv_type,
                "comment": v.comment,
                "variable_group": v.variable_group,
                "register_trace": v.register_trace,
                "assigned_by_tasks": v.assigned_by_tasks,
                "declaration_line": v.declaration_line,
                "semantic_summary": "",
            }
            for v in ast.variables
        ],
        "events": [
            {
                "id": f"event:{e.name}",
                "name": e.name,
                "comment": e.comment,
                "declaration_line": e.declaration_line,
                "semantic_summary": "",
            }
            for e in ast.events
        ],
        "tasks": [
            {
                "id": f"task:{t.name}",
                "name": t.name,
                "kind": t.kind,
                "updates_variables": t.updates_variables,
                "declaration_line": t.declaration_line,
                "semantic_summary": "",
            }
            for t in ast.tasks
        ],
        "cross_refs": [
            {
                "full_path": cr.full_path,
                "wp_token": cr.wp_token,
                "var_name": cr.var_name,
            }
            for cr in ast.cross_refs
        ],
    }
