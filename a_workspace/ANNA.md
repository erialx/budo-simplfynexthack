# ANNA work log

A's running log of work done in this folder. Newest session at the top.

---

## Session: Saturday 5 September 2026

### Context at the start

The team had moved a long way since the `ANNA` branch was last touched. `origin/main`
had advanced 10 commits (to `d4a4f8f`) with C's Stage 1, Stage 2, Stage 3 and C1 work
plus D's `daphne-demo-ready` fork merged in, and `PLAN.md` had been rewritten and moved
to `docs/PLAN.md`. A's assigned top priority in that new plan: port `SafetyWatchdog` and
`HazardVetoAgent` into `NavigationRunner` and stop the `WAYPOINT_STOP_OVERRIDE` reflex
from overriding a real safety stop.

### 1. Repo sync

- Fetched `origin`. Found `main` at `d4a4f8f`, plus a new `daphne-demo-ready` branch.
- Stashed 152 pre-existing modified files before merging. These were not mine and were
  never committed: every one shows an identical insertion and deletion count, which is
  the signature of a bulk line ending conversion, not real edits. They are still
  recoverable with `git stash list` if anyone wants them back.
- Merged `origin/main` into `ANNA` cleanly. No conflicts.
- Re-ran the suite after the merge: 98 passed, 1 skipped (the skip is `test_grpc_bridge.py`,
  which needs `grpc` installed, unrelated to any of this).

### 2. Built the NavigationRunner safety integration

The gap, restated: D's live demo runs through `NavigationRunner` in
`NaVILA-Orca/src/navila_orca/runner.py`, which is a completely separate loop from C's
per-step MCP bridge. That loop had zero watchdog and zero veto wiring, so nothing stopped
its forward-nudge reflex (`premature_stop_recovery_command`) from overriding a stop that
came from a genuine safety trip. C's `session.stop_override_suppressed` flag in
`navila_bridge.py` was built for exactly this but had no consumer, because the reflex it
was meant to suppress lives in a file that bridge never calls.

Built in `a_workspace/safety_integration.py`:

- **`SafeVelocityPhysicsBackend`** wraps a `VelocityPhysicsBackend`. It ticks the
  `SafetyWatchdog` on every physics tick, which is the reactive 20Hz layer, and once
  tripped it freezes the robot in place (same position, incremented `step_id`,
  `terminated=True`) instead of advancing physics. `runner.py`'s own tick loop already
  breaks out on `step.terminated`, so this makes an existing code path fire for the right
  reason rather than adding a new one.
- **`SafeVLMClient`** wraps a `VLMClient`. Once per decision, which is the ~1Hz tactical
  cadence from the architecture, it runs a `HazardVetoAgent` check on the frame the driver
  just reasoned over, before that decision's motion executes. A VETO, or an already tripped
  watchdog, makes it return `"stop."` regardless of what the wrapped VLM actually said.
- **`SafeNavigationRunner`** subclasses `NavigationRunner` and overrides exactly one thing:
  `premature_stop_recovery_command` becomes a property that reads back `None` when this
  decision's stop came from a trip or a veto. `runner.py`'s own unmodified override logic
  then sees "no recovery command configured" and waits for the next decision instead of
  forcing forward motion over a real safety stop. `run()` is inherited untouched.
- **`build_safe_runner()`** assembles all of the above the same way C's
  `_build_safety_stack` and `_build_veto_stack` assemble the per-step bridge's stack, and
  returns the runner plus the watchdog, veto agent and logbook so a caller can inspect
  `.tripped`, `.log` and `.dump()`.

### Design decision: composition, not a patch to runner.py

`NavigationRunner.__init__` already accepts `physics`, `vlm_client` and
`premature_stop_recovery_command` as plain constructor arguments. That is the seam its own
author built for exactly this kind of substitution, so wrapping those three reproduces the
behaviour C verified in `navila_bridge.py` without editing a single line of code anyone
else owns. Files deliberately not touched: `runner.py`, `navila_bridge.py`,
`bridge_backends.py`, `cli.py`, `mjlab_go2.py`, `traffic_crossing.py` and all of their
tests.

### Files created

| File | What it is |
|---|---|
| `a_workspace/safety_integration.py` | The three wrappers plus `build_safe_runner()` (305 lines) |
| `a_workspace/tests/test_safety_integration.py` | 4 integration tests |
| `a_workspace/README.md` | What this folder is, how to run it, what is left before it lands |
| `a_workspace/ANNA.md` | This log |

### Verified

`PYTHONPATH=NaVILA-Orca/src python3 -m pytest a_workspace/tests/ -q` gives 4 passed.

The tests drive the **real** `NavigationRunner`, `SafetyWatchdog` and `HazardVetoAgent`
end to end. Only the physics backend, renderer and driver VLM are faked, which matches how
`runner.py`'s own constructor treats those three as pluggable. What they cover:

1. Normal operation is completely unaffected by the wrapping.
2. A watchdog trip mid-chunk freezes the robot and ends the run as `terminated`, with the
   trip recorded in the decision logbook.
3. A veto stops the decision before any physics runs at all (`control_steps == 0`).
4. The actual gap: a veto'd stop is **not** overridden by `WAYPOINT_STOP_OVERRIDE`, while
   an ordinary premature VLM stop in the same run still gets nudged forward exactly as D
   designed it. This one asserts on final position, so it proves behaviour rather than
   just flag state.

Full existing suite after all of this: 98 passed, 1 skipped. Nothing outside
`a_workspace/` was modified, confirmed by `git status`.

### Not verified, and honest about it

- Nothing here has run against a real `MjlabGo2Backend`, a real OrcaLab renderer or a real
  VLM. The pattern is proven against the real safety and runner code, but with fakes at
  those three seams.
- `cli.py` is untouched, so the live demo command does **not** pick any of this up yet.
  Swapping `NavigationRunner(...)` for `build_safe_runner(...)` there is small and
  mechanical, but that file is D's, so it is left for review or coordination.
- `SafeVelocityPhysicsBackend` only covers the velocity-facade path, which is what
  `MjlabGo2Backend` implements. The joint-action facade is not used by this project today
  and is out of scope.

### Next steps for A

1. Commit this to `ANNA` and push, so the team can see it at the meeting.
2. Run `build_safe_runner()` against a real `MjlabGo2Backend` plus the OrcaLab renderer on
   the GPU box. This is the main thing standing between "design proven" and "actually
   protects the demo".
3. Agree with D on wiring it into `cli.py`, then do that change.
4. `docs/PLAN.md` A item 1, still open and quick: add `anthropic` to
   `NaVILA-Orca/pyproject.toml` and smoke-test `AnthropicVetoVisionClient` against one
   real saved OrcaLab frame with an API key.
5. `docs/PLAN.md` A item 2, low priority: make `HazardVetoAgent.assess` and
   `ScenarioInjector.inject` accept a raw `np.ndarray` as well as PIL. C already worked
   around this locally, so it is cleanliness rather than a blocker.
6. `docs/PLAN.md` A item 4, tuning `SafeForceBand`, `debounce_ticks` and the veto cadence,
   stays blocked on C2 landing a real loop with measurable latency.
