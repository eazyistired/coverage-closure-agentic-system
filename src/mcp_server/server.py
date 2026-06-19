"""FastAPI + fastapi-mcp server exposing vManager coverage report data as MCP tools.

Endpoints
---------
POST /wps                    — list available work packages in a report
POST /uncovered-covergroups  — list uncovered covergroup names for a given WP
POST /uncovered-bins         — list uncovered bins grouped by parent (coverpoint/cross)

Usage
-----
    python src/mcp_server/server.py
    # or via uvicorn directly:
    uvicorn src.mcp_server.server:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mcp import FastApiMCP
import uvicorn

from .covergroup_report_analyzer import CovergroupReportAnalyzer
from .models import (
    CovergroupEntry,
    UncoveredBinGroup,
    UncoveredBinsRequest,
    UncoveredBinsResponse,
    UncoveredCovergroupsRequest,
    UncoveredCovergroupsResponse,
    WpsRequest,
    WpsResponse,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _resolve_report(report_path: str) -> Path:
    """Return an absolute Path for *report_path*, resolving relative paths against the workspace root."""
    path = Path(report_path)
    if not path.is_absolute():
        path = _WORKSPACE_ROOT / path
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Report file not found: {path}")
    return path


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="coverage-gap-analyzer-mcp",
    summary="MCP server for querying vManager coverage report data.",
    description=(
        "Exposes vManager .report file contents as queryable endpoints. "
        "Designed to be consumed by AI agents via the Model Context Protocol (MCP)."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post(
    "/wps",
    response_model=WpsResponse,
    description=(
        "Parse a vManager .report file and return the list of available work packages (WPs). "
        "Call this first to discover which WP names can be used in subsequent requests."
    ),
)
def get_wps(body: WpsRequest) -> WpsResponse:
    report = _resolve_report(body.report_path)
    analyzer = CovergroupReportAnalyzer(report)
    wps = analyzer.get_available_wps()
    return WpsResponse(wps=wps, wp_count=len(wps))


@app.post(
    "/uncovered-covergroups",
    response_model=UncoveredCovergroupsResponse,
    description=(
        "Return the names of all uncovered (< 100%) covergroups for a specific work package. "
        "Use the returned 'covergroup_name' values as input to /uncovered-bins."
    ),
)
def get_uncovered_covergroups(
    body: UncoveredCovergroupsRequest,
) -> UncoveredCovergroupsResponse:
    report = _resolve_report(body.report_path)
    analyzer = CovergroupReportAnalyzer(report)

    wp_upper = body.wp.upper().strip()
    available = analyzer.get_available_wps()
    if wp_upper not in available:
        raise HTTPException(
            status_code=404,
            detail=f"WP '{wp_upper}' not found in report. Available WPs: {available}",
        )

    raw = analyzer.get_uncovered_covergroups(wp=wp_upper)
    covergroups = [
        CovergroupEntry(covergroup_name=item["covergroup_name"]) for item in raw
    ]
    return UncoveredCovergroupsResponse(
        wp=wp_upper,
        covergroups=covergroups,
        covergroup_count=len(covergroups),
    )


@app.post(
    "/uncovered-bins",
    response_model=UncoveredBinsResponse,
    description=(
        "Return all uncovered bins for a named covergroup, grouped by their parent coverpoint or cross. "
        "Each group includes the sampling expression and the list of uncovered bin names. "
        "Use 'sampled_on' to understand which design variables each bin exercises."
    ),
)
def get_uncovered_bins(body: UncoveredBinsRequest) -> UncoveredBinsResponse:
    report = _resolve_report(body.report_path)
    analyzer = CovergroupReportAnalyzer(report)

    wp_filter = body.wp.upper().strip() if body.wp else None
    raw_groups = analyzer.get_uncovered_bins(
        covergroup_name=body.covergroup_name, wp=wp_filter
    )

    if not raw_groups:
        # Distinguish "covergroup not found" from "covergroup fully covered"
        cg = analyzer.get_covergroup(body.covergroup_name, wp=wp_filter)
        if cg is None:
            raise HTTPException(
                status_code=404,
                detail=f"Covergroup '{body.covergroup_name}' not found in the report.",
            )

    bin_groups = [
        UncoveredBinGroup(
            parent_name=g["parent_name"],
            parent_type=g["parent_type"],
            sampling_expression=g["sampling_expression"],
            sampled_on=g["sampled_on"],
            bin_names=g["bin_names"],
        )
        for g in raw_groups
    ]
    total = sum(len(g.bin_names) for g in bin_groups)
    return UncoveredBinsResponse(
        covergroup_name=body.covergroup_name,
        wp=wp_filter,
        uncovered_bin_groups=bin_groups,
        total_uncovered_bins=total,
    )


# ---------------------------------------------------------------------------
# MCP mount
# ---------------------------------------------------------------------------

mcp = FastApiMCP(app)
mcp.mount()

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
