#!/usr/bin/env python3
"""Phase 1 MCP bridge: Claude Code <-> NaVILA-Orca / OrcaLab locomotion pipeline.

This does NOT reimplement the VLM socket protocol, action parsing, or physics
stepping. It wraps the existing, already-working
`run_orcalab_scene_locomotion.sh` script as a single MCP tool and parses its
`measurements.json` output into a structured result.

REQUIRED BEFORE THIS WILL WORK:
  1. Terminal 1: the AWS SSM tunnel must be open (localhost:54321 reachable).
  2. Terminal 2: the OrcaLab GUI must be open, factory.json loaded, and the
     simulation started (manual launch mode) -- exactly as in your normal
     3-terminal setup.
  3. This script must be run with the `orcalab` conda environment's Python
     interpreter (it shells out to a script that itself needs that
     environment; the MCP process doesn't strictly need orcalab.* imports
     itself, but keeping it in the same env avoids any PATH/env-var surprises).

Install the MCP SDK first (inside the orcalab env):
    pip install "mcp[cli]"
"""

import dataclasses
import json
import math
import os
import socket
import subprocess
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("navila-orcalab-bridge")

# ---------------------------------------------------------------------------
# Configuration -- confirmed from your own measurements.json / setup guide.
# Double-check these still match your machine before relying on this.
# ---------------------------------------------------------------------------
ORCA_VLN_ROOT = Path("/home/guest/simplifynext/budo-simplfynexthack")
LOCOMOTION_SCRIPT = ORCA_VLN_ROOT / "NaVILA-Orca" / "scripts" / "run_orcalab_scene_locomotion.sh"
MEASUREMENTS_PATH = ORCA_VLN_ROOT / "NaVILA-Orca" / "outputs" / "scene_locomotion_smoke" / "measurements.json"
# The pipeline's runtime resolver (scripts/orcalab_env.sh) otherwise can't find an
# OrcaLab Python: there is no project-local .conda/envs/orcalab, and the MCP server
# is spawned with no conda env activated. Point it at 'orcalab-phys' -- a clone of
# the 'orcalab' env with the CPU physics stack added (mjlab 1.2.0, mujoco-warp
# 3.5.0, rsl-rl-lib 5.0.1, torch 2.11.0+cpu, warp-lang PINNED to 1.12.0 -- 1.17.0
# breaks mujoco_warp sensor codegen -- and opencv-python-headless 5.0.0.93).
ORCALAB_PYTHON = "/home/guest/miniconda3/envs/orcalab-phys/bin/python"
# navila_orca.cli defaults --device to "cuda:0"; this box has no GPU. The physics
# backend still needs mjlab/mujoco-warp/rsl-rl installed in ORCALAB_PYTHON's env
# before an episode can actually run -- this only avoids the CUDA pre-flight abort.
ORCALAB_DEVICE = "cpu"
VLM_HOST = "127.0.0.1"
VLM_PORT = 54321
DEFAULT_TIMEOUT_S = 900  # 15 minutes. 300s proved too short in real testing --
                          # a ~27-decision episode did not reliably finish in time.
TAIL_CHARS = 2000  # how much of stdout/stderr to keep on failure, for debugging

METRICS_CAVEAT = (
    "scene_fidelity is false and Matterport 3DGS collision geometry is not "
    "installed in this environment, so 'success'/'spl' here are "
    "protocol-compatibility placeholders, not real navigation-quality "
    "evidence. Use termination_reason and vlm_outputs as the source of truth "
    "for what actually happened, not the success/spl numbers."
)


# ---------------------------------------------------------------------------
# JSON safety -- root cause of the `json.dumps` TypeError that was blocking
# the per-step refactor.
#
# stdlib `json` cannot serialize numpy/torch values. Confirmed failures:
#   np.float32, np.int64, np.ndarray  -> TypeError: Object of type ... is not
#   JSON serializable
# (np.float64 happens to slip through because it subclasses `float`, which is
# why this only bites intermittently.)
#
# The Phase-1 tools below only ever return values parsed back out of
# measurements.json (already plain JSON), so they are safe today. But every
# Phase-2 per-step tool -- navigate_step(), get_status(), emergency_stop() --
# will hand back live RobotState fields (step_id, root_pos_world, base_rpy,
# joint_pos, ...) that come straight off MJWarp/torch tensors as numpy. The
# moment any of that reaches `json.dumps` (an MCP return value, a status file,
# or a debug print) it raises and kills the tool call.
#
# Fix: one recursive coercer, applied at the MCP tool boundary. Duck-typed so
# the Phase-1 bridge keeps needing no numpy/torch import of its own.
# ---------------------------------------------------------------------------

def _jsonable(obj):
    """Recursively coerce a value into something stdlib json can serialize.

    Handles dict/list/tuple/set, dataclasses, numpy arrays and scalars, and
    torch tensors -- without importing numpy or torch. Anything still unknown
    (Path, Enum, ...) is stringified rather than allowed to raise.
    """
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _jsonable(dataclasses.asdict(obj))
    if hasattr(obj, "detach"):  # torch.Tensor -> host memory first
        obj = obj.detach().cpu() if hasattr(obj, "cpu") else obj.detach()
    if hasattr(obj, "tolist") and not isinstance(obj, (bytes, bytearray)):
        try:
            return _jsonable(obj.tolist())  # numpy ndarray, torch tensor
        except Exception:
            pass
    if hasattr(obj, "item"):
        try:
            return obj.item()  # numpy scalar: np.float32 / np.int64 / np.bool_
        except Exception:
            pass
    return str(obj)  # Path, Enum, and any other last-resort type


def _dumps(obj, **kwargs) -> str:
    """json.dumps that never raises on numpy/torch. Use this for every debug
    print or status-file write in this module -- never bare json.dumps."""
    kwargs.setdefault("indent", 2)
    return json.dumps(_jsonable(obj), default=str, **kwargs)


# ---------------------------------------------------------------------------
# Plain, undecorated implementations -- these are the ones to unit-test
# directly without going through MCP or Claude Code at all. See the testing
# instructions for exactly how.
# ---------------------------------------------------------------------------

def _health_check_impl() -> dict:
    """Confirm the NaVILA VLM server is reachable through the SSM tunnel."""
    request = json.dumps({"type": "health"}, separators=(",", ":")).encode()
    try:
        with socket.create_connection((VLM_HOST, VLM_PORT), timeout=5.0) as sock:
            sock.sendall(len(request).to_bytes(8, "big"))
            sock.sendall(request)

            size_bytes = sock.recv(8)
            if len(size_bytes) != 8:
                return {"ok": False, "error": "connection closed before size header arrived"}
            size = int.from_bytes(size_bytes, "big")

            buf = b""
            while len(buf) < size:
                chunk = sock.recv(size - len(buf))
                if not chunk:
                    break
                buf += chunk
            response = json.loads(buf.decode("utf-8"))
    except (ConnectionRefusedError, socket.timeout, OSError) as exc:
        return {
            "ok": False,
            "error": (
                f"cannot reach NaVILA at {VLM_HOST}:{VLM_PORT}: {exc}. "
                "Is Terminal 1's SSM tunnel still open?"
            ),
        }

    return {"ok": response.get("status") == "ok", "response": response}


def _run_instruction_impl(instruction: str, timeout_s: int) -> dict:
    """Run one full NaVILA episode for `instruction` and parse the result."""
    if not instruction or not instruction.strip():
        return {"ok": False, "error": "instruction must be a non-empty string"}
    if not LOCOMOTION_SCRIPT.exists():
        return {"ok": False, "error": f"script not found at {LOCOMOTION_SCRIPT}"}

    started_at = time.time()
    try:
        result = subprocess.run(
            [str(LOCOMOTION_SCRIPT), "--instruction", instruction,
             "--device", ORCALAB_DEVICE],
            cwd=str(ORCA_VLN_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env={**os.environ, "NAVILA_ORCA_PYTHON": ORCALAB_PYTHON},
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "elapsed_s": round(time.time() - started_at, 1),
            "error": f"episode timed out after {timeout_s}s",
            "raw_stdout_tail": (exc.stdout or "")[-TAIL_CHARS:],
            "raw_stderr_tail": (exc.stderr or "")[-TAIL_CHARS:],
        }

    elapsed_s = round(time.time() - started_at, 1)

    if result.returncode != 0:
        return {
            "ok": False,
            "error": f"script exited with code {result.returncode}",
            "elapsed_s": elapsed_s,
            "raw_stdout_tail": result.stdout[-TAIL_CHARS:],
            "raw_stderr_tail": result.stderr[-TAIL_CHARS:],
        }

    if not MEASUREMENTS_PATH.exists():
        return {
            "ok": False,
            "error": f"script exited 0 but measurements.json not found at {MEASUREMENTS_PATH}",
            "elapsed_s": elapsed_s,
            "raw_stdout_tail": result.stdout[-TAIL_CHARS:],
        }

    try:
        data = json.loads(MEASUREMENTS_PATH.read_text())
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"measurements.json is not valid JSON: {exc}", "elapsed_s": elapsed_s}

    metrics = data.get("metrics", {})
    final_state = data.get("final_state", {})
    episode = data.get("episode", {})

    return {
        "ok": True,
        "elapsed_s": elapsed_s,
        "instruction_run": episode.get("instruction"),
        "pipeline_status": data.get("pipeline_status"),
        "termination_reason": data.get("termination_reason"),
        "decisions": data.get("decisions"),
        "control_steps": data.get("control_steps"),
        "vlm_outputs": data.get("vlm_outputs", []),
        "final_position": final_state.get("root_pos_world"),
        "metrics": metrics,
        "metrics_caveat": METRICS_CAVEAT,
        "limitations": data.get("limitations", []),
    }


# ---------------------------------------------------------------------------
# MCP tool registrations -- thin wrappers around the implementations above.
# ---------------------------------------------------------------------------

@mcp.tool()
def navila_health_check() -> dict:
    """Check that the NaVILA VLM server is reachable before running a full
    episode. Fast (a few seconds) -- use this before navila_run_instruction,
    which can take minutes."""
    return _jsonable(_health_check_impl())


@mcp.tool()
def navila_run_instruction(instruction: str, timeout_s: int = DEFAULT_TIMEOUT_S) -> dict:
    """Run one full NaVILA navigation episode in OrcaLab for a given
    natural-language instruction (e.g. "walk toward the red waste bin and
    stop beside it"), and return a structured summary of what happened.

    This shells out to the existing run_orcalab_scene_locomotion.sh pipeline,
    which runs the whole closed loop internally (camera capture -> NaVILA
    query -> action parsing -> physics stepping) repeatedly until the model
    decides to stop or the episode times out. Requires the AWS SSM tunnel
    (Terminal 1) and the OrcaLab GUI with simulation running (Terminal 2) to
    already be up.
    """
    return _jsonable(_run_instruction_impl(instruction, timeout_s))


# ===========================================================================
# Phase 2 -- per-step tools (PLAN.md Stage 1: the seam the veto agent and the
# safety watchdog plug into).
#
# navila_run_instruction above runs a whole episode as one opaque call. The
# tools below expose one decision at a time so the Orchestrator (Claude) can
# reason between every atomic turn/move:
#
#   navila_start_episode(instruction, goal_x=, goal_y=)  -> arms the loop
#   navila_navigate_step()   repeatedly, until it returns done=true
#   navila_get_status()      read pose/counters without advancing physics
#   navila_emergency_stop()  latch zero velocity now (watchdog-style)
#   navila_reset_episode()   re-arm with the same parameters
#
# Backend + VLM come from bridge_backends.make_backend / make_vlm, selected by
# env var (default: MockBackend + MockVLM, so this works with no tunnel/GPU).
# ===========================================================================

_PERSTEP: dict = {}


def _load_perstep() -> dict:
    """Import the per-step dependencies lazily so a broken navila_orca / physics
    env never breaks the Phase 1 tools above. Returns {} on failure with the
    error stashed under 'error'."""
    if _PERSTEP:
        return _PERSTEP
    try:
        import bridge_backends as bb
        from navila_orca.actions import ActionParseError, parse_velocity_command
        from navila_orca.contracts import PhysicsStep, VelocityCommand
        from navila_orca.runner import duration_to_ticks
    except Exception as exc:  # noqa: BLE001 -- surface any import failure verbatim
        _PERSTEP["error"] = f"per-step dependencies unavailable: {exc!r}"
        return _PERSTEP
    _PERSTEP.update(
        make_backend=bb.make_backend,
        make_vlm=bb.make_vlm,
        placeholder_frame=bb.placeholder_frame,
        parse_velocity_command=parse_velocity_command,
        ActionParseError=ActionParseError,
        duration_to_ticks=duration_to_ticks,
        VelocityCommand=VelocityCommand,
        PhysicsStep=PhysicsStep,
    )
    return _PERSTEP


def _pose(state) -> dict:
    x, y, z = (float(v) for v in state.root_pos_world)
    return {
        "x": x,
        "y": y,
        "z": z,
        "yaw_deg": math.degrees(float(state.base_rpy[2])),
        "step_id": int(state.step_id),
        "sim_time_s": float(state.sim_time_s),
    }


def _wrap_deg(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0


def _action_text(cmd) -> str:
    """Canonical NaVILA phrase for a non-stop VelocityCommand."""
    if cmd.wz != 0.0:
        deg = round(math.degrees(abs(cmd.wz) * cmd.duration_s))
        return f"turn {'left' if cmd.wz > 0 else 'right'} by {deg} degrees"
    return f"move forward by {round(cmd.vx * cmd.duration_s * 100)} cm"


class _PerStepSession:
    """One navigation episode, advanced one decision per navila_navigate_step."""

    def __init__(self) -> None:
        self._reset_fields()

    def _reset_fields(self) -> None:
        self.phase = "idle"  # idle -> running -> done | stopped
        self.backend = None
        self.vlm = None
        self.instruction = ""
        self.goal_xy = None
        self.goal_radius = 0.5
        self.max_decisions = None
        self.max_control_steps = None
        self.decision_index = 0
        self.control_steps = 0
        self.last_action = None
        self.last_vlm_text = None
        self.termination_reason = None
        self._state = None
        self._frames = []
        self._start_kwargs = None

    # -- helpers --------------------------------------------------------------
    def _distance_to_goal(self):
        if self.goal_xy is None or self._state is None:
            return None
        px, py, _ = self._state.root_pos_world
        return math.hypot(self.goal_xy[0] - float(px), self.goal_xy[1] - float(py))

    def _snapshot(self) -> dict:
        return {
            "phase": self.phase,
            "done": self.phase in ("done", "stopped"),
            "instruction": self.instruction,
            "goal_xy": list(self.goal_xy) if self.goal_xy else None,
            "goal_radius": self.goal_radius,
            "decision_index": self.decision_index,
            "control_steps": self.control_steps,
            "last_action": self.last_action,
            "last_vlm_text": self.last_vlm_text,
            "termination_reason": self.termination_reason,
            "distance_to_goal": self._distance_to_goal(),
            "pose": _pose(self._state) if self._state is not None else None,
            "state": self._state,
        }

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        if self.backend is not None:
            try:
                self.backend.close()
            except Exception:  # noqa: BLE001
                pass
        self._reset_fields()

    def start_episode(self, instruction: str, **kw) -> dict:
        deps = _load_perstep()
        if "error" in deps:
            return {"ok": False, "error": deps["error"]}
        if not instruction or not instruction.strip():
            return {"ok": False, "error": "instruction must be a non-empty string"}

        self.close()
        self._start_kwargs = {"instruction": instruction, **kw}
        self.instruction = instruction.strip()

        goal_x, goal_y = kw.get("goal_x"), kw.get("goal_y")
        self.goal_xy = (
            None if goal_x is None or goal_y is None else (float(goal_x), float(goal_y))
        )
        self.goal_radius = float(kw.get("goal_radius", 0.5))
        self.max_decisions = int(kw.get("max_decisions", 25)) or None
        self.max_control_steps = int(kw.get("max_control_steps", 4000)) or None

        backend_kind = kw.get("backend_kind")
        vlm_kind = kw.get("vlm_kind")
        vlm_script = kw.get("vlm_script")
        try:
            self.backend = deps["make_backend"](backend_kind)
            vlm_kwargs = {}
            effective_vlm = (vlm_kind or os.environ.get("NAVILA_BRIDGE_VLM", "mock"))
            if vlm_script and effective_vlm == "mock":
                if isinstance(vlm_script, str):
                    vlm_script = [p.strip() for p in vlm_script.split(";") if p.strip()]
                vlm_kwargs["script"] = list(vlm_script)
            vlm_timeout_s = kw.get("vlm_timeout_s")
            if vlm_timeout_s is not None and effective_vlm == "tcp":
                # Per-decision socket timeout for the live loop. Defaults (see the
                # tool docstring) to the same 120s as the tested whole-episode
                # client -- shortening it is a deliberate tuning call once real
                # per-decision GPU latency is measured, not a number to guess at
                # here. What IS fixed now: any timeout/error below degrades to a
                # safe STOP instead of hanging or crashing the tool call.
                vlm_kwargs["timeout_s"] = float(vlm_timeout_s)
            self.vlm = deps["make_vlm"](vlm_kind, **vlm_kwargs)
            self.backend.start()
            self._state = self.backend.reset()
        except Exception as exc:  # noqa: BLE001
            self._reset_fields()
            return {"ok": False, "error": f"episode start failed: {exc!r}"}

        self._frames = [deps["placeholder_frame"]()]
        self.phase = "running"
        return {
            "ok": True,
            **self._snapshot(),
            "note": "episode armed; call navila_navigate_step until done=true",
        }

    def status(self) -> dict:
        if self.phase == "idle":
            return {"ok": True, "phase": "idle", "note": "no active episode"}
        return {"ok": True, **self._snapshot()}

    def navigate_step(self, goal: str = "") -> dict:
        deps = _load_perstep()
        if "error" in deps:
            return {"ok": False, "error": deps["error"]}
        if self.phase == "idle":
            return {"ok": False, "error": "no active episode; call navila_start_episode first"}
        if self.phase in ("done", "stopped"):
            return {
                "ok": True,
                **self._snapshot(),
                "action": "stop",
                "note": "episode already finished; call navila_reset_episode to run again",
            }

        instruction = goal.strip() if goal and goal.strip() else self.instruction

        # An out-of-band emergency_stop (Stage 3 watchdog) latches this flag.
        if getattr(self.backend, "interrupted", False):
            self.phase = "stopped"
            self.termination_reason = "emergency_stop"
            return {
                "ok": True,
                **self._snapshot(),
                "action": "stop",
                "note": "backend was interrupted before this step",
            }

        self._frames.append(deps["placeholder_frame"]())
        try:
            raw = self.vlm.next_action(
                instruction=instruction,
                state=self._state,
                frames=self._frames,
                goal_xy=self.goal_xy,
            )
        except Exception as exc:  # noqa: BLE001 -- socket timeout, refused, protocol
            # A live per-step loop must never hang or crash on a VLM fault: the
            # 900s whole-episode timeout in navila_run_instruction is wrong here
            # (see CLAUDE.md). Same rule as a parse failure -- default to STOP.
            self.phase = "done"
            self.termination_reason = "vlm_error"
            self.last_action = "stop"
            return {
                "ok": True,
                **self._snapshot(),
                "action": "stop",
                "note": f"VLM call failed -> safe STOP: {exc!r}",
            }
        self.last_vlm_text = raw

        try:
            cmd = deps["parse_velocity_command"](raw)
        except deps["ActionParseError"] as exc:
            # CLAUDE.md rule: malformed VLM output -> safe STOP, never crash.
            self.phase = "done"
            self.termination_reason = "parse_error"
            self.last_action = "stop"
            return {
                "ok": True,
                **self._snapshot(),
                "action": "stop",
                "raw_vlm_text": raw,
                "note": f"unparseable VLM output -> safe STOP: {exc}",
            }

        self.decision_index += 1
        if cmd.stop:
            self.phase = "done"
            self.termination_reason = "stop"
            self.last_action = "stop"
            return {"ok": True, **self._snapshot(), "action": "stop", "raw_vlm_text": raw}

        control_dt = float(self.backend.control_dt)
        ticks = deps["duration_to_ticks"](cmd.duration_s, control_dt)
        self.backend.set_velocity_command(cmd)
        start_pose = _pose(self._state)
        executed = 0
        chunk_term = None
        for _ in range(ticks):
            if (
                self.max_control_steps is not None
                and self.control_steps >= self.max_control_steps
            ):
                chunk_term = "max_control_steps"
                break
            if getattr(self.backend, "interrupted", False):
                chunk_term = "emergency_stop"
                break
            raw_step = self.backend.step()
            ps = (
                raw_step
                if isinstance(raw_step, deps["PhysicsStep"])
                else deps["PhysicsStep"](raw_step)
            )
            self._state = ps.state
            self.control_steps += 1
            executed += 1
            if ps.terminated or ps.truncated:
                chunk_term = "terminated" if ps.terminated else "truncated"
                break
        self._frames.append(deps["placeholder_frame"]())
        self.last_action = _action_text(cmd)

        end_pose = _pose(self._state)
        moved_m = math.hypot(
            end_pose["x"] - start_pose["x"], end_pose["y"] - start_pose["y"]
        )
        yaw_delta_deg = _wrap_deg(end_pose["yaw_deg"] - start_pose["yaw_deg"])

        done = False
        dist = self._distance_to_goal()
        if chunk_term in ("terminated", "truncated"):
            done, self.phase, self.termination_reason = True, "done", chunk_term
        elif chunk_term == "emergency_stop":
            done, self.phase, self.termination_reason = True, "stopped", "emergency_stop"
        elif chunk_term == "max_control_steps":
            done, self.phase, self.termination_reason = True, "done", "max_control_steps"
        elif dist is not None and dist <= self.goal_radius:
            done, self.phase, self.termination_reason = True, "done", "goal_reached"
        elif (
            self.max_decisions is not None
            and self.decision_index >= self.max_decisions
        ):
            done, self.phase, self.termination_reason = True, "done", "max_decisions"

        return {
            "ok": True,
            **self._snapshot(),
            "action": self.last_action,
            "raw_vlm_text": raw,
            "command": {
                "vx": cmd.vx,
                "vy": cmd.vy,
                "wz": cmd.wz,
                "duration_s": cmd.duration_s,
            },
            "requested_ticks": ticks,
            "executed_ticks": executed,
            "moved_m": moved_m,
            "yaw_delta_deg": yaw_delta_deg,
        }

    def emergency_stop(self) -> dict:
        if self.backend is None:
            return {"ok": True, "phase": self.phase, "note": "no active backend"}
        if hasattr(self.backend, "emergency_stop"):
            self.backend.emergency_stop()
            stop_path = "backend.emergency_stop()"
        else:
            try:
                vc = _load_perstep()["VelocityCommand"]
                self.backend.set_velocity_command(vc(0.0, 0.0, 0.0, 0.0, stop=True))
                stop_path = "latched zero VelocityCommand (backend has no emergency_stop)"
            except Exception as exc:  # noqa: BLE001
                stop_path = f"stop attempt failed: {exc!r}"
        if self.phase == "running":
            self.phase = "stopped"
        self.termination_reason = self.termination_reason or "emergency_stop"
        return {"ok": True, **self._snapshot(), "stop_path": stop_path}

    def reset_episode(self) -> dict:
        if not self._start_kwargs:
            return {"ok": False, "error": "no previous episode to reset; call navila_start_episode"}
        return self.start_episode(**self._start_kwargs)

    def continue_episode(self, instruction: str, **kw) -> dict:
        """Swap in a new instruction/goal WITHOUT resetting the robot.

        start_episode / reset_episode spawn a fresh backend and call
        backend.reset(), which teleports the dog back to its spawn pose. This
        keeps self.backend, self._state, and self._frames intact, so the next
        navila_navigate_step continues from wherever the last instruction left
        the robot. Only the VLM is rebuilt (so a scripted mock / history cursor
        starts clean for the new instruction). Use for every follow-up prompt.
        """
        deps = _load_perstep()
        if "error" in deps:
            return {"ok": False, "error": deps["error"]}
        if self.backend is None or self._state is None:
            return {
                "ok": False,
                "error": "no live episode to continue; call navila_start_episode first",
            }
        if not instruction or not instruction.strip():
            return {"ok": False, "error": "instruction must be a non-empty string"}

        self.instruction = instruction.strip()
        base = dict(self._start_kwargs or {})
        base.update({"instruction": self.instruction, **kw})
        self._start_kwargs = base

        if "goal_x" in kw or "goal_y" in kw:
            gx, gy = kw.get("goal_x"), kw.get("goal_y")
            self.goal_xy = None if gx is None or gy is None else (float(gx), float(gy))
        if "goal_radius" in kw:
            self.goal_radius = float(kw["goal_radius"])
        if "max_decisions" in kw:
            self.max_decisions = int(kw["max_decisions"]) or None
        if "max_control_steps" in kw:
            self.max_control_steps = int(kw["max_control_steps"]) or None

        try:
            vlm_kind = base.get("vlm_kind")
            vlm_script = base.get("vlm_script")
            vlm_kwargs = {}
            effective_vlm = vlm_kind or os.environ.get("NAVILA_BRIDGE_VLM", "mock")
            if vlm_script and effective_vlm == "mock":
                if isinstance(vlm_script, str):
                    vlm_script = [p.strip() for p in vlm_script.split(";") if p.strip()]
                vlm_kwargs["script"] = list(vlm_script)
            vlm_timeout_s = base.get("vlm_timeout_s")
            if vlm_timeout_s is not None and effective_vlm == "tcp":
                vlm_kwargs["timeout_s"] = float(vlm_timeout_s)
            self.vlm = deps["make_vlm"](vlm_kind, **vlm_kwargs)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"continue failed rebuilding VLM: {exc!r}"}

        # New instruction gets a fresh decision budget; control_steps stays as
        # the running safety tally across the whole session.
        self.decision_index = 0
        self.last_action = None
        self.last_vlm_text = None
        self.termination_reason = None
        self.phase = "running"
        return {
            "ok": True,
            **self._snapshot(),
            "note": "continued from current pose; call navila_navigate_step until done=true",
        }


_SESSION = _PerStepSession()


@mcp.tool()
def navila_start_episode(
    instruction: str,
    goal_x: float | None = None,
    goal_y: float | None = None,
    goal_radius: float = 0.5,
    max_decisions: int = 25,
    max_control_steps: int = 4000,
    backend_kind: str | None = None,
    vlm_kind: str | None = None,
    vlm_script: str | None = None,
    vlm_timeout_s: float | None = None,
) -> dict:
    """Arm a per-step navigation episode. After this, call navila_navigate_step
    repeatedly until it returns done=true.

    instruction: natural-language navigation goal for NaVILA.
    goal_x / goal_y: optional world-frame target; when set, the episode ends with
        termination_reason 'goal_reached' once the robot is within goal_radius (m).
    max_decisions / max_control_steps: safety caps (0 = unlimited).
    backend_kind: 'mock' (default) or 'mjlab'. vlm_kind: 'mock' (default) or 'tcp'.
    vlm_script: ';'-separated action phrases for the mock VLM, e.g.
        "move forward by 75 cm; turn left by 30 degrees; stop".
    vlm_timeout_s: per-decision socket timeout for vlm_kind='tcp' (default: the
        tested 120s whole-episode client default -- shorten this once real
        per-decision GPU latency is measured for a tighter live-loop budget).
        Ignored for vlm_kind='mock'. Any timeout/connection failure here
        degrades to a safe STOP (termination_reason='vlm_error'), never a hang
        or a crashed tool call.
    """
    return _jsonable(
        _SESSION.start_episode(
            instruction,
            goal_x=goal_x,
            goal_y=goal_y,
            goal_radius=goal_radius,
            max_decisions=max_decisions,
            max_control_steps=max_control_steps,
            backend_kind=backend_kind,
            vlm_kind=vlm_kind,
            vlm_script=vlm_script,
            vlm_timeout_s=vlm_timeout_s,
        )
    )


@mcp.tool()
def navila_navigate_step(goal: str = "") -> dict:
    """Advance the armed episode by exactly one NaVILA decision: capture a frame,
    query the VLM, parse the action, and execute that one motion chunk.

    Returns the action taken, the raw VLM text, the executed velocity command,
    per-chunk motion (moved_m, yaw_delta_deg), the full robot state, and
    distance_to_goal. When done=true, read termination_reason ('stop',
    'goal_reached', 'max_decisions', 'max_control_steps', 'terminated',
    'truncated', 'emergency_stop', 'parse_error') and stop calling this.

    goal: optional per-step instruction override (otherwise the episode's).
    """
    return _jsonable(_SESSION.navigate_step(goal))


@mcp.tool()
def navila_get_status() -> dict:
    """Report the current episode phase, robot pose, step counters, last action,
    and distance_to_goal WITHOUT advancing physics."""
    return _jsonable(_SESSION.status())


@mcp.tool()
def navila_emergency_stop() -> dict:
    """Immediately latch a zero velocity command on the backend (calls the
    backend's emergency_stop() if it has one). Any in-progress navila_navigate_step
    tick loop also bails as soon as it sees the interrupt. Use for an unsafe
    situation the Orchestrator spots; the Stage 3 Safety Watchdog calls the
    backend directly on the same path."""
    return _jsonable(_SESSION.emergency_stop())


@mcp.tool()
def navila_reset_episode() -> dict:
    """Re-arm a fresh episode with the exact parameters of the last
    navila_start_episode call (new backend + VLM, counters cleared). This
    RESETS the robot to its spawn pose -- use navila_continue_episode instead
    to keep the dog where it is."""
    return _jsonable(_SESSION.reset_episode())


@mcp.tool()
def navila_continue_episode(
    instruction: str,
    goal_x: float | None = None,
    goal_y: float | None = None,
    goal_radius: float | None = None,
    max_decisions: int | None = None,
    max_control_steps: int | None = None,
) -> dict:
    """Give the current episode a NEW instruction without resetting the robot.

    Unlike navila_start_episode / navila_reset_episode (fresh backend, dog
    teleports back to spawn), this keeps the live backend, physics state, and
    frame history, so the next navila_navigate_step continues from wherever the
    previous instruction left the robot. Use this for every follow-up prompt in
    a multi-step session. Fails if there is no live episode -- call
    navila_start_episode once at the top.

    Only the goal_* / max_* args you pass are changed; omitted ones keep the
    values from the last navila_start_episode.
    """
    kw: dict = {}
    if goal_x is not None or goal_y is not None:
        kw["goal_x"], kw["goal_y"] = goal_x, goal_y
    if goal_radius is not None:
        kw["goal_radius"] = goal_radius
    if max_decisions is not None:
        kw["max_decisions"] = max_decisions
    if max_control_steps is not None:
        kw["max_control_steps"] = max_control_steps
    return _jsonable(_SESSION.continue_episode(instruction, **kw))


if __name__ == "__main__":
    mcp.run()
