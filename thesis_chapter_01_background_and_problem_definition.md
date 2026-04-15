# Chapter 1: Background and Problem Definition

## 1. Introduction

This project studies how functional coverage-gap analysis can be automated with LLM-based tooling while remaining grounded in the concrete artifacts of a digital verification environment. The broader research direction, described in the project outline, is to reduce the manual debug effort required after a regression reaches only partial functional coverage. In a conventional workflow, an engineer inspects an uncovered bin in a report, searches the corresponding covergroup in the verification code, reconstructs the intended scenario, and then checks whether the scenario is missing because of constraints, missing sequences, or because the scenario is fundamentally unreachable. That investigation is technically rich, but operationally repetitive and time-consuming.

The repository implements the first deterministic building blocks of such a system. Instead of asking a language model to infer everything from raw files, the codebase structures the problem into smaller retrieval steps. One layer extracts precise coverage-gap candidates from vManager report data. Another layer retrieves the semantic context of a specific covergroup from the SystemVerilog environment, including the covergroup declaration, its documentation block, its sample task, and the golden-model variables used by that coverage logic. Together, these components form the basis of a multi-agent coverage analysis flow.

## 2. Functional Coverage Gaps

Functional coverage is a verification metric used to measure whether the intended behaviors of a design have been exercised during simulation. In SystemVerilog and UVM environments, this is typically encoded through covergroups, coverpoints, crosses, and bins. A coverpoint captures the distribution of a sampled variable or expression, while a cross measures combinations of multiple sampled conditions. Each bin represents one scenario or value range that the verification plan considers important.

A functional coverage gap appears when one or more of those bins remain unhit after executing a regression. In practice, that means the test environment has not demonstrated a required functional situation. The gap may indicate that the random stimulus never generated the right conditions, that an intended directed sequence has not been implemented, that a transition is blocked by constraints or initialization order, or that the scenario is unreachable because of configuration or dead code. The important point is that an uncovered bin is not yet a diagnosis. It is only an observation that a targeted behavior has not been observed.

This is why functional coverage gaps matter. Coverage closure is not merely a reporting exercise; it is a core verification activity. If uncovered bins are ignored, the team may tape out with unverified scenarios. If they are investigated inefficiently, verification closure slows down and engineering effort is wasted on repetitive source-code navigation. For that reason, coverage-gap analysis is both a quality problem and a productivity problem.

## 3. Why Coverage Reports Alone Are Not Enough

A raw coverage report provides evidence that a bin is uncovered, but it usually does not explain the meaning of that bin or the reason it was missed. For a human engineer, the next step is always contextualization. The engineer reads the covergroup source, identifies the sampled variables, locates the task or event that triggers sampling, and then maps those symbols back to the golden model or scoreboard state that produced them. Only after that can the engineer decide whether the gap is caused by stimulus, sequencing, configuration, or impossibility.

This repository reflects that reality. The implemented context-retrieval tooling does not stop at the name of a covergroup. It reconstructs the semantic neighborhood of the gap by extracting four kinds of information:

1. The documentation block placed above the covergroup, which acts as a human-written description of the requirement or scenario.
2. The exact covergroup code block, which contains the formal sampling logic, coverpoints, crosses, and bin definitions.
3. The matching sample task, which shows when and how the covergroup is sampled.
4. The relevant golden-model variable context, including declarations and update snippets for only the symbols referenced by the selected covergroup.

The golden model is especially relevant because it is the bridge between observed DUT behavior and the internal semantic state used by coverage. Many coverage expressions do not sample direct DUT pins; they sample interpreted states, mirrored register fields, filtered status variables, previous-state history, or derived conditions. Those values are maintained in the scoreboard or golden-model layer. Without accessing that layer, an automated analysis can see the syntax of a coverpoint but still miss the actual meaning of the scenario being measured.

In other words, the project treats a coverage gap as a problem of evidence linking. The uncovered bin comes from the report, but its meaning comes from the coverage code, and its executable semantics come from the golden-model environment. That design choice is essential for any serious automated root-cause analysis.

## 4. Why Environment Context Must Be Included

Including parts of the verification environment in the analysis flow is not optional if the goal is trustworthy interpretation. In a modern UVM-based environment, the meaning of a coverage expression is often distributed across multiple artifacts: the coverage file defines the observed condition, the sample task defines when sampling occurs, and the golden model defines the derived state variables being sampled. A report-only workflow can identify a missing bin, but it cannot explain the internal verification meaning of that missing observation.

This project therefore treats the testbench environment as a semantic knowledge source rather than as passive background. The golden model, scoreboard variables, mirrored register fields, and event-driven sampling logic all contribute to the interpretation of coverage. As a consequence, automated gap analysis must be environment-aware if it is to support meaningful root-cause classification.

## 5. Chapter Summary

Functional coverage gaps are important because they expose missing evidence in the verification process, but they do not explain themselves. An uncovered bin is only the starting point of analysis. To understand why a scenario is missing, the analysis system must combine report data with code-level and environment-level context. That requirement motivates the architecture adopted in this project, in which deterministic retrieval of semantic evidence precedes any higher-level reasoning about root cause.