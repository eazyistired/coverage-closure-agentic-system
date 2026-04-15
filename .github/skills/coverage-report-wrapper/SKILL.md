---
name: coverage-report-wrapper
description: 'Use when you need to parse vManager .report coverage files, list uncovered covergroups/coverpoints/cross bins, and fetch full details for a specific uncovered bin. Includes a self-contained Python wrapper and analyzer scripts.'
argument-hint: 'report path, WP name, and the uncovered-bin triad (covergroup_name, parent_name, bin_name)'
user-invocable: true
---

# Coverage Report Wrapper

## Purpose
Use this skill to run a minimal-data coverage triage loop on a vManager `.report` file:
1. Parse report and choose a WP.
2. Get uncovered covergroup names only.
3. For each covergroup, get uncovered coverpoint/cross names only.
4. For each uncovered coverpoint/cross, get uncovered bin names only.
5. Request full details only for one specific uncovered bin when needed.

This keeps agent payloads small and deterministic.

## Bundled Files
- Analyzer class: [scripts/covergroup_report_analyzer.py](./scripts/covergroup_report_analyzer.py)
- CLI wrapper: [scripts/covergroup_report_wrapper.py](./scripts/covergroup_report_wrapper.py)

These files are intentionally bundled inside the skill so the workflow is portable.

## When To Use
- You need an AI agent to process uncovered bins one-by-one.
- You want minimal response payloads for iteration.
- You want a separate detail lookup for a specific uncovered bin.

## Procedure
1. Parse and inspect available WPs.
2. Select a WP.
3. List uncovered covergroups (names only).
4. For each covergroup, list uncovered coverpoints/crosses (names only).
5. For each covergroup, list uncovered bins (name triads only).
6. For a selected uncovered bin, fetch full details.

## Example Commands
Run from repository root:

```bash
/bin/python3.11 .github/skills/coverage-report-wrapper/scripts/covergroup_report_wrapper.py parse --report reports/data_15-04-2026_09~14~48.report
```

```bash
/bin/python3.11 .github/skills/coverage-report-wrapper/scripts/covergroup_report_wrapper.py get-available-wps --report reports/data_15-04-2026_09~14~48.report
```

```bash
/bin/python3.11 .github/skills/coverage-report-wrapper/scripts/covergroup_report_wrapper.py get-uncovered-covergroups --report reports/data_15-04-2026_09~14~48.report --wp HSS --compact-json
```

```bash
/bin/python3.11 .github/skills/coverage-report-wrapper/scripts/covergroup_report_wrapper.py get-uncovered-coverpoints-crosses --report reports/data_15-04-2026_09~14~48.report --wp HSS --covergroup-name dscov_HSS_01_mode_change --compact-json
```

```bash
/bin/python3.11 .github/skills/coverage-report-wrapper/scripts/covergroup_report_wrapper.py get-uncovered-bins --report reports/data_15-04-2026_09~14~48.report --wp HSS --covergroup-name dscov_HSS_01_mode_change --compact-json
```

```bash
/bin/python3.11 .github/skills/coverage-report-wrapper/scripts/covergroup_report_wrapper.py get-uncovered-bin-details --report reports/data_15-04-2026_09~14~48.report --wp HSS --covergroup-name dscov_HSS_01_mode_change --parent-name SBC_MODE_HSS1_MODE_crs --bin-name 'STOP;ON' --compact-json
```

## Decision Points
- If `--report` is omitted, wrapper auto-discovers nearest `reports/*.report` from workspace ancestors.
- If `--wp` is omitted for export, wrapper prompts for interactive WP selection.
- If a bin is covered or missing, `get-uncovered-bin-details` returns `found: false`.

## Quality Checks
- `parse` returns `wp_count > 0`.
- Uncovered list commands return only identifiers (no large nested payloads).
- Detail command returns one targeted payload (`found: true`) for a valid uncovered bin.

## Suggested Agent Loop
1. Call `get-uncovered-covergroups`.
2. For each returned `covergroup_name`, call `get-uncovered-bins`.
3. For each returned bin tuple (`covergroup_name`, `parent_name`, `bin_name`), run your analysis logic.
4. Only when needed, call `get-uncovered-bin-details` for full context.
