# OrcaLab + NaVILA + Claude — Findings & Integration Plan

Consolidated from: `navila_vlm_server.py`, `check_navvlm_endpoint.py`, `vlm_client.py`,
`frames.py`, `actions.py`, `contracts.py`, `orca_camera.py`, `mjlab_go2.py`, and the
3-terminal setup guide. Source: your team's `NaVILA-Orca` repo (a private fork; not the
public `NaVILA-Bench`).

---

## TL;DR

- NaVILA's inference server is a **bare TCP socket**, not an HTTP/REST API. No auth.
- The Go2's **physics simulation runs locally, in-process, on GPU** (MJLab/MJWarp) —
  it is *not* a remote OrcaGym service you talk to over the network. This changes the
  whole mental model of "where does control happen."
- OrcaLab itself is used for **visualization and camera capture only**, via a separate
  gRPC "edit service."
- The existing `run_orcalab_scene_locomotion.sh` pipeline already implements a complete,
  working closed loop (camera → NaVILA → action parse → physics step → repeat until
  stop). The fastest, lowest-risk way to get Claude into this system is to **wrap that
  whole pipeline as one MCP tool first**, not to reimplement its internals.

---

## 1. Confirmed architecture map

| Address | What it actually is | Confirmed by |
|---|---|---|
| `127.0.0.1:54321` (tunneled from AWS via SSM) | NaVILA VLM inference server. Raw TCP, length-prefixed JSON, no auth, no HTTP. | `navila_vlm_server.py`, `vlm_client.py`, `check_navvlm_endpoint.py` |
| `127.0.0.1:50151` (`--orcalab-edit-address`) | OrcaLab's **edit service** — a gRPC channel for scene/actor management and camera capture (`get_camera_png`, `set_properties`, `add_actor_batch`, etc.) | `orca_camera.py` |
| `127.0.0.1:50051` (`--orcagym-address`) | **Not yet directly observed.** Best current theory: a channel used to "scatter" the locally-computed physics state (robot joint positions) into OrcaLab's renderer so the GUI visually shows the robot moving. Inferred from a code comment in `mjlab_go2.py` ("renderer scatter stays name-based") and the fact that nothing in the physics backend itself makes a network call to this address. **Flagged as an open item below — don't treat as confirmed.** |

The three terminals map onto this as: Terminal 1 opens the tunnel to the VLM server;
Terminal 2 runs the OrcaLab GUI (which hosts the edit service and, presumably, whatever
answers on 50051); Terminal 3 runs `navila_orca.cli run`, which owns the entire control
loop internally.

---

## 2. NaVILA VLM server contract — fully confirmed

- **Framing**: 8-byte big-endian length prefix, then a UTF-8 JSON payload. Same shape
  for requests and responses, both directions.
- **Health check**: request `{"type": "health"}` → response
  `{"service": "navila-vlm", "status": "ok", "protocol_version": 1}`.
- **Inference request**: `{"images": [...base64 JPEG strings...], "query": "<instruction>"}`.
  Must contain **exactly `NUM_VIDEO_FRAMES` images (= 8)** or the server silently drops
  the request (the exception is swallowed server-side — no response is sent, and the
  client just hangs until timeout).
- **Inference response**: a JSON-encoded, non-empty string — freeform natural language
  describing one action (e.g. `"turn left 30 degrees"`).
- **Frame history handling** (`frames.py`, `sample_history()`): given a live buffer of
  captured frames, left-pads with black frames if fewer than 8 exist; if more than 8,
  picks 7 evenly spaced historical frames via `floor(i * (N-1) / 7)` plus the current
  frame as the 8th. This is the exact algorithm to reuse — don't reimplement it.
- **Timeouts**: the real inference client (`vlm_client.py`) defaults to a **120s** socket
  timeout (GPU inference is slow); the health-check client uses **5s**.
- **Encoding**: JPEG specifically (`encode_images_jpeg_base64`), not PNG.

---

## 3. Action vocabulary — fully confirmed (`actions.py`)

The response text is parsed by a **strict, tiny grammar** — not free-form:

- `stop`
- `move forward by <N> cm` — N must be exactly **25, 50, or 75** (maps to 0.5/1.0/1.5s
  at a fixed 0.5 m/s)
- `turn left/right by <N> degrees` — N must be exactly **15, 30, or 45** (maps to
  0.5/1.0/1.5s at a fixed π/6 rad/s)

Any other number, phrasing, or multiple action-phrases in one response raises
`ActionParseError` / `AmbiguousActionError` — there is **no silent fallback**. Effectively
NaVILA only ever emits one of ~7 discrete atomic actions per call.

---

## 4. Camera capture path — fully confirmed (`orca_camera.py`)

Connects only to the **edit service** (`127.0.0.1:50151`), never to `orcagym-address`.
Given your run scripts pass `--camera-actor-name mujococamera1080 --orcalab-camera-mode
mujoco-png`, the concrete classes in play are:

- `OrcaMujocoCameraFollower` — confirms/creates the persistent `mujococamera1080` actor.
- `OrcaMujocoPngCamera` — does the actual capture via a `get_camera_png` RPC, which
  writes a PNG to disk; the class then reads it back into a numpy RGB array.

Both require the real `orcalab.*` Python packages (`orcalab.actor`,
`orcalab.protos.edit_service_wrapper`, etc.), imported lazily.

---

## 5. Locomotion/physics backend — fully confirmed, and the big surprise (`mjlab_go2.py`)

`MjlabGo2Backend` runs the Go2's physics **entirely in-process, on GPU, via MJLab/MJWarp**
— it loads a trained RL checkpoint (`Unitree-Go2-Flat` task) directly into a local
`ManagerBasedRlEnv`, and steps it locally. There is no network call to OrcaGym anywhere
in this file.

Key implementation details worth the team knowing:

- **Startup is expensive**: `start()` loads the GPU sim, the checkpoint, and then runs a
  **100-step zero-velocity warmup** before anything else happens. This backend must be
  a long-lived, persistent object — never construct and discard it per call.
- **Control interface**: `set_velocity_command(vx, vy, wz)` sets the target; `step()`
  advances one policy tick and returns a `RobotState`. A `VelocityCommand` (from
  `actions.py`'s parser) maps directly onto repeated calls to this.
- **Strict alignment assertions**: on startup, it hard-fails (`RuntimeError`) if MuJoCo's
  timestep, integrator, ground friction/solref/solimp, or the policy's 12-joint action
  order don't exactly match the values the checkpoint was trained with. This is
  deliberate — it's designed to fail loudly rather than run a subtly-wrong policy.
- **`device` defaults to `"cpu"`** in the constructor signature — worth checking what
  the actual launch path passes, since running an RL policy on CPU vs GPU has real
  performance implications.

---

## 6. Open items — still unconfirmed, worth the team's attention

1. **What answers on `127.0.0.1:50051` (`orcagym-address`) and how the locally-computed
   physics state gets pushed into the OrcaLab GUI for visualization.** Not seen in any
   file so far — likely in `render/orca.py`, `render/grpc_bridge.py`, or
   `orcalab_runtime/` (all referenced by `ls` but not yet read).
2. **`cli.py`'s full content** — only grep hits so far. This is where `--orcagym-address`
   and `--orcalab-edit-address` actually get wired into concrete backend/render objects,
   and where the main control loop (frame capture → NaVILA call → action parse → physics
   step → repeat) is likely orchestrated.
3. **Whether `NUM_VIDEO_FRAMES=8` and other defaults could be overridden** by whatever
   arguments `start_navvlm_server.sh` actually passes on the AWS side.
4. Minor: `vlm_client.py`'s `ConnectionRefusedError` message references
   `scripts/start_vlm_server.sh`, but the real file is `start_navvlm_server.sh` — a stale
   error message, not a real second script.

---

## 7. Operational notes for the team

- Anything touching `orcalab.*` (camera capture) or `mjlab`/`mujoco-warp`/`rsl-rl`
  (physics) **must run inside the `orcalab` conda environment**. A bridge process
  launched from a different Python will fail at import time.
- Budget for **multi-second latency per NaVILA call** (real GPU inference on an 8B
  model) — fine for occasional high-level instructions, not for tight control loops.
- The action space is **intentionally coarse** (7 discrete actions) — don't design any
  Claude-side reasoning that assumes fine-grained continuous control.
- Malformed/ambiguous VLM output is a **hard failure**, not a soft fallback — any bridge
  needs explicit handling for `ActionParseError`/`AmbiguousActionError`.

---

## 8. MCP server integration plan

### Phase 1 (recommended starting point): wrap the existing pipeline as one tool

`run_orcalab_scene_locomotion.sh` (via `navila_orca.cli run --max-decisions 0
--max-control-steps 0`) already runs the **entire closed loop** — camera capture, NaVILA
query, action parsing, physics stepping — repeatedly, until the model says stop. That
means we don't need to reverse-engineer the unconfirmed render-sync layer (item #1
above) at all to get something working end-to-end.

```python
@mcp.tool()
def navila_run_instruction(instruction: str, timeout_s: int = 300) -> dict:
    """Run one full NaVILA navigation episode in OrcaLab for the given
    natural-language instruction, and report the outcome."""
    result = subprocess.run(
        ["./NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh",
         "--instruction", instruction],
        cwd="/path/to/Orca_VLN",
        capture_output=True, text=True, timeout=timeout_s,
    )
    # parse outputs/scene_locomotion_smoke/measurements.json for the
    # structured outcome (path taken, stop reason, step count, etc.)
    ...
    return {"returncode": result.returncode, "log_tail": result.stdout[-2000:], ...}
```

**Claude's role in this phase**: high-level task decomposition. Claude decides *what*
instruction to issue (possibly breaking a bigger goal into several sequential
instructions), calls this tool, reads back the outcome/logs, and decides the next
instruction. This is a genuine agentic loop — Claude is reasoning and NaVILA is acting —
without either side needing to change.

- **Pros**: reuses 100% tested code; no reverse-engineering required; buildable today.
- **Cons**: Claude can't see intermediate camera frames or intervene mid-episode; each
  call is coarse-grained (a full episode, not one atomic action).

### Phase 2 (later, if finer control is wanted): expose primitives directly

Once open items #1–#2 are resolved, expose each step as its own tool so Claude can
reason between every atomic action instead of committing to a full episode:

- `orcalab_get_camera_frame()` — via `OrcaMujocoPngCamera`
- `navila_get_next_action(instruction, frame_paths)` — via `LengthPrefixedJsonVLMClient`
  + `sample_history`
- `orcalab_execute_velocity_command(cmd)` — via a persistent `MjlabGo2Backend`
- `orcalab_get_robot_state()` — via the same backend's `.step()` return value

This requires: confirming the render-sync mechanism, and managing the `MjlabGo2Backend`
and camera objects as long-lived singletons inside the bridge process (not per-call).
Meaningfully more engineering effort, but gives Claude the ability to re-plan after
every atomic turn/move rather than committing to a whole episode at once.

---

## 9. Concrete next steps

1. Decide: start with Phase 1 (fast, low-risk) or go straight for Phase 2 primitives?
2. If Phase 1: confirm the exact CLI invocation and output format
   (`outputs/scene_locomotion_smoke/measurements.json`) so the bridge can parse a
   structured result, not just raw stdout.
3. If Phase 2 (or eventually, regardless): grab `cli.py`'s full content and whatever's
   in `render/orca.py` / `render/grpc_bridge.py` / `orcalab_runtime/` to close item #1.
4. Either way: set up the MCP server's Python environment to point at the `orcalab`
   conda env's interpreter, and register it with `claude mcp add`.
