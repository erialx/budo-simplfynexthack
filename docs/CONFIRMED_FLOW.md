# Confirmed Flow vs. the Original 4-Agent Plan

This documents the actual, live-traced request/response flow through the per-step MCP
bridge ("Loop A" below) as confirmed in this session (2026-09-05), and maps it against
CLAUDE.md's original "four agents, three speeds" architecture. It also calls out where
what's built diverges from or extends that original plan, and gives manual test cases
for a human to run by hand — prompting through a Claude Code session with the
`navila-orcalab` MCP server connected, and checking the result by eye in the OrcaLab
GUI, not by reading tool output alone.

## Two loops exist. This document is about Loop A.

- **Loop A — the per-step MCP bridge** (`navila_bridge.py` + `bridge_backends.py`).
  Claude Code calls one MCP tool per decision (`navila_navigate_step`), reads back a
  JSON result, and decides whether to continue. This is the loop CLAUDE.md's
  architecture diagram actually describes — Claude Code via MCP as the Orchestrator.
- **Loop B — D's CLI demo loop** (`navila-orca run ...` → `runner.py`'s
  `NavigationRunner`/`SafeNavigationRunner`). A standalone process that runs one
  instruction (or a waypoint file) to completion autonomously. **No MCP or Claude
  orchestration in this loop at all** — Claude's only possible appearance is the
  Hazard Veto Agent's vision call, if `--veto-client anthropic` is passed.

Historically Loop B had real articulated gait (`OrcaLabRenderBridge`, full qpos push)
and Loop A did not (`OrcaLabMirrorBackend`, root-pose only — the dog glides). That gap
is why Loop A was never used as "the demo." **As of the `daphne-demo-ready` merge this
session, Loop A also has a real-gait option** (`backend_kind="orcalab-render"`, wrapping
the same `OrcaLabRenderBridge` full-qpos push Loop B uses) — see Test Case 5 below. This
is newly merged and **not yet live-verified** — that's the most important open item this
document exists to close out.

## The confirmed flow, one decision

Traced live against a running OrcaLab GUI this session (`backend_kind="orcalab-mock"`,
watchdog + veto both on):

1. **Claude (MCP) → the bridge.** `navila_start_episode(instruction, backend_kind=...)`,
   then `navila_navigate_step()`. The prompt goes to the bridge process, not to OrcaLab
   or NaVILA directly. OrcaLab never sees or interprets the instruction — it only
   understands "move this actor" / "give me a camera frame."
2. **Bridge → OrcaLab: pull a camera frame**, only if real capture is enabled
   (`NAVILA_BRIDGE_ORCA_CAMERA=1` + a `mujococamera1080` actor present in the scene).
   Otherwise the bridge fabricates an 8x8 black placeholder frame itself — no OrcaLab
   round-trip.
3. **Bridge → NaVILA: frame(s) + instruction.** NaVILA is the only thing that "decides
   what to do" in this whole cycle — it returns one of 7 discrete action phrases.
4. **Bridge parses that text into a velocity command**, then gates it before any motion:
   - **Safety Watchdog** (`SafetyWatchdog.tick()`, no LLM) checks harness force. An
     out-of-band reading for `debounce_ticks` consecutive ticks trips
     `backend.emergency_stop()` directly.
   - **Hazard Veto Agent** gets that same frame + the proposed action text and can VETO
     it. This is the only point in the entire loop where a Claude vision call can
     happen — and today, in Loop A, it's always the free `_RedBarStubVetoClient`
     (pixel-color detection). `navila_start_episode`'s `veto_client_kind` param only
     accepts `"stub"` right now (see Known Gaps).
5. **If clear, bridge → physics**, then **bridge → OrcaLab: push the result** — root
   transform only for `orcalab`/`orcalab-mock`, full articulated `qpos` for
   `orcalab-render`.
6. **Bridge → Claude: a JSON result** (action, new pose, `done`/`termination_reason`) —
   never the frame itself.
7. Claude reads that result and decides whether to call `navigate_step()` again or issue
   a new instruction via `navila_continue_episode()`. This is the only place Claude is
   actually "deciding" anything, and it's "continue or not," never "what should the dog
   do physically."

## Mapping against CLAUDE.md's four agents

| # | Agent (CLAUDE.md) | Planned role | Confirmed this session | Where in the flow |
|---|---|---|---|---|
| 1 | **Orchestrator** — Claude Code via MCP | Plans the route, issues one step, waits for a result | ✅ Exactly as planned. Claude never receives a frame, never picks the action — only an instruction in, a JSON summary out. | Steps 1, 6, 7 |
| 2 | **Driver** — NaVILA VLM + translator | Vision → 1 of 7 actions → `Move(vx,vy,vyaw)` | ✅ Confirmed with `vlm_kind="mock"` (scripted). **Not confirmed with the real NaVILA VLM** — `vlm_kind="tcp"` needs the AWS SSM tunnel to `127.0.0.1:54321`, confirmed DOWN on this box this session (`check_navvlm_endpoint.py` → connection refused). | Steps 2-3 |
| 3 | **Safety Watchdog** | Reactive ~20Hz, zero LLM calls, e-stops directly | ✅ Confirmed live: `navila_inject_force_drop` → watchdog trips → `termination_reason="emergency_stop"` → dog freezes in the GUI → `navila_clear_force_drops` + `navila_clear_stop` → resumes without teleporting. | Step 4 |
| 4 | **Hazard Veto Agent** (the differentiator) | Tactical ~1Hz, one Claude vision call, VETO/CLEAR + reason | ✅ Wiring confirmed live: `navila_inject_hazard` → veto → `termination_reason="veto"`, zero physics, `stop_override_suppressed=true`. ✅ Real Claude vision (`AnthropicVetoVisionClient`, `claude-haiku-4-5`) is now wired into **both** loops — Loop A via `navila_start_episode(veto_client_kind="anthropic")`, Loop B via `cli.py --veto-client anthropic`. A construction failure (missing package/key) degrades to `veto_enabled: false` + a `veto_client_error` string instead of crashing the episode — **not yet exercised with a real API call/key** (see Known Gaps). | Step 4 |

## Known gaps as of this document

- **Real Anthropic veto client wired into Loop A, but never actually called.**
  `navila_start_episode(veto_client_kind="anthropic")` is now available and confirmed to
  degrade cleanly (episode still starts, `veto_enabled: false` + a readable
  `veto_client_error`) when the `anthropic` package or a key isn't available — verified
  on this dev box, where `anthropic` isn't installed. **No real API call has been made
  from either loop yet** — that first real, money-spending call is still untested. Do
  that deliberately (see the note after TC4), not by accident.
- **`orcalab-render` (real gait in Loop A) is merged but not live-verified.** Unit-tested
  against a fake inner backend (`test_bridge_backends_orcalab_render.py`, 4/4 pass) but
  never run against the actual OrcaLab GUI. Test Case 5 below closes this out.
- **Real camera capture needs one setup step per scene load.** `mujococamera1080` isn't
  baked into `street.json`. Spawn it once after each fresh scene load — `navila_spawn_camera()`
  (2026-09-06, live-verified) is the one-call way; else `capture_frame()` silently falls
  back to the 8×8 placeholder. `prefabs/mujococamera1080` itself is a built-in OrcaLab
  prefab (nothing to obtain).
- **Real NaVILA VLM untested this session** — the AWS tunnel to the inference endpoint
  was down. All flow confirmation above used the deterministic mock VLM.

## Watching Loop A live (judge-facing)

Loop A's stdout **is** the MCP stdio transport, so the bridge can't just `print()` a
running status. Two observability seams exist instead:

- **`navila_get_live_status(since_seq=, max_lines=)`** — a text feed the Orchestrator
  polls between `navila_navigate_step` calls and reads out to the audience. It carries a
  per-decision trace (instruction in → ego frame described → NaVILA's action → the exact
  velocity command → distance moved), a loud `[VETO: <reason>]` / `[EMERGENCY STOP:
  <reason>]` banner the instant the veto agent or watchdog fires (also surfaced as
  `active_alert`), a `Status: CLEAR - Navigating` heartbeat ~every 3s while nothing is
  wrong, and `logbook_tail` (the last few `DecisionLogbook` entries). **No extra model
  calls** — it only formats data already flowing through the loop. Every line is also
  mirrored to the MCP server's stderr. Pass the previous response's `next_seq` back as
  `since_seq` for just what's new.
- **The OpenCV ego-view window** (`LiveNavigationMonitor`, same view as
  `navila-orca run --live-monitor`). Opens **automatically** on `navila_start_episode`
  when GUI `opencv-python` (not `-headless`) and a `DISPLAY` are present in the MCP
  server's interpreter — the server runs under the **`orcalab`** conda env (its
  `claude mcp` registration, *not* the `orcalab-phys` one used only for
  `navila_run_instruction`'s shell-out). It runs on **its own thread** with a continuous
  pump loop, so it stays responsive between MCP tool calls (an un-pumped window makes the
  desktop show a "not responding / Force Quit or Wait" dialog) and is **session-scoped** —
  reused across episodes, not recreated. `live_monitor=false` closes it;
  `live_monitor=true` forces it (a failure then warns loudly). If a dependency is missing
  the episode still runs and the response's `live_monitor` field says why.
  - **`navila_live_monitor_selftest()`** — run this if no window appears. Opens the
    window immediately with a synthetic frame and returns a full diagnosis (cv2 version
    and whether it's the GUI or headless build, `DISPLAY`/`WAYLAND_DISPLAY`, which Python
    runs the server, and on failure the exception + a hint).
  - **Real dog's-eye frame** instead of the 8×8 black placeholder: `camera` is
    **auto-enabled** when `live_monitor` is on and `backend_kind` is
    `orcalab-mock`/`orcalab` (pass `camera=false` to opt out). It additionally needs the
    OrcaLab GUI + edit service (:50151) up **and a `mujococamera1080` actor in the loaded
    scene**. That actor is **not** in `street.json` and there is nothing to download —
    `prefabs/mujococamera1080` is a built-in OrcaLab prefab. Get it into the scene one of
    three ways: call **`navila_spawn_camera()`** (adds it via the edit service; live-only,
    rerun per scene load), add the `prefabs/mujococamera1080` prefab in the OrcaLab GUI,
    or run `NaVILA-Orca/scripts/run_orcalab_camera_smoke.sh` once (it creates the actor,
    `--no-publish`, then exits). Without the actor, capture fails silently and the panel
    shows the placeholder — that black square is the symptom of "no camera actor," not a
    bug. The camera **follows the dog**: `capture_frame()` pushes the camera actor to
    `robot pose ⊕ mount offset` (`compose_camera_pose`, yaw-stabilised) just before each
    capture, so the ego view tracks the robot as it moves (`NAVILA_BRIDGE_ORCA_CAMERA_
    FOLLOW=0` / `..._STABILIZE=0` to tune). **Live-verified 2026-09-06**: `spawn_camera_
    actor()` added the actor to a running OrcaLab, `capture_frame()` returned a real
    1080×1080 render, and the frame changed as the dog drove forward.
  - This window is **not** part of the OrcaLab GUI. **Restart the MCP server** after any
    `navila_bridge.py` change so it reloads.

## Manual test cases

Run each of these by prompting a Claude Code session with the `navila-orcalab` MCP
server connected (`claude mcp list` should show it `Connected`). For each case: give
Claude the natural-language prompt, confirm it called the tool shown, and **check the
result yourself in the OrcaLab GUI** — a passing tool response is not sufficient on its
own for any case marked "GUI check required."

Prerequisite for every case below: the OrcaLab GUI is open with a scene loaded, and its
edit service is reachable (`netstat -ano | findstr 50151` / `ss -tln | grep 50151`
should show something listening).

### Before you run ANY test case: live monitor + status feed

These apply to every TC below — set them up once, then watch them on every run.

1. **Restart the `navila-orcalab` MCP server** if `navila_bridge.py` changed since it
   started (it loads the file once). The server runs under the **`orcalab`** conda env.
2. **One-time per scene load — add the ego camera:** call **`navila_spawn_camera()`**
   (or run `bridge_backends.spawn_camera_actor()`, or drop the `prefabs/mujococamera1080`
   prefab in the OrcaLab GUI). `prefabs/mujococamera1080` is a built-in OrcaLab prefab —
   nothing to download; the add is live-only, so redo it after every fresh scene load.
   Skip this and the ego panel just shows an 8×8 black placeholder.
3. **The OpenCV "guide dog — live monitor" window MUST appear on every
   `navila_start_episode`** and update as the dog moves. It opens automatically
   (`live_monitor` defaults on, own thread so it never freezes, session-scoped so it
   persists across episodes). With `backend_kind` `orcalab-mock`/`orcalab`, `camera`
   auto-enables and the ego view **follows the dog**. **If the window does not appear,
   treat that as a failure**: run **`navila_live_monitor_selftest()`** (returns cv2
   build, `DISPLAY`, server python, and the exact reason) before continuing. `camera=false`
   / `live_monitor=false` opt out.
4. **Narrate with the status feed:** poll **`navila_get_live_status(since_seq=)`** between
   `navila_navigate_step` calls and read `new_lines` to the audience — per-decision trace,
   loud `[VETO: …]` / `[EMERGENCY STOP: …]` banners the instant they fire, a
   `Status: CLEAR - Navigating` heartbeat, and `logbook_tail`. Pass the previous response's
   `next_seq` back as `since_seq` for just what's new. No extra model calls. Full detail:
   "Watching Loop A live" above.

---

### TC1 — Orchestrator: instruction in, structured result out, never a frame

**Prompt:** "Start a navila episode with instruction 'walk forward down the street',
backend orcalab-mock, and a scripted VLM script of 'move forward by 75 cm; move forward
by 75 cm; stop', then take one navigation step."

**Expect Claude to call:** `navila_start_episode(...)` then `navila_navigate_step()`.

**Check:**
- [ ] The tool response is a JSON object with `action`, `pose`, `done` — no image data,
  no base64 blob, nothing resembling pixel content.
- [ ] **GUI check:** the dog actor visibly moved forward between before/after.
- [ ] The "guide dog — live monitor" window opened on `navila_start_episode` and its
  panels (ego frame, instruction, NaVILA output, command) updated on the step. If the
  `mujococamera1080` actor was spawned, the ego frame is a real render that tracks the
  dog, not the 8×8 placeholder. (Absent window → `navila_live_monitor_selftest()`.)
- [ ] `navila_get_live_status()` returns the decision trace + a `Status: CLEAR -
  Navigating` line for this run.

**Pass:** both boxes checked.

---

### TC2 — Driver: NaVILA's decision actually drives the translated motion

**Prompt:** "Continue that episode and take another navigation step. Show me the raw
VLM text and the executed velocity command."

**Expect Claude to call:** `navila_navigate_step()`.

**Check:**
- [ ] `raw_vlm_text`/`action` in the response matches the next scripted phrase
  ("move forward by 75 cm" the second time, "stop" the third).
- [ ] `command.vx`/`duration_s` are non-zero for a move action, all-zero for stop.
- [ ] **GUI check:** the dog stops moving on the "stop" step.

**Note:** this only confirms the parse→translate plumbing with the deterministic mock
VLM. Confirming NaVILA's own reasoning (different scenes/instructions producing
different actions) needs `vlm_kind="tcp"` against the real endpoint — currently blocked,
see Known Gaps.

**Pass:** both boxes checked.

---

### TC3 — Safety Watchdog: reactive e-stop, independent of the VLM

**Prompt:** "Inject a force drop on the current episode starting a few ticks from now,
then keep taking navigation steps until it trips. Then clear the force drop and the
stop, and take one more step."

**Expect Claude to call:** `navila_inject_force_drop(at_step=...)`, then
`navila_navigate_step()` (repeated), then `navila_clear_force_drops()` +
`navila_clear_stop()`, then `navila_navigate_step()` again.

**Check:**
- [ ] A step's response shows `termination_reason: "emergency_stop"`.
- [ ] **GUI check:** the dog visibly freezes in place — not just stops accepting new
  commands, its position stops changing entirely.
- [ ] After clearing, the next step's `termination_reason` is NOT `"emergency_stop"` and
  the dog moves again from the SAME position it froze at (not teleported).

**Pass:** all three boxes checked.

---

### TC4 — Hazard Veto Agent: blocks the action before any physics runs

**Prompt:** "Schedule a hazard on the next decision of this episode, then take a
navigation step. Show me the veto reason if there is one."

**Expect Claude to call:** `navila_inject_hazard(at_step=...)`, then
`navila_navigate_step()`.

**Check:**
- [ ] Response shows `termination_reason: "veto"`, a non-empty `veto_reason`, and
  `stop_override_suppressed: true`.
- [ ] `pose` in the response is IDENTICAL before and after this step — confirms zero
  physics executed, not just a fast stop.
- [ ] **GUI check:** the dog does not move at all on this step.
- [ ] `navila_get_logbook` shows a `VETO (hazard_veto)` entry.

**Pass:** all four boxes checked. **Reminder:** this exercises the free stub client
only, not real Claude vision — treat this as confirming the gate mechanism, not the
vision quality. For the real thing, see TC9, which costs money — don't run it by
accident.

---

### TC5 — Real gait via `orcalab-render` (NEW — the important one)

**Prompt:** "Start a fresh navila episode with backend_kind orcalab-render, instruction
'walk forward', and a scripted VLM script of 'move forward by 75 cm; move forward by 75
cm; stop'. Take steps until it's done."

**Expect Claude to call:** `navila_start_episode(backend_kind="orcalab-render", ...)`,
then `navila_navigate_step()` repeatedly.

**Check:**
- [ ] `navila_start_episode` returns `ok: true` (a failure here likely means
  `OrcaLabRenderBackend` couldn't reach OrcaGym on `:50051` or the edit service on
  `:50151` — check both ports, and that Multipass isn't squatting on `:50051`, see
  CLAUDE.md's "Windows-specific" port-collision note).
- [ ] **GUI check — this is the actual point of this test:** the dog's **legs visibly
  articulate** (a real walking gait), not a rigid glide. Compare against `orcalab-mock`
  behavior (TC1) if you want a side-by-side.
- [ ] Steps complete without crashing; final `termination_reason` is `"stop"`.

**Pass:** all three boxes checked. **If this fails or the legs still don't move,** that's
a real, actionable finding — report back with the exact error/response rather than
retrying blind.

---

### TC6 — In-scene hazard trigger (visibly real, not a frame overlay)

**Prompt:** "Trigger a scene hazard: move blue_hatchback_car_1 to be right next to the
dog's current position."

**Expect Claude to call:** `navila_trigger_scene_hazard(actor_name="blue_hatchback_car_1", x=..., y=..., z=0)`.

**Check:**
- [ ] Response is `ok: true`.
- [ ] **GUI check:** the car actor visibly jumped to the new position.

**Pass:** both boxes checked.

---

### TC7 — Scene reset reliability

**Prompt:** "Reset the scene layout for just blue_hatchback_car_1 back to its authored
position."

**Expect Claude to call:** `navila_reset_scene_layout(actor_names="blue_hatchback_car_1")`.

**Check:**
- [ ] Response is `ok: true` and `restored_actors` includes `blue_hatchback_car_1`.
- [ ] **GUI check:** the car visibly snapped back to where it was before TC6.

**Pass:** both boxes checked.

---

### TC8 — `continue_episode`: pose carries over, doesn't teleport

**Prompt:** "Give the current episode a new instruction, 'keep walking', without
resetting it, then take one more step."

**Expect Claude to call:** `navila_continue_episode("keep walking")`, then
`navila_navigate_step()`.

**Check:**
- [ ] `navila_continue_episode`'s response pose matches wherever the dog last was —
  NOT reset to spawn.
- [ ] `decision_index` in that response is `0` (resets per instruction).
- [ ] **GUI check:** the dog does NOT jump back to its starting position when this tool
  is called.

**Pass:** all three boxes checked.

---

### TC9 — Real Claude vision veto (⚠️ costs money — first real API call from either loop)

This is the first time either loop would make an actual Anthropic API call. Confirm you
mean to spend it before running this.

**Prompt:** "Start a fresh navila episode, backend orcalab-mock, veto_client_kind
anthropic, with a scripted VLM script that would move the dog forward. Inject a hazard
on the first decision, then take one step."

**Expect Claude to call:** `navila_start_episode(veto_client_kind="anthropic", ...)`,
`navila_inject_hazard(at_step=1)`, `navila_navigate_step()`.

**Check:**
- [ ] `navila_start_episode`'s response shows `veto_enabled: true` and
  `veto_client_error: null` — if `veto_enabled` is `false`, check `veto_client_error`
  for why (missing `ANTHROPIC_API_KEY`, or `anthropic` not installed — run
  `pip install -e "NaVILA-Orca[veto]"` first) before assuming the step itself failed.
- [ ] The step's `termination_reason` is `"veto"`, with a `veto_reason` that reads like
  an actual sentence about what's in the frame (not the stub's fixed
  "injected hazard marker..." text) — confirms this was a real model call, not the stub.
- [ ] **GUI check:** same as TC4 — the dog does not move at all.

**Pass:** all three boxes checked, and you're satisfied with what it cost.

---

## Recording results

There's no automated pass/fail file for this set — these are manual, GUI-verified
checks. When you run these, note the date and which backend/scene you tested against
(scene content affects TC5/TC6/TC7 especially), and update this file's Known Gaps
section if a case reveals something new, the same way `docs/STAGE3_TESTING.md` did for
the previous round of live verification.
