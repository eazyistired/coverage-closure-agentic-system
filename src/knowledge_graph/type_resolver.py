"""Type and register-enum resolver for UVM SV files.

Parses two shared files and produces lookup tables used to enrich
variable nodes in the WP knowledge graph at build time.

Sources
-------
- ``common.svh``      → typedef enum definitions  (ifx_car_sbc_hss_mode_t, etc.)
- ``sfr_enums.svh``   → register field enums       (TIMER1_CTRL_TIMER1_ON_ENUM, etc.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# typedef enum [base_type] { ... } type_name ;
_TYPEDEF_ENUM_RE = re.compile(
    r"typedef\s+enum\s*(?:[^\{]*)\s*\{"
    r"(?P<body>[^}]+)"
    r"\}\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;",
    re.DOTALL,
)

# Individual enum member: NAME [= value] [, // comment]
_MEMBER_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s*=\s*(?P<val>[^\s,/}]+))?"
    r"(?:\s*//\s*(?P<comment>[^\n]*))?"
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class EnumTypeDef:
    """A typedef enum from common.svh."""

    name: str  # e.g. "ifx_car_sbc_hss_mode_t"
    members: list[str]  # e.g. ["HSS_ON", "HSS_OFF", ...]
    member_comments: dict[str, str] = field(default_factory=dict)


@dataclass
class RegisterEnumDef:
    """A register-field enum from sfr_enums.svh."""

    name: str  # e.g. "TIMER1_CTRL_TIMER1_ON_ENUM"
    values: dict[str, int | str]  # e.g. {"SETTING1": 0, "SETTING2": 1, ...}
    comments: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class TypeResolver:
    """
    Parses the shared type-definition and register-enum files and exposes
    look-up helpers used by KGBuilder to enrich variable nodes.
    """

    def __init__(
        self,
        common_file: str | Path | None = None,
        sfr_enums_file: str | Path | None = None,
        sfr_enums_files: list[str | Path] | None = None,
    ) -> None:
        self._type_defs: dict[str, EnumTypeDef] = {}
        self._reg_enums: dict[str, RegisterEnumDef] = {}

        if common_file:
            self._parse_common(Path(common_file))

        # Normalise: accept both the old single-file kwarg and a list
        all_sfr: list[Path] = []
        if sfr_enums_files:
            all_sfr.extend(Path(p) for p in sfr_enums_files)
        if sfr_enums_file:
            all_sfr.append(Path(sfr_enums_file))
        for p in all_sfr:
            self._parse_sfr_enums(p)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_common(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in _TYPEDEF_ENUM_RE.finditer(text):
            type_name = m.group("name")
            members, comments = self._parse_enum_body(m.group("body"))
            self._type_defs[type_name] = EnumTypeDef(
                name=type_name,
                members=members,
                member_comments=comments,
            )

    def _parse_sfr_enums(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in _TYPEDEF_ENUM_RE.finditer(text):
            enum_name = m.group("name")
            members, comments = self._parse_enum_body(m.group("body"))
            # Parse integer values for SETTING-style enums
            values: dict[str, int | str] = {}
            raw_body = m.group("body")
            for mm in _MEMBER_RE.finditer(raw_body):
                member_name = mm.group("name")
                if not member_name or member_name.lower() in {"int", "bit", "logic"}:
                    continue
                raw_val = (mm.group("val") or "").strip()
                if raw_val:
                    # Convert hex 'hXX or integer literals
                    try:
                        if raw_val.startswith("'h") or raw_val.startswith("'H"):
                            values[member_name] = int(raw_val[2:], 16)
                        elif raw_val.startswith("0x") or raw_val.startswith("0X"):
                            values[member_name] = int(raw_val, 16)
                        elif raw_val.startswith("'b") or raw_val.startswith("'B"):
                            values[member_name] = int(raw_val[2:], 2)
                        else:
                            values[member_name] = int(raw_val, 0)
                    except ValueError:
                        values[member_name] = raw_val  # keep as string if unparseable
                else:
                    # Auto-increment (not tracked here — just record 0-based position)
                    values[member_name] = len(values)

            self._reg_enums[enum_name] = RegisterEnumDef(
                name=enum_name,
                values=values,
                comments=comments,
            )

    @staticmethod
    def _parse_enum_body(body: str) -> tuple[list[str], dict[str, str]]:
        members: list[str] = []
        comments: dict[str, str] = {}
        for m in _MEMBER_RE.finditer(body):
            name = m.group("name")
            if not name or name.lower() in {"int", "bit", "logic", "enum", "typedef"}:
                continue
            comment = (m.group("comment") or "").strip()
            members.append(name)
            if comment:
                comments[name] = comment
        return members, comments

    # ------------------------------------------------------------------
    # Look-up helpers
    # ------------------------------------------------------------------

    def resolve_type(self, type_name: str) -> dict[str, Any] | None:
        """Return type definition dict for a common.svh typedef, or None."""
        td = self._type_defs.get(type_name)
        if td is None:
            return None
        return {
            "kind": "enum",
            "members": td.members,
            "member_comments": td.member_comments,
        }

    def resolve_register_enum(self, type_name: str) -> dict[str, Any] | None:
        """Return register enum dict for a sfr_enums.svh typedef, or None."""
        # Try exact match, then strip trailing _ENUM suffix variants
        re_def = self._reg_enums.get(type_name)
        if re_def is None:
            return None
        return {
            "type": re_def.name,
            "values": re_def.values,
            "comments": re_def.comments,
        }

    def is_register_enum(self, type_name: str) -> bool:
        return type_name in self._reg_enums

    @property
    def type_def_count(self) -> int:
        return len(self._type_defs)

    @property
    def register_enum_count(self) -> int:
        return len(self._reg_enums)
