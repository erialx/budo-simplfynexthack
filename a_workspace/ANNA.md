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
| `a_workspace/check_real_backend.py` | Validates the wrapper against a real MjlabGo2Backend (added later in the session, see section 5) |
| `a_workspace/run_check.sh` | One-line runner for the above; resolves the orcalab interpreter and the PYTHONPATH |
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

### 3. Added `anthropic` as an optional extra (docs/PLAN.md A item 1)

Pinned `anthropic==1.4.0` in `NaVILA-Orca/pyproject.toml` under a new `veto`
optional-dependency group, not as a base dependency. Reason: everything in
`navila_orca.veto` except `AnthropicVetoVisionClient` works with no SDK and no API
key, so the mock and demo paths should never be forced to install it. Install with
`pip install -e "NaVILA-Orca[veto]"`.

Smoke-tested against a real OrcaLab robot-view render
(`NaVILA-Orca/assets/cover/warehouse-robot-view.png`). What passed: the SDK installs
and imports, the deferred import path builds a real `anthropic.Anthropic` client,
`_encode_png` produces a valid PNG that round-trips, and the full request assembles
correctly with the real frame in the image block. What was not tested: an actual API
call, which needs a real key.

**Two problems that smoke test found, both still open, both in
`veto/claude_vision_client.py` (A's own file):**

1. **Frames are sent at full resolution as PNG.** That 2467x1219 render encodes to
   3,950,276 base64 characters, roughly 2.9MB, per veto call. At the ~1Hz tactical
   cadence the upload alone would blow the budget, and it buys nothing: the API
   downscales anything past about 1568px on the long edge anyway. Fix is to downscale
   before encoding and switch to JPEG, which for a photographic sim render should be
   one to two orders of magnitude smaller. This is a demo-latency bug, not cosmetic.
2. **A missing API key does not fail at construction.** `AnthropicVetoVisionClient()`
   built fine with `ANTHROPIC_API_KEY` unset, so the failure would land on the first
   real veto call instead. `HazardVetoAgent` defaults to VETO on any client error, so
   the fail-safe direction is right, but the visible symptom mid-demo would be the dog
   refusing to move at all with no obvious cause. Worth an explicit key check at
   construction or episode start.

**Also worth a decision:** the client's default model is still `claude-sonnet-4-5`.
It is a valid ID in the SDK's model list, so nothing is broken, but that list now also
carries `claude-sonnet-5`, `claude-opus-5` and `claude-haiku-4-5`. For a 1Hz binary
VETO/CLEAR gate, latency dominates, so `claude-haiku-4-5` is probably the better
default with a newer Sonnet as the accuracy fallback. Not changed unilaterally since
it affects demo behaviour and overlaps A item 4 (tuning).

### 4. Proved the unit tests are not vacuous (mutation testing)

Fair challenge raised: the tests pass, but I wrote both the code and the tests, so
passing only proves internal consistency. Answered by deliberately breaking each of
the three pieces and confirming the tests notice:

| Mutation | Result |
|---|---|
| Baseline, untouched | 4 passed |
| Override-suppression property ignores the safety flag | 1 failed, 3 passed |
| Watchdog never ticked on physics steps | 1 failed, 3 passed |
| A VETO is let through instead of forcing a stop | 2 failed, 2 passed |
| Restored | 4 passed |

`git status` clean afterwards, so the file is exactly what was committed. Anyone can
repeat this in two minutes: delete the two lines in
`premature_stop_recovery_command` that return `None`, rerun, watch one test fail.

### 5. Validated against the REAL MjlabGo2Backend

Wrote `a_workspace/check_real_backend.py` plus `a_workspace/run_check.sh`. It builds a
real `MjlabGo2Backend` (MJLab/MJWarp, real `go2_flat.pt` policy) and runs three
scenarios through `build_safe_runner()`. No OrcaLab GUI, no AWS tunnel and no GPU
needed, since the backend defaults to `device="cpu"`. The renderer and driver VLM are
still faked, as neither is what is under test.

**First real run: 6 of 7 checks passed.** On real physics with the real trained policy:

- Clean run: the dog walked 2.593 m over 4 decisions and 300 control steps with the
  wrapper in place, and the watchdog correctly stayed quiet on nominal force.
- Force drop: the watchdog tripped after exactly 3 consecutive out-of-band readings,
  the run ended as `terminated`, and the dog stopped after 8 control steps having
  travelled 0.018 m instead of 2.593 m. Logbook caught it verbatim:
  `STOP (safety_watchdog): harness force 0.00N outside safe band [20.0, 80.0]N for 3
  consecutive ticks`.
- Veto: the run ended with `control_steps: 0`, so not a single physics tick executed,
  and the logbook recorded `VETO (hazard_veto): pedestrian in the path`.

That is the core claim confirmed against real physics rather than against my own fakes.

**The one failure was the check, not the safety code.** C2 asserted "robot never moved"
and reported 0.011 m on a run where C1 had just proved zero physics ticks executed. The
two cannot both be about navigation. Cause: `MjlabGo2Backend.reset()` runs a
zero-velocity warmup of real physics steps to settle the Go2 onto its feet, explicitly
"outside the public navigation clock", and its reset events (`reset_base`,
`reset_robot_joints`, `randomize_terrain`) randomize the starting pose. The robot is
therefore about a centimetre off the world origin before decision 1, and I was
measuring distance from the origin, which counts that settling as navigation. Every
distance assertion now uses `NavigationMetrics.path_length`, which accumulates from the
post-reset pose and only grows on real navigation ticks.

**Not yet re-run after that fix.** Expect 7 of 7, but that is a prediction, not a
result. If C2 still reports a non-zero path length it is a genuine finding.

### 6. Two self-inflicted bugs worth remembering

- `run_check.sh` crashed on `USER: unbound variable`. Git Bash on Windows sets
  `USERNAME`, not `USER`, and the script runs under `set -u`. Fixed by defaulting both
  and allowing `PY=` as an explicit override.
- Python printed nothing for minutes and looked hung. Git Bash's terminal is a pipe
  rather than a real Windows console, so Python block-buffers stdout. Fixed with `-u`
  and `PYTHONUNBUFFERED=1`.
- A stray `line 64: script: command not found` appeared mid-run. That was me editing
  `run_check.sh` while bash was executing it: bash reads scripts incrementally by byte
  offset, so inserting lines shifted everything and it resumed inside a comment
  reading "the script looks hung for". Confirmed by `git show` on that commit. Harmless,
  but do not edit a shell script while it is running.

### Next steps for A

1. **Re-run `bash a_workspace/run_check.sh`** after the `path_length` fix. Not done yet.
   Expecting 7/7. This is the cheapest outstanding item and it closes out the
   real-backend validation.
2. **Swap `NullRenderBridge` for the real OrcaLab render bridge** and confirm the freeze
   is visible in the GUI, not just in a results table. This needs the OrcaLab setup
   (stop Multipass first, it squats on port 50051).
3. **`cli.py` wiring.** Still untouched, so **the live demo does not use any of this
   yet.** That is the gap between "A's task is done" and "the demo is actually
   protected". Needs agreement with D, who owns that file.
4. Fix the two smoke-test findings in `veto/claude_vision_client.py`: downscale and
   JPEG-encode frames before sending (currently ~2.9MB per call, far too slow for a 1Hz
   gate), and fail fast on a missing API key rather than silently vetoing everything
   mid-demo.
5. Decide the veto model default. Currently `claude-sonnet-4-5`; `claude-haiku-4-5` is
   probably the better choice for a 1Hz binary gate where latency dominates.
6. `docs/PLAN.md` A item 2, low priority: accept `np.ndarray` frames as well as PIL in
   `HazardVetoAgent.assess` and `ScenarioInjector.inject`. C already worked around it.
7. `docs/PLAN.md` A item 4, tuning the force band, debounce and veto cadence, stays
   blocked on C2 landing a real loop with measurable latency.

### Status summary, honestly

**Verified against real physics:** the watchdog trips, the dog freezes, a veto blocks a
decision before any physics runs, and both land in the logbook.

**Verified only against fakes:** everything in `a_workspace/tests/`, which is where the
override-suppression behaviour is proven. The real-backend script does not exercise the
waypoint override path.

**Not verified at all:** the OrcaLab GUI freeze, a real VLM in the loop, a real
Anthropic API call, and the corrected C2 check.

**Not wired up:** `cli.py`. The demo command runs none of this today.
