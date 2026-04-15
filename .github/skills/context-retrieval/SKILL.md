---
name: context-retrieval
description: "Use when you need deterministic context retrieval for a named covergroup from verification files: fetch the documentation block, covergroup code, sample task, and resolved variable context from golden model declarations/updates. Ideal for coverage gap analysis workflows and downstream agent chaining."
argument-hint: "coverage file path, golden model path, covergroup name, optional WP"
user-invocable: true
---

# Context Retrieval Skill

## Purpose
Retrieve structured context for one covergroup so downstream agents/skills can reason over coverage gaps with minimal ambiguity.

## Outputs
The main command returns JSON with:
- covergroup documentation block (Jama comment)
- exact covergroup code block
- exact sample task block
- parsed entities (coverpoints, crosses, sample triggers)
- strict variable context map (referenced variables only)

## Bundled Files
- Analyzer class: [scripts/context_retrieval_analyzer.py](./scripts/context_retrieval_analyzer.py)
- CLI wrapper: [scripts/context_retrieval_wrapper.py](./scripts/context_retrieval_wrapper.py)

## When To Use
- You need semantic/code context for a specific covergroup during coverage gap analysis.
- You need machine-readable payloads for orchestration by other skills/agents.
- You need variable-level context grounded in the golden model.

## Commands
```bash
/bin/python3.11 .github/skills/context-retrieval/scripts/context_retrieval_wrapper.py list-covergroups --coverage-file coverage.svh
```

```bash
/bin/python3.11 .github/skills/context-retrieval/scripts/context_retrieval_wrapper.py get-covergroup-context \
  --coverage-file coverage.svh \
  --golden-model-file golden_model.svh \
  --covergroup-name dscov_HSS_01_mode_change \
  --wp HSS \
  --compact-json
```

## Notes
- Variable context scope is strict: only symbols referenced in the selected covergroup/sample blocks are resolved.
- If symbols cannot be resolved in the golden model, they are reported in `unresolved_symbols` with reasons.
- The wrapper emits deterministic JSON suitable for chained automation.
