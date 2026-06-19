# UVM Knowledge — Shared Agent Context

## Covergroup anatomy
- A **covergroup** samples design state at defined clock edges or events.
- A **coverpoint** covers one expression; its **bins** partition the value space.
- A **cross** covers the cross-product of two or more coverpoints.
- A bin is **hit** when its condition is true at the sample point.
- Coverage percentage = (hit bins / total bins) × 100.

## Common reasons a bin is not hit
1. The stimulus required to reach the condition was never generated.
2. A constraint in the sequence or transaction prevented the value combination.
3. The design feature is gated behind a mode/register that was never enabled in tests.
4. Timing: the sample event fired before the condition was established.
5. The bin requires a specific sequence of events that no test exercises end-to-end.

## UVM test phases (relevant for gap analysis)
- `build_phase` — components created, configuration applied.
- `connect_phase` — TLM ports connected.
- `run_phase` — stimulus driven; most coverage accumulated here.
- `check_phase` — scoreboard checks; no new stimulus.

## Signal naming conventions in this testbench
- Coverpoint expressions reference internal scoreboard signals, not DUT ports directly.
- Cross coverpoints are named `<cp1>_X_<cp2>` or end with `_crs`.
- Golden-model outputs are compared in checkers (`*_checkers.svh`).
