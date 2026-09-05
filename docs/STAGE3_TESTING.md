# Stage 3 Testing — Veto Gate, Safety Precedence, Continuation

Live-verified against a running OrcaLab GUI (`street.json` loaded) through the
`navila-orcalab-bridge` MCP server, driven from Claude Code. Complements the automated suite
in `test_navila_bridge.py` (34/34 passing headless) — this is the first time the same logic
was exercised against the real OrcaLab pose mirror (`OrcaLabMirrorBackend`) instead of a
mocked-out edit service.

**Test setup used throughout:** `backend_kind="orcalab-mock"` (edit-service pose mirror, no
GPU/MJLab needed) + `vlm_kind="mock"` with a scripted action list, so behavior is deterministic
and the OrcaLab GUI is the thing actually being validated, not NaVILA's real inference.

## What was tested

### 1. Hazard Veto Agent gate (`navigate_step` → `HazardVetoAgent.assess`)

| Check | Expected | Result |
|---|---|---|
| Normal motion, no hazard scheduled | dog moves in GUI, `termination_reason` not `veto` | PASS |
| `navila_inject_hazard` scheduled, then `navigate_step` | `termination_reason: "veto"`, `action: "stop"`, `veto_reason` present, dog does **not** move | PASS |
| `navila_get_logbook` after a veto | a `VETO` / `hazard_veto` entry present | PASS |
| `veto=False` on `navila_start_episode`, hazard still scheduled | motion executes anyway, hazard ignored | PASS |

**Means:** the differentiator (a vision gate that can refuse a proposed motion before any
physics runs) is live end-to-end against the actual OrcaLab render/pose path, not just the
headless mock physics used by the automated tests.

### 2. `WAYPOINT_STOP_OVERRIDE` precedence flag (`stop_override_suppressed`)

| Check | Expected | Result |
|---|---|---|
| Veto ends a step | `stop_override_suppressed: true` | PASS |
| `navila_inject_force_drop` trips the watchdog | `termination_reason: "emergency_stop"`, `stop_override_suppressed: true`, dog freezes in GUI | PASS |

**Means:** the flag correctly distinguishes a real safety stop (watchdog trip or veto) from an
ordinary VLM-issued stop. It has no consumer in this codebase yet — D's actual forward-nudge
reflex lives in `NaVILA-Orca/src/navila_orca/runner.py`'s `NavigationRunner`, a separate
CLI-driven loop this bridge doesn't call (see `docs/PLAN.md`). This flag is a ready-made check
for whenever that reflex gets ported into the per-step bridge.

### 3. `navila_continue_episode` — pose continuity across prompts

| Check | Expected | Observed |
|---|---|---|
| Pose immediately after `continue_episode`, before any new `navigate_step` | unchanged from the prior leg's final pose (not spawn) — `continue_episode` itself never moves the robot | Pose A held at `x=0.5, y=0.0`, `decision_index=0`, `last_action=null` — PASS |
| Pose after running `navigate_step` for the continued leg | advances **from** the prior pose, not from spawn | pose advanced past `x=0.5` (not reset to `~0` and re-climbing) — PASS |
| `watchdog_enabled` / `veto_enabled` after continuing | unchanged — `continue_episode` never rebuilds the safety/veto stacks | PASS |
| A hazard scheduled with `navila_inject_hazard(at_step=1)`, then continued | re-fires on the continued leg's first decision, because `decision_index` resets to 0 on every `continue_episode` and hazard windows are keyed to it, not a global counter | confirmed — required an explicit `navila_clear_hazards()` before the continued leg could proceed | PASS |
| An emergency stop (`navila_inject_force_drop`), then continued | **stays latched** — `continue_episode` resets `phase` to `"running"` but never touches `backend.interrupted`, so the very next `navigate_step` immediately re-reports `termination_reason: "emergency_stop"` until `navila_clear_stop()` is called explicitly | confirmed | PASS |

**Means:** multi-turn "keep walking, follow this next instruction" sessions work as intended
— the robot's physical position genuinely carries over between prompts instead of teleporting
back to spawn each time (that's what `navila_reset_episode`/`navila_start_episode` are for
instead). Two gotchas to remember when scripting a multi-leg demo or a rehearsal sequence:
- Re-arm/clear any scheduled hazard (`navila_clear_hazards`) before continuing into a leg
  where you don't want it to refire.
- An emergency stop is **not** cleared by continuing — always `navila_clear_stop()` (and
  `navila_clear_force_drops()` if the fault was a scheduled force drop) before the next
  `navigate_step`, or it will just re-report the same stop.

## Not covered by this pass

Everything gated on C2 (`orcalab-render` real gait/frames — still MJLab-mirror-only, so the
dog glides rather than walks and the veto agent still sees the placeholder frame, not a real
camera capture), the real `AnthropicVetoVisionClient` (still the pixel-detection stub), and
the in-scene hazard trigger / scene-reset-reliability items — all still open per
`docs/PLAN.md`'s D task list.
