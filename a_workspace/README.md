# a_workspace — A's in-progress work

This folder is scratch space for **A's** current task in `docs/PLAN.md`
("`NavigationRunner` safety-gap / precedence porting"). It is deliberately kept
separate from `NaVILA-Orca/src/` and everyone else's files so it's obvious
what's mine and in-review versus what's already landed and owned by someone
else. Nothing in this folder touches, imports-and-monkeypatches, or requires
edits to any file another teammate owns (`runner.py`, `navila_bridge.py`,
`bridge_backends.py`, `cli.py`, `mjlab_go2.py`, `traffic_crossing.py`, or their
tests) — see `safety_integration.py`'s module docstring for exactly how.

## What's here

- `safety_integration.py` — `SafeVelocityPhysicsBackend`, `SafeVLMClient`,
  `SafeNavigationRunner`, and `build_safe_runner()`. Together they wire A's
  `SafetyWatchdog` + `HazardVetoAgent` into `NavigationRunner` (D's live-demo
  CLI loop, `runner.py`) purely by wrapping the three dependencies its own
  constructor already takes as pluggable — no patch to `runner.py` itself.
- `tests/test_safety_integration.py` — 4 tests, all passing, that run the
  **real** `NavigationRunner`, `SafetyWatchdog`, and `HazardVetoAgent` end to
  end (only the physics backend, renderer, and driver VLM are faked, matching
  how `runner.py`'s own constructor treats those three as pluggable). Covers:
  normal operation is unaffected, a watchdog trip freezes the robot mid-chunk
  and ends the run as `terminated`, a veto stops a decision before any physics
  runs, and — the actual gap this closes — a veto'd stop does **not** get
  overridden by `WAYPOINT_STOP_OVERRIDE` while an ordinary premature VLM stop
  still gets nudged forward exactly as D designed it.

## Run the tests

From the repo root:

```
PYTHONPATH=NaVILA-Orca/src python3 -m pytest a_workspace/tests/ -q
```

(`a_workspace/tests/test_safety_integration.py` adds `a_workspace/` itself to
`sys.path` so `import safety_integration` resolves regardless of cwd.)

## Status / what's left before this can actually land

1. **Design proven, not yet wired into the real demo.** The four tests use
   fakes for the physics backend, renderer, and VLM client — the pattern is
   verified against the real safety/veto/runner code, but nobody has run
   `build_safe_runner()` against a real `MjlabGo2Backend` + OrcaLab renderer +
   real/mock VLM yet. That's the natural next step.
2. **`cli.py` isn't touched.** D owns the actual assembly of `NavigationRunner`
   from CLI flags (`cli.py::_make_renderer` / `_run`, per `docs/PLAN.md`'s C2
   item). Swapping `NavigationRunner(...)` for
   `build_safe_runner(...)` there is a small, mechanical change once this is
   reviewed — deliberately left for whoever owns that file, or for me to do
   as a follow-up once this is agreed on, rather than editing it unasked.
3. **Scope note:** `SafeVelocityPhysicsBackend` only covers the
   velocity-facade path (`VelocityPhysicsBackend`, what `MjlabGo2Backend`
   actually implements) — the project doesn't use the joint-action facade
   today, so that path is out of scope here.
4. **Tuning is separately blocked** (see `docs/PLAN.md`'s A item 4) on C2
   landing a real end-to-end loop with measured latency — nothing to do here
   until then.

## Design notes (why composition, not a patch)

`NavigationRunner.__init__` already accepts `physics`, `vlm_client`, and
`premature_stop_recovery_command` as plain constructor arguments — that's the
seam its own author built for exactly this kind of substitution. Wrapping
those three reproduces the same behaviour C already verified in
`navila_bridge.py`'s `navigate_step` (see `docs/STAGE3_TESTING.md`): a
watchdog trip or a veto both end the run as a real, non-overridable stop.

- `SafeVelocityPhysicsBackend` ticks the watchdog on every physics tick and,
  once tripped, freezes the robot (same position, `terminated=True`) instead
  of advancing — `runner.py`'s own tick loop already breaks and reports
  `termination_reason="terminated"` on that flag; this doesn't add a new code
  path, it makes an existing one fire for the right reason.
- `SafeVLMClient` runs the Hazard Veto Agent once per decision, before
  `runner.py`'s own `action_parser` ever sees the text — a VETO just looks
  like the VLM said `"stop."`.
- `SafeNavigationRunner` overrides exactly one thing:
  `premature_stop_recovery_command` becomes a property that reads back `None`
  when this decision's stop came from a trip or a veto, so `runner.py`'s own
  (unmodified) `WAYPOINT_STOP_OVERRIDE` logic sees "no recovery command
  configured" and waits for the next decision instead of forcing forward
  motion over a real safety stop. Every other line of `run()` is inherited,
  untouched.
