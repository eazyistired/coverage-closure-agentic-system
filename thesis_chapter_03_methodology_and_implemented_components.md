# Chapter 3: Methodology and Implemented Components

## 1. Methodological Principle

The methodology implemented in this project is based on a simple principle: reasoning about coverage gaps should occur only after the relevant evidence has been extracted and normalized. Rather than prompting a language model with a large collection of raw files, the repository breaks the problem into deterministic retrieval stages. Each stage reduces the search space, structures the available information, and preserves a clear connection to the original verification artifacts.

This methodology is appropriate for functional verification because coverage analysis is inherently evidence-driven. An uncovered bin in a report does not contain enough information for root-cause diagnosis by itself. The workflow therefore proceeds from identification, to semantic contextualization, to later classification.

## 2. Coverage Report Wrapper Skill

The coverage-report-wrapper skill is the project’s triage front end. It is designed around a minimal-data philosophy. Instead of loading the entire report into an agent prompt, the wrapper exposes narrow commands that progressively reduce the search space. An agent can first identify which work packages exist, then fetch only uncovered covergroup names for one work package, then fetch only uncovered coverpoints or crosses for a selected covergroup, then fetch only uncovered bins, and finally request full details for one chosen bin.

This approach is highly suitable for multi-agent systems. It keeps payloads deterministic, reduces token cost, and avoids mixing retrieval with reasoning. From an engineering perspective, it also mirrors how a verification engineer debugs coverage: first isolate the failing area, then inspect one specific missing scenario in detail.

The underlying analyzer reinforces that design. It parses covergroup blocks from the report, extracts tagged covergroup names, groups them by work package, classifies subitems as coverpoints or crosses, and filters results according to whether coverage is complete or incomplete. The wrapper exposes this logic as a clean CLI surface for orchestration.

## 3. Context Retrieval Skill

The context-retrieval skill addresses the central weakness of report-only analysis: lack of semantic grounding. Given a coverage file, a golden-model file, and a covergroup name, it reconstructs the local evidence needed to understand the gap.

Its analyzer performs several concrete tasks. It locates the selected covergroup block in the SystemVerilog coverage file. It searches for the documentation block immediately above that covergroup. It finds a matching `sample_<covergroup_name>` task, which often captures the operational condition under which sampling occurs. It parses coverpoints, crosses, event triggers, and the sample call. Then it resolves the symbols used in those blocks against declarations in the golden model and retrieves a bounded set of update snippets showing where those values are assigned.

This is precisely why golden-model context belongs in the project methodology. Coverage logic often samples interpreted state rather than raw interface signals. By resolving only the variables actually referenced by the selected covergroup, the skill builds a compact but semantically rich context package. That makes later reasoning more defensible and less likely to hallucinate unrelated state.

## 4. Context Retrieval Agent

The custom context-retrieval agent is a thin but important layer above the skill. Its role is not to add intelligence; its role is to constrain intelligence. The agent instructions require a single wrapper invocation, prohibit unrelated inference, preserve unresolved symbols, and enforce a JSON-only response with a fixed top-level schema.

From a methodological perspective, this is a significant design choice. In a verification workflow, determinism and auditability are often more important than conversational fluency. The agent therefore behaves more like a controlled retrieval operator than a free-form assistant. That pattern is valuable when integrating LLMs into engineering signoff flows.

## 5. vManager Access Layer as Execution-Side Foundation

Although the repository’s most visible custom agent is the context-retrieval agent, the execution side is already partially represented by the vManager access utilities. The `VManagerAccess` client can authenticate, inspect sessions, generate HTML reports, and query metrics endpoints with sticky-context support. It can list coverage types, covergroups, covergroup items, and bins for a selected session.

This means the project is not limited to offline report analysis. It already contains the technical ingredients required to move toward live acquisition from the verification-management database. In the long term, that supports the execution-agent role described in the project outline.

## 6. Why the Golden Model Must Be in the Methodology

Including the golden model in the analysis loop is not just helpful; for many covergroups it is necessary. A coverage gap can only be classified correctly if the analysis system understands the semantic variables behind the coverpoint expressions. For example, a transition bin may depend on current state, previous state, filtered conditions, mirrored register settings, and sample timing. Those are usually maintained in the scoreboard or golden-model layer, not in the raw DUT interface.

The repository’s implementation confirms this. The context-retrieval analyzer extracts declarations from the golden model, identifies update locations for referenced variables, infers basic roles from naming and comments, and emits unresolved symbols when no matching declaration exists. This produces an evidence trail that explains how a coverage expression is connected to the verification environment.

For the thesis, the consequence is straightforward: automated functional coverage-gap analysis is not a pure report-mining problem. It is an environment-aware interpretation problem. The report identifies what is missing, but the golden model and coverage source explain what that missing item means.

## 7. Current Methodological Boundary

The current implementation supports deterministic retrieval and semantic grounding, but it does not yet implement a fully autonomous analyst stage that classifies every gap into a fixed taxonomy of causes. This boundary should be stated clearly in the thesis. The present work establishes the retrieval and context-construction pipeline upon which later root-cause classification can be built.

That limitation does not weaken the contribution. On the contrary, it reflects a disciplined methodology: reliable extraction and evidence normalization are established first, before higher-level agent reasoning is introduced. In a verification setting, that ordering is technically sound because it reduces the risk of unsupported interpretation.

## 8. Chapter Summary

The methodology used in this project is based on deterministic extraction, structured context building, and constrained agent interfaces. The implemented skills do not attempt to replace verification reasoning with opaque generation. Instead, they prepare the evidence needed for later reasoning by narrowing the problem, grounding it in source artifacts, and making the relationship between report data, coverage code, and golden-model state explicit. This methodology is a strong foundation for extending the project toward full multi-agent root-cause classification.