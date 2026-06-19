"""Pydantic request and response models for the MCP server."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class WpsRequest(BaseModel):
    report_path: str = Field(
        ...,
        description="Absolute or workspace-relative path to the vManager .report file.",
        examples=["inputs/data_16-05-2026_08~32~57.report"],
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{"report_path": "inputs/data_16-05-2026_08~32~57.report"}]
        }
    }


class UncoveredCovergroupsRequest(BaseModel):
    report_path: str = Field(
        ...,
        description="Absolute or workspace-relative path to the vManager .report file.",
    )
    wp: str = Field(
        ...,
        description="Work-package name (e.g. 'CAN', 'SPI'). Must be one of the values returned by /wps.",
        examples=["CAN"],
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "report_path": "inputs/data_16-05-2026_08~32~57.report",
                    "wp": "CAN",
                }
            ]
        }
    }


class UncoveredBinsRequest(BaseModel):
    report_path: str = Field(
        ...,
        description="Absolute or workspace-relative path to the vManager .report file.",
    )
    covergroup_name: str = Field(
        ...,
        description="Exact covergroup name as returned by /uncovered-covergroups.",
        examples=["dscov_CAN_01_frame_type"],
    )
    wp: Optional[str] = Field(
        default=None,
        description="Optional work-package filter to speed up lookup. If omitted the entire report is searched.",
        examples=["CAN"],
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "report_path": "inputs/data_16-05-2026_08~32~57.report",
                    "covergroup_name": "dscov_CAN_01_frame_type",
                    "wp": "CAN",
                }
            ]
        }
    }


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class WpsResponse(BaseModel):
    wps: List[str] = Field(
        description="Sorted list of work-package names found in the report."
    )
    wp_count: int = Field(description="Number of work packages found.")


class CovergroupEntry(BaseModel):
    covergroup_name: str


class UncoveredCovergroupsResponse(BaseModel):
    wp: str
    covergroups: List[CovergroupEntry] = Field(
        description="Uncovered covergroups (names only) for the requested WP."
    )
    covergroup_count: int


class UncoveredBinGroup(BaseModel):
    parent_name: str = Field(
        description="Name of the coverpoint or cross that owns these bins."
    )
    parent_type: str = Field(description="'coverpoint' or 'cross'.")
    sampling_expression: str = Field(
        description="Raw sampling expression from the source."
    )
    sampled_on: List[str] = Field(
        description="Resolved logic signals / expressions being sampled."
    )
    bin_names: List[str] = Field(
        description="Names of the uncovered bins under this parent."
    )


class UncoveredBinsResponse(BaseModel):
    covergroup_name: str
    wp: Optional[str]
    uncovered_bin_groups: List[UncoveredBinGroup]
    total_uncovered_bins: int = Field(
        description="Total number of individual uncovered bin names across all groups."
    )
