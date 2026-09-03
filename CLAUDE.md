# NaVILA-Orca Guide Dog — Project Context for Claude Code

Hackathon project, due in 1 week. Goal: a working end-to-end simulated demo of a guide-dog
agentic system, NOT real hardware integration. Read this before doing anything in this repo.

## What we're building

A closed-loop agentic guide dog: NaVILA drives a Go2 (in OrcaLab sim), Claude orchestrates
navigation via MCP, a reactive safety layer watches harness force, and a vision-based "veto"
agent gives the dog intelligent disobedience — the ability to refuse an unsafe instruction
(e.g. red pedestrian signal, person crossing). That veto agent is our differentiator; protect
time for it.

## Environment

- Conda env: `orcalab`. On Windows this is a **prefix** env at `<repo>/.conda/envs/orcalab`,
  not a named env — `conda activate` by name will not find it. Call its interpreter directly:
  `.conda/envs/orcalab/python.exe` (or `python` on the platform this instance is on). Always
  resolve and use that interpreter explicitly rather than relying on `conda activate` having
  been run first. A past bug happened from running `claude mcp add` with `(base)` active from
  the wrong directory — same class of mistake, verify the interpreter path before running
  anything.
- **If you're driving this repo through the Cowork desktop bridge (`device_bash`), note that
  it runs inside an isolated Linux VM on the user's machine even though the machine itself is
  Windows.** `.conda/envs/orcalab/python.exe` is a Windows PE binary and cannot be exec'd from
  that VM (`cannot execute binary file: Exec format error`) — there is no way around this from
  in there. For pure-Python modules with no OrcaLab/torch/mjlab dependency (currently:
  `robot_backend`, `safety_watchdog`, `veto`, `decision_logbook` — see "A's components"
  below), the VM's own `python3` plus `PYTHONPATH=src` is enough (they only need numpy and
  PIL, both present). Anything that touches OrcaLab, MJLab, or the real NaVILA VLM socket has
  to be run on the actual Windows machine, not through this bridge.
- Codebase root: this is a fork of `openverse-orca/Orca_VLN`. The public repo's
  `NaVILA-Bench` targets Isaac Lab and is NOT a reliable source of truth for this fork's wire
  protocols — use only cautiously, prefer the actual source files here.
- Node.js v20+ required for `mcp dev` (v18 fails).
- `claude mcp add` scope is tied to the working directory it's run from. Always run it from
  the repo root, with the `orcalab` env's interpreter resolved. Correct syntax for a local
  stdio server: `claude mcp add <name> -- /full/path/to/python /full/path/to/server.py`
- **Platform note**: this fork's bash launcher scripts (`scripts/setup_orcalab_env.sh`,
  `scripts/run_orcalab_scene_locomotion.sh`, etc.) are Linux-only — they patch native `.so`
  files with `patchelf` and check XCB/GLVND bindings that don't exist on Windows. A native
  Windows OrcaLab install plus a directly-invoked `python -m navila_orca.cli run …` (bypassing
  the bash wrapper) is a verified-working path on Windows; see "Windows-specific" below.

## Architecture — four agents, three speeds

1. **Orchestrator (Claude Code via MCP)** — deliberative, slowest. Plans the route, issues one
   navigation step at a time, waits for a result before issuing the next. Never sits in the
   fast safety loop.
2. **Driver** — NaVILA VLM (vision → one of 7 discrete actions) + a translator that maps that
   action to `Move(vx, vy, vyaw)`. Use `Move()` only — not `TrajectoryFollow` — for clean,
   immediate interrupt semantics. Model this against the `unitree_sdk2_python` API shape even
   while backed by a mock, so a real-hardware swap later is a config change, not a rewrite.
3. **Safety Watchdog** — reactive, ~20Hz, **zero LLM calls in this loop**. Reads harness force
   (mocked this week), debounces over a small window, calls `emergency_stop()` directly if out
   of the safe band. Must be able to preempt the Driver without going through the Orchestrator.
   **Built — see "A's components" below.**
4. **Hazard Veto Agent** — tactical, ~1Hz. One Claude vision call per step: "given this frame,
   is the proposed action unsafe right now?" → VETO/CLEAR + one-sentence reason. Gates whether
   the Driver's next `Move()` goes out. **Built — see "A's components" below.**

Keep these speeds separate. The reactive layer must stay dumb and fast (threshold + debounce,
nothing else) — that's what makes it actually independent of whatever the LLM layers are doing.

## A's components (built, tested, ready to integrate)

Everything below is self-contained (no OrcaLab dependency), lives under
`NaVILA-Orca/src/navila_orca/`, and has a passing pytest suite under `NaVILA-Orca/tests/`.
Run them with `cd NaVILA-Orca && PYTHONPATH=src python3 -m pytest tests/ -q` — 59/59 passing
as of this update. This is what C's Stage 3 ("Integrate the Veto Agent and Safety Watchdog
into the loop") wires into the per-step MCP tools.

- **`robot_backend/`** — `RobotBackend` Protocol shaped after `unitree_sdk2_python`
  (`move(vx, vy, vyaw)`, `emergency_stop()`, `read_harness_force()`, `get_pose()`), plus
  `MockBackend` (a standalone unicycle-kinematics implementation, no sim dependency) and
  `MockForceSensor` (schedulable "force drops to zero" events, step-indexed). Calling `move()`
  after `emergency_stop()` raises `EmergencyStopActive` — it latches, on purpose, until
  `reset()`. `MockBackend.reset()` and a `SafetyWatchdog`'s own `reset()` are intentionally
  decoupled (see next item) — don't accidentally couple them when wiring the real loop.
- **`safety_watchdog.py`** — `SafetyWatchdog`: call `tick()` at ~20Hz, it debounces over
  `debounce_ticks` (default 3) consecutive out-of-band harness-force readings before calling
  `backend.emergency_stop()` directly. Latches after tripping (won't re-call `emergency_stop()`
  every tick). `watchdog.reset()` clears only the watchdog's own state, never the backend's —
  clearing the robot's e-stop has to stay a separate, deliberate call to `backend.reset()`.
- **`veto/`** — `HazardVetoAgent.assess(frame, instruction, proposed_action)` runs one vision
  call via any `VetoVisionClient`, then strictly parses the response into VETO/CLEAR + reason
  (`parse_veto_response`, expects exactly `"CLEAR"` / `"CLEAR: <reason>"` / `"VETO: <reason>"`).
  A parse failure or a client exception **defaults to VETO**, never crashes, never silently
  defaults to CLEAR. `.gate(decision, move)` only calls `move` on CLEAR.
  `ScenarioInjector` is the disclosed automated-test fault injector — composites an obvious red
  bar + label onto a frame during scheduled step windows (never mutates the input frame); this
  is separate from the live-demo trigger (D's job — an in-scene OrcaLab object or a physical
  prop, "visibly real," not a frame overlay).
  `claude_vision_client.py` has `AnthropicVetoVisionClient`, the real Claude-backed
  implementation — the `anthropic` import is deferred into `__init__` and it accepts an
  injected `client=`, so the rest of the package works with no SDK installed and no API key.
  **`anthropic` is not yet a dependency in `pyproject.toml`** — add it (`pip install
  anthropic`) before wiring this into the live loop.
- **`decision_logbook.py`** — `DecisionLogbook` merges the watchdog's and veto agent's own
  `.log` into one timestamped, human-readable stream. Wire it by passing its methods as the
  callbacks those two classes already accept:
  `SafetyWatchdog(backend, on_trip=logbook.record_watchdog_trip)` and
  `HazardVetoAgent(client, on_decision=logbook.record_veto_decision)`. Routine CLEAR decisions
  are *not* logged by default (would flood the log at ~1Hz) — pass `log_clear=True` if you
  want them too. This is the direct answer to "how do you know it's making good decisions" in
  Q&A: `logbook.dump()`.

### Three "backend" seams — don't conflate them

Stage 2/3 integration has to reconcile three objects loosely called "backend":

1. `navila_orca.backends.MjlabGo2Backend` / `bridge_backends.StepBackend` — physics:
   `set_velocity_command(VelocityCommand)` + `step()` + `control_dt`. What
   `navila_navigate_step` drives today.
2. `navila_orca.robot_backend.RobotBackend` (A's) — the Driver/Watchdog seam:
   `move(vx,vy,vyaw)` continuous + `emergency_stop()` + `read_harness_force()` + `get_pose()`,
   shaped after `unitree_sdk2_python`.
3. The real OrcaLab render path (`--render-backend orcalab`), driven from the CLI.

The adapter is small: `SafetyWatchdog` takes a `force_reader=` callable and only needs
`emergency_stop()` on the backend — and `bridge_backends.MockBackend` already has
`emergency_stop()` + an `interrupted` flag that `navila_navigate_step` already checks. So give
the per-step session a `MockForceSensor` and construct
`SafetyWatchdog(session.backend, force_reader=sensor.read, on_trip=logbook.record_watchdog_trip)`.
Don't build a `RobotBackend`↔`StepBackend` bridge unless the real hardware path actually needs it.

## Per-step bridge (C) — status

`navila_bridge.py` + `bridge_backends.py`, all on `main`. Full task board in `docs/PLAN.md`.

- **Stage 1 — done.** `navila_start_episode` / `navila_navigate_step` / `navila_get_status` /
  `navila_emergency_stop` / `navila_reset_episode` / `navila_continue_episode`, over the
  `StepBackend` / `StepVLM` seams. Backends: `mock` (planar), `mjlab` (real MJWarp).
- **Stage 2 — done.** A's `SafetyWatchdog` + `MockForceSensor` + `DecisionLogbook` wired in
  (`_build_safety_stack`): `watchdog.tick()` runs once per physics tick inside
  `navila_navigate_step`; a trip calls `backend.emergency_stop()` and the step ends
  `termination_reason="emergency_stop"`. New tools `navila_inject_force_drop`,
  `navila_clear_force_drops`, `navila_clear_stop`, `navila_get_logbook`; `start_episode` gained
  `watchdog` / `watchdog_debounce_ticks` / `force_low` / `force_high`. `MjlabGo2Backend` gained
  `interrupted` + `emergency_stop()` so the watchdog attaches to it too. Verified headless;
  23/23 `test_navila_bridge.py` pass.
- **C1 — done.** `bridge_backends.OrcaLabMirrorBackend` wraps an inner backend and pushes the
  robot's **root pose** into the running OrcaLab scene via `set_actor_transform_batch` after
  every step. New backend kinds `orcalab` (mjlab inner) / `orcalab-mock` (mock inner, no GPU);
  env `NAVILA_BRIDGE_ORCA_{EDIT_ADDRESS,ROBOT_ACTOR,INNER}`. Owns one event-loop thread (grpc.aio
  channels bind to their creating loop); degrades to headless + one stderr line if the edit
  service is unreachable. **Verified live** against a running OrcaLab — dog glides on
  `navila_navigate_step`, freezes on a watchdog trip, `navila_clear_*` resumes from the freeze
  point.
  - **Limitation:** root-transform only → the dog *glides*, legs don't articulate. Real gait +
    ego-camera frames need `OrcaLabRenderBridge` (full qpos push via OrcaGym `UpdateLocalEnv`) —
    that's **C2**, owned by D (see `docs/PLAN.md`).
  - `at_step` for `navila_inject_force_drop` counts *physics ticks*; one `navila_navigate_step`
    burns 50–150, so use `at_step` ≈ 80–150 for a visible "walks then freezes" demo.

## D's components / handover

D handed over `handover/` (3 files) plus context; D's actual working NaVILA-Orca fork is
**not in this repo** and can't be pushed to origin — get it as a `git bundle` / zip.

- **`handover/D_street.json`** — the OrcaLab authored demo scene (v3.0, 44 actors), loads on
  the OrcaLab side, not by Python. Already contains the full hazard cast: `traffic_light_1..4`,
  `blue_hatchback_car_1`, `range_rover_suv_1`, `young_male_character_1/2`,
  `female_pedestrian_model_1..4`, `supine_human_model_1` (person lying in the path),
  `blue_flammable_liquid_drums_1/2`, `soccer_ball_1/2`, with `standard_cardboard_box_1` as the
  navigation target and `striped_anti_slip_mat_1/2` as the zebra crossing. Canonical demo
  scene — most Stage-3 hazards just need staging + camera movement, not runtime spawning.
- **`handover/D_traffic_crossing.py`** — belongs at `src/navila_orca/traffic_crossing.py`
  (imports `from .contracts import VelocityCommand`). `traffic_light_crossing_waypoints()`
  returns a 3-tuple of staged VLM instructions (wait / center / exit), each repeating a 2 m
  vehicle-clearance invariant; `premature_stop_recovery_command()` returns one forward nudge
  (`vx=0.5, 0.5s`). This is only the command builder — the runner call-site is in D's
  un-pushed `runner.py`.
- **D's fork has CLI features this repo's `cli.py` does NOT have**: `--realtime-visual-sync`
  (physics/renderer frame-lock, fixes "ghost dog"), `--rehearsal` (the "READY FOR DEMO"
  prompt), `--traffic-light-crossing` + `--traffic-wait-waypoint` / `--traffic-center-waypoint`
  / `--traffic-exit-waypoint`. The "verified working" command in "Windows-specific" plus these
  flags is D's demo command — **it will not run against this repo's `cli.py` as-is.** Real
  end-to-end demo is blocked until the fork is handed over.
- **`WAYPOINT_STOP_OVERRIDE`** (D's term) — a runner-level reflex: on a predicted premature
  stop, physically force `vx=0.5` for 0.5s to change the camera view and break the "frozen
  frame → VLM says stop forever" visual deadlock. Stronger than this repo's existing
  `WAYPOINT_STOP_REJECTED` (`runner.py` ~line 290), which only re-prompts the VLM with the
  same frame — D found that insufficient. **Precedence conflict** (see "Open"): a
  `SafetyWatchdog` trip or a veto `VETO` is also a no-motion stop and must NOT trigger the
  forward nudge.
- **Prompting finding**: positive spatial constraints beat negative ones. "Maintain a strict
  1-meter safety boundary" got the robot to navigate 3.4+ m and brake smoothly before a
  hazard. Use positive-boundary phrasing in generated instructions.
- D's machine is **CPU-only** (`--device cpu`, local CUDA constraints) — a different box from
  A's GPU machine. Per-decision NaVILA latency there is high; factor into per-step timeout tuning.

## Fault injection (for testing AND the live demo)

We inject synthetic hazards on purpose — this is standard practice (same idea as chaos
engineering / AV sensor-fault testing), not a shortcut, and we say so openly in the pitch.

- **Automated testing**: `ScenarioInjector` (see above, built) composites a hazard marker onto
  captured frames before they reach the veto agent, and `MockForceSensor` (built) schedules a
  "force drops to zero" event. Fully disclosed as our test harness.
- **Live demo**: use something visibly real, not a frame overlay — either spawn/move an object
  in the OrcaLab scene via the edit service, or a physical prop held in front of whatever
  camera feeds the run. The edit service **does support writes** (verified, see below), so the
  in-scene approach is viable — no need to fall back to the physical-prop plan by default.

## Known technical facts (verified — don't re-derive)

- NaVILA's VLM server: bare TCP socket, 8-byte big-endian length-prefixed JSON framing.
- Inference needs exactly 8 JPEG-encoded frames per call; history via `sample_history()`.
- Action vocabulary: strict 7-option discrete grammar, parsed by `actions.py`, **hard-fails**
  on malformed output. On parse failure, the safe default is STOP — never silent retry, never
  crash the whole loop. (The veto agent's own parser follows the same hard-fail philosophy —
  see `veto/veto_agent.py::parse_veto_response` — except its safe default on failure is VETO,
  not STOP, since VETO is the conservative choice for that particular gate.)
- Camera capture (sim): gRPC to the OrcaLab edit service, port 50151.
- Physics (sim): two paths exist, don't assume the first is canonical. (a) In-process
  MJLab/MJWarp (`navila_orca.backends.MjlabGo2Backend`, GPU) — offline rollout and the
  per-step bridge's `mjlab` mode. (b) The OrcaLab render path (`--render-backend orcalab`
  + `--orcagym-address`) — this is what drives the OrcaLab GUI window judges watch and is
  the verified end-to-end demo path (see "Windows-specific" and "D's components" below).
- `unitree_sdk2_python` (real hardware — stretch goal only): needs `cyclonedds==0.10.2`; may
  require building CycloneDDS from source if no prebuilt wheel matches the platform. Test the
  install FIRST, in isolation, before writing any integration code against it.
- Go2's onboard camera (`VideoClient.GetImageSample()`) returns JPEG bytes directly — no
  transcode step needed if we ever move off OrcaLab's camera.
- Go2 has built-in obstacle avoidance (`ObstaclesAvoidClient`) on real hardware — don't rebuild
  this, just toggle it if real hardware ever enters the picture.
- **OrcaLab edit-service writes are confirmed working.** `EditServiceWrapper` (from
  `orcalab.protos.edit_service_wrapper`) exposes `set_actor_transform_batch(paths, transforms)`
  over the port-50151 gRPC connection, and it moved a live actor in a running scene. `Transform`
  comes from `orcalab.transform` (fallback `orcalab.math`) and requires **numpy arrays**, not
  lists, for `position` (shape (3,)) and `rotation` (wxyz quaternion, shape (4,)). Actor paths
  must be wrapped in `orcalab.path.Path(...)`, not passed as bare strings. `destroy_grpc()` is
  a coroutine — must be awaited or it warns and leaks. `EditServiceWrapper` also exposes
  `add_actor_batch`, `delete_actor_batch`, `save_state`/`restore_state`, `get_camera_png`, and
  `change_sim_state` — all candidates for D's scene-reset and hazard-object work.
- **The Go2 does not persist position across `navila-orca run` invocations.** Each run resets
  to the pose baked into the currently-loaded OrcaLab layout (`--anchor-existing-scene` anchors
  to "the authored Go2 scene XY/yaw" — i.e. whatever the live scene currently has, not a fixed
  config value). Confirmed empirically: manually dragging the Go2 in the editor between runs
  changes where the next run starts. This means: (a) per-step `navigate_step()` tools MUST
  write the robot's final transform back via `set_actor_transform_batch` after each step, or
  consecutive steps will not compose into a route; (b) this is also the mechanism for Stage-1
  scene reset (D's task) — write the layout's original transform back to reset.
- `--waypoint-instruction-file` exists on the CLI (`navila-orca run`) — one staged instruction
  per non-empty line, executed in sequence **within a single run**, no reset between stages.
  This is a useful reference for how the fork's own author intended multi-leg sequencing, and
  may be a faster path to a single-run demo fallback if the per-step MCP tools slip.
- The bundled robot prefab pack (`unitree_robots`) ships both `go2_usda` and `b2_usda`. The
  Go2 locomotion checkpoint (`go2_flat.pt`) is trained for Go2 geometry specifically — using
  the B2 prefab will fail `--strict-scene-alignment` or produce garbage motion. Always confirm
  the actor in the scene outline is the Go2, not the B2, before a run.
- Only one checkpoint ships in this fork: `go2_flat.pt` (flat-ground locomotion). There is no
  separate "beg" or trick-animation policy reachable through NaVILA's action vocabulary — any
  such behaviour would have to come from an OrcaLab prefab animation triggered independently
  of the navigation loop, not from an instruction string.
- NaVILA responds to instruction content, not just executing a fixed forward gait — confirmed
  by observing turn commands (`turn left`/`turn right`, non-zero `wz`) appear in
  `vlm_outputs` when the prompt and scene geometry call for a turn, not only `move forward`.

## Windows-specific (this fork, this session)

- Native Windows OrcaLab (regular installer, not WSL) plus a Windows-native `orcalab` conda
  **prefix** env reproduces the full pipeline, including real Vulkan/CUDA on an NVIDIA GPU.
  This is the recommended path on Windows.
- **WSL2 cannot run OrcaLab's native viewport.** Atom/O3DE requires a real Vulkan device; WSL2
  only exposes `llvmpipe` (software rasterizer, `PHYSICAL_DEVICE_TYPE_CPU`) even with
  `LIBGL_ALWAYS_SOFTWARE=1` and an explicit lavapipe ICD — confirmed by three separate crash
  reproductions, all failing at the same point (`AZ::RPI::WindowContext::Initialize`). CUDA
  *does* work fine in WSL2 (NVIDIA's stub driver), so headless/compute-only work in WSL is
  fine — only the OrcaLab GUI itself is the blocker. Do not spend more time trying to fix this;
  it's a WSL2/Vulkan limitation, not a config issue.
- Building the env on Windows: the `setup_orcalab_env.sh` steps up to and including the
  `pip install --editable "NaVILA-Orca[orca,test]"` line are portable (tested working on
  Windows via a manual `python -m pip install` sequence in Git Bash). The
  `prepare_orcalab_runtime.py` / `--verify` steps after that are Linux-only (patchelf/RPATH/
  XCB checks against the bundled Linux OrcaLab GUI) — skip them on Windows; they are not needed
  since Windows uses the native installer, not the bundled `orcalab-pyside` GUI.
  `torch==2.11.0+cu128` from the PyTorch cu128 index installs and runs fine against a driver
  reporting a newer CUDA version (13.1) — the cu128 wheel is backward compatible.
- `python -m navila_orca.cli run [flags]` can be invoked directly (Git Bash, env's
  `python.exe`), bypassing the bash launcher entirely. This is the flag set that's been
  verified working end-to-end against OrcaLab-native-Windows + the AWS NaVILA endpoint:
  `--render-backend orcalab --orcagym-address 127.0.0.1:50051 --orcalab-edit-address
  127.0.0.1:50151 --camera-actor-name mujococamera1080 --camera-asset-path
  prefabs/mujococamera1080 --orcalab-camera-mode mujoco-png --camera-transport grpc-png
  --no-publish --robot-actor-name auto --anchor-existing-scene --scene-profile orca-train
  --strict-scene-alignment --manual-xml-override --vlm-backend tcp --vlm-host 127.0.0.1
  --vlm-port 54321 --image-interval 0.5 --state-stream-interval 0.04 --warmup-steps 100`.
  Always set `--max-decisions` (0 = unlimited, confirmed) and `--max-control-steps` (0 =
  unlimited, confirmed — do not assume 0 silently means "run zero steps," it was checked via
  `--help` and it means unlimited).
- **Set `PYTHONIOENCODING=utf-8` before every run on Windows.** Without it, the CLI dies
  immediately on start with `UnicodeEncodeError` on a Unicode arrow character the console's
  default codepage can't encode — this is a hard blocker, not cosmetic, and it fails silently
  fast enough to look like an instant crash with no useful stack context near the top.
- Port collision: **Multipass** (`multipassd.exe`, if installed — e.g. for other VM work) binds
  `127.0.0.1:50051` by default, the same port OrcaLab's OrcaGym sim service wants. Symptom: a
  freshly-launched native OrcaLab window opens then crashes with no obvious log. Fix:
  `Stop-Service multipass` (admin PowerShell) before launching OrcaLab, and check
  `netstat -ano | findstr "50051 50151"` returns nothing before assuming an OrcaLab problem is
  something else. Multipass autostarts, so this needs re-checking after every reboot unless its
  startup type is set to Manual.
- The AWS SSM port-forward tunnel (`aws ssm start-session ... --document-name
  AWS-StartPortForwardingSession --parameters '{"portNumber":["54321"],"localPortNumber":
  ["54321"]}'`) must be run in its own terminal and left open — it dies the instant that
  terminal closes, including on a reboot. There is no persistent/background mode being used
  currently; check liveness with `scripts/check_navvlm_endpoint.py --host 127.0.0.1 --port
  54321` before assuming a run failure is a model problem rather than a dead tunnel.
- Git Bash mangles some Windows-targeted commands (notably `msiexec /i <url>`, which prints
  msiexec's own usage instead of running — it's a path-translation issue, not a permissions
  one) and multi-line pasted commands are prone to silently losing a trailing backslash
  mid-paste, which splits one command into two and produces a confusing "unrecognized
  arguments" error rather than a syntax error. Prefer saving long invocations as a `.sh` file
  and running `bash script.sh "$instruction"` over pasting multi-line commands directly.
  `taskkill`, `msiexec`, `Stop-Service` etc. belong in PowerShell, not Git Bash.

## Resolved issues (don't re-debug)

- 300s → 900s timeout: correct for the research/bridge-script context. **Not** correct for a
  live per-step safety loop — that needs a short per-step timeout with default-to-stop on expiry.
- `mcp dev` Node v18 failure — fixed by upgrading to v20+.
- Missing space in `claude mcp add` causing "unknown option" — watch syntax carefully.
- WSL2 cannot render OrcaLab — see "Windows-specific" above. Don't re-attempt; use native
  Windows OrcaLab instead.
- Windows env setup for `orcalab` conda prefix env — full working command sequence exists;
  see "Windows-specific" above rather than re-deriving from the Linux setup script.
- `python.exe` inside a Cowork `device_bash` call — that shell is a Linux VM, not the actual
  Windows machine, so the conda prefix env's interpreter can't run there. See "Environment"
  above for the workaround for the pure-Python modules.

## Open / unresolved

- ~~`json.dumps` TypeError in the bridge~~ **Root-caused + fixed.** stdlib `json` can't
  serialize numpy/torch scalars (`np.float32`, `np.int64`, `np.ndarray`; `np.float64` slips
  through as a `float` subclass — hence the intermittency). `navila_bridge.py` has a
  duck-typed `_jsonable()` coercer at the MCP tool boundary + `_dumps()` for every debug
  print / status write — never call bare `json.dumps` in that module again.
- ~~**Top priority (C)**: per-step tools + Stage 2 watchdog loop + C1 GUI mirror.~~ **All
  done + on `main`** — see "Per-step bridge (C) — status" above. Remaining per-step work is
  Stage 3 (veto gate, C) and C2 (real gait + ego frames, D).
- **C2 — real gait + ego-camera frames (open, D).** `navila_navigate_step` still hands the VLM
  `bridge_backends.placeholder_frame()` (8×8 black), and the OrcaLab mirror is root-pose only
  (dog glides). Wiring `OrcaLabRenderBridge` (full qpos push via OrcaGym `UpdateLocalEnv` +
  `capture()` for real RGB) into a `StepBackend` gets both. Needed before the real veto path
  (`AnthropicVetoVisionClient` on real frames) and before the `tcp` NaVILA VLM can run in the
  per-step loop. Needs GPU for the Go2 policy.
- **D's NaVILA-Orca fork is not in this repo** — `--realtime-visual-sync` / `--rehearsal` /
  `--traffic-light-crossing` / `--traffic-*-waypoint` and the `WAYPOINT_STOP_OVERRIDE` runner
  logic live only on D's machine, which can't push to origin. Needs a `git bundle` / zip
  handoff — or, now that D has an AI coding assistant, re-implement those flags directly in
  this repo's `cli.py` / `runner.py`. Blocks the traffic-crossing demo specifically.
- **`WAYPOINT_STOP_OVERRIDE` vs. safety/veto precedence** — rule to implement once D's
  override lands: a `SafetyWatchdog` trip or a `VETO` sets a flag that suppresses the forward
  nudge for that step, so a legitimate stop is never overridden into motion.
- **Does OrcaLab run its own physics on the Go2 under "Play"? (open, D.)** C1's mirror
  teleports the robot actor via `set_actor_transform_batch`; if OrcaLab is simulating the Go2
  it may fight the teleport (jitter/ragdoll). Verified gliding cleanly in the current session;
  confirm behaviour under Play and whether the scene needs the Go2 set externally-driven.
- Whether `--waypoint-instruction-file`'s within-run staging could substitute for true per-step
  MCP tool calls in a time-crunch fallback — untested for how it interacts with the Veto Agent
  (veto needs to gate *before* a step executes, which a pre-baked waypoint file can't do; it's
  a fallback for the demo working at all, not a substitute for the differentiator).
- The repo currently has a pile of untracked stray files in the repo root named after CLI flags
  (`--anchor-existing-scene`, `--camera-actor-name`, etc.) — looks exactly like the "lost
  trailing backslash mid-paste" Git Bash issue described above, where a multi-line command got
  split and the flags landed as filenames. Clean these up before doing `git add -A`.
- `git diff --stat` against `origin/main` currently shows 146 tracked files each with the exact
  same insertion and deletion count — the signature of a bulk line-ending/encoding conversion,
  not real edits. Don't commit this as-is; figure out what caused it (likely a checkout-time
  CRLF conversion) before staging those files.
- `navila_agent.py` (untracked, repo root) is the old superseded Ollama/llava planning-layer
  prototype — see "Explicitly out of scope" below. Safe to delete once confirmed nothing else
  references it.
- `anthropic` is not yet added as a dependency in `pyproject.toml` — needed before
  `veto/claude_vision_client.py::AnthropicVetoVisionClient` can actually be used for real
  (the rest of `veto/` works without it).

## Explicitly out of scope this week

Real Unitree hardware, `TrajectoryFollow`, multi-axis force disambiguation, route memory,
TTS/audio output (plain text/log output is enough for the demo — keep it behind one `notify()`
function so swapping in TTS later is a 10-minute polish item, not a rebuild). These are
"roadmap" talking points for the pitch, not build targets. Voice input and caregiver alerts are
not happening this week either — later, not now, not even as a stretch goal.

An earlier direction explored routing a general local vision-language model (llava, via
Ollama) as a planning layer above NaVILA, calling the CLI once per instruction. This is
**superseded by the Claude Code / MCP orchestrator design above** — the working parts of that
prototype (the `navigate()` wrapper pattern, the pose-carry-forward mechanism via
`set_actor_transform_batch`) fed directly into the "Known technical facts" and open items
above, but Ollama/llava itself is not part of the current architecture. The leftover
`navila_agent.py` file at the repo root is this prototype; see "Open / unresolved" above.
