# Chapter 2: System Architecture and Project Structure

## 1. Target Architecture and Current State

The project outline describes a target multi-agent architecture composed of an execution agent, a context agent, and an analyst agent. The execution agent interacts with the verification-management system, the context agent translates uncovered coverage structures into semantically meaningful scenarios, and the analyst agent classifies the reason the gap exists. This decomposition is well suited to functional verification because the workflow naturally separates data acquisition, semantic reconstruction, and diagnosis.

The current repository implements the deterministic infrastructure for the first two phases more strongly than for the third. In its present form, the codebase provides:

- A report-processing layer that parses vManager report files and enumerates uncovered covergroups, uncovered coverpoints or crosses, and uncovered bins.
- A context-retrieval layer that maps a selected covergroup back to its coverage source file and associated golden-model file.
- A user-invocable agent definition that wraps the context-retrieval flow in a deterministic JSON contract suitable for downstream orchestration.
- An API-access layer for vManager that can generate reports and query metrics endpoints, which serves as the implementation foundation for an execution-side workflow.

What is not yet fully implemented as a dedicated autonomous layer is the final analyst component that classifies each gap into root-cause categories such as missing stimulus, unimplemented sequence, or unreachable scenario. The project is therefore best understood as an architecture in progress: the conceptual framework is broader, while the repository currently focuses on reliable extraction and semantic grounding.

## 2. Repository Organization

The repository is organized around a small orchestration workspace rather than a monolithic application. Each top-level area supports one part of the coverage-analysis pipeline.

### 2.1 Root-Level Python Tools

The workspace root contains the main Python utilities used for coverage parsing and wrapper-based interaction.

- `covergroup_report_analyzer.py` implements the parser and in-memory query model for vManager `.report` files. It groups covergroups by work package, extracts coverage percentages, distinguishes coverpoints from crosses, and provides minimal query methods that return only the identifiers needed for iterative agent workflows.
- `covergroup_report_wrapper.py` exposes the analyzer through a command-line interface. Its commands support parsing a report, listing available work packages, exporting parsed JSON, and retrieving uncovered entities at progressively finer granularity.
- `parse_covergroup_report.py` supports report parsing in script form.
- `project_outline.md` contains the conceptual thesis-level description of the problem and the intended multi-agent solution.

This root level acts as the report-triage layer of the project.

### 2.2 Skill and Agent Definitions

The `.github` directory contains the skill packaging that makes the tooling usable as agent capabilities.

- `.github/skills/coverage-report-wrapper` packages the report-analysis workflow as a reusable skill. Its documentation explicitly promotes a minimal-payload loop: parse the report, list uncovered covergroups, then narrow to uncovered items, then narrow to uncovered bins, and request full details only when necessary.
- `.github/skills/context-retrieval` packages deterministic semantic extraction for a named covergroup. It is explicitly designed to return machine-readable JSON containing documentation, covergroup code, sample task, parsed entities, resolved variable context, unresolved symbols, and warnings.
- `.github/agents/context-retrieval.agent.md` defines a specialized user-invocable agent that executes the context-retrieval wrapper exactly once and returns normalized JSON only. This is important because it constrains the agent toward retrieval rather than speculation.

These definitions are not incidental documentation. They are part of the implementation architecture because they define how a general-purpose LLM is expected to consume the underlying Python tools.

### 2.4 Data and Output Directories

- `reports` stores the raw vManager report files consumed by the parsing workflow.
- `outputs` stores structured JSON exports derived from those reports.
- `logs` is available for runtime traces and debugging output.

These directories separate raw evidence from derived artifacts. That separation is useful both for reproducibility and for thesis work, because it makes the transformation pipeline explicit.

### 2.5 Verification Environment Snapshot

The `tb` directory contains the verification-environment snapshot that supplies the semantic context required for analysis. The important observation is that the project does not treat the testbench as background material; it is part of the data source.

Within `tb/dig_mvc`, the package file includes multiple golden-model and coverage source files. The package structure shows a repeated pattern in which domain-specific golden models include their associated coverage files. This matters because it demonstrates a direct coupling between scoreboarding or behavioral state reconstruction and the functional coverage model. Additional directories such as `tb/dig_tb` and supporting UVC packages represent the larger UVM environment in which those models operate.

For the thesis, this is an important architectural point: the environment is not merely where tests run. It is where the semantic interpretation of coverage is encoded.

## 3. Architectural Interpretation

The repository can therefore be viewed as a layered system. At the bottom lies the verification environment, including the golden model and coverage source files. Above that lies the retrieval layer, which extracts structured evidence from reports and source code. Above that lies the agent layer, which defines how deterministic tools are exposed to an LLM-based orchestration flow. Finally, the intended top layer is an analysis and classification component that reasons over the retrieved evidence and maps each uncovered bin to a root-cause category.

This separation is important because it reduces ambiguity. Instead of letting an agent infer architectural meaning directly from a large and noisy workspace, the repository organizes evidence into increasingly structured layers. Such layering is well aligned with the requirements of verification engineering, where reproducibility, traceability, and controlled interpretation are more important than open-ended conversational behavior.

## 4. Chapter Summary

The system architecture described in the thesis outline is only partially realized as autonomous agents, but its deterministic foundations are already present in the repository. The workspace structure reflects a deliberate decomposition of the problem into report retrieval, semantic context extraction, vManager integration, and environment-aware evidence linking. This structure is not merely organizational; it directly supports the type of trustworthy automation required for coverage-gap analysis.