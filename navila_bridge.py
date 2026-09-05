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
import sys
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
#   navila_continue_episode(instruction)  new goal, keep current pose
#
# Stage 2 -- A's SafetyWatchdog + MockForceSensor + DecisionLogbook are wired
# into start_episode / navigate_step (see _build_safety_stack). Extra tools:
#   navila_inject_force_drop(at_step=)  schedule the harness-force fault
#   navila_clear_force_drops()          harness back to nominal
#   navila_clear_stop()                 un-latch an e-stop, resume from pose
#   navila_get_logbook()                timestamped stop/veto log
#
# Backend + VLM come from bridge_backends.make_backend / make_vlm, selected by
# env var or the start_episode backend_kind arg:
#   mock         planar kinematics, no GPU, headless (default)
#   mjlab        real MJWarp physics + Go2 policy, headless
#   orcalab      mjlab physics, robot pose mirrored into the OrcaLab GUI
#   orcalab-mock planar physics mirrored into the OrcaLab GUI (no GPU)
#   orcalab-render real MJWarp physics + full articulated qpos in OrcaLab
# 'orcalab' and 'orcalab-mock' push root pose only (dog glides, legs don't
# articulate) and degrade to headless if the edit service isn't reachable.
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
        trigger_scene_hazard=bb.trigger_scene_hazard,
        reset_scene_layout=bb.reset_scene_layout,
        parse_velocity_command=parse_velocity_command,
        ActionParseError=ActionParseError,
        duration_to_ticks=duration_to_ticks,
        VelocityCommand=VelocityCommand,
        PhysicsStep=PhysicsStep,
    )
    # A's safety stack (SafetyWatchdog + MockForceSensor + DecisionLogbook) is
    # optional: if it fails to import, the core per-step tools still work, the
    # watchdog is just unavailable. 'safety_error' records why.
    try:
        from navila_orca.decision_logbook import DecisionLogbook
        from navila_orca.robot_backend.mock_force_sensor import MockForceSensor
        from navila_orca.safety_watchdog import (
            SafeForceBand,
            SafetyWatchdog,
            WatchdogEvent,
        )

        _PERSTEP.update(
            SafetyWatchdog=SafetyWatchdog,
            SafeForceBand=SafeForceBand,
            WatchdogEvent=WatchdogEvent,
            MockForceSensor=MockForceSensor,
            DecisionLogbook=DecisionLogbook,
        )
    except Exception as exc:  # noqa: BLE001 -- watchdog optional, surface the reason
        _PERSTEP["safety_error"] = f"safety stack unavailable: {exc!r}"
    # The Hazard Veto Agent (Stage 3 differentiator) is also optional and
    # imported independently of the watchdog block above, so a failure in
    # either stack never takes down the other. 'veto_error' records why.
    try:
        from navila_orca.decision_logbook import DecisionLogbook
        from navila_orca.veto.scenario_injector import ScenarioInjector
        from navila_orca.veto.veto_agent import HazardVetoAgent
        from PIL import Image

        _PERSTEP.update(
            HazardVetoAgent=HazardVetoAgent,
            ScenarioInjector=ScenarioInjector,
            DecisionLogbook=DecisionLogbook,
            Image=Image,
        )
    except Exception as exc:  # noqa: BLE001 -- veto optional, surface the reason
        _PERSTEP["veto_error"] = f"veto stack unavailable: {exc!r}"
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


def _logbook_sink(line: str) -> None:
    """DecisionLogbook sink -> stderr. stdout is the MCP stdio transport, so the
    logbook must never print there; stderr shows up in the server log instead."""
    print(line, file=sys.stderr, flush=True)


def _env_flag(name: str, default: bool) -> bool:
    """Parse an on/off env var, e.g. NAVILA_BRIDGE_VETO. Unset -> default."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


class _RedBarStubVetoClient:
    """Self-contained VetoVisionClient stub -- no API key, no anthropic SDK.

    Detects ScenarioInjector's injected hazard marker directly from pixel data:
    the top bar always covers row 0 regardless of frame size (bar_height =
    max(4, height // 12) in scenario_injector.py), so (0, 0) is a reliable probe
    pixel. This is the seam navila_orca.veto.claude_vision_client.
    AnthropicVetoVisionClient swaps into later (see _make_veto_vision_client)
    without changing anything in _build_veto_stack or the navigate_step gate.
    """

    _HAZARD_RGB = (220, 20, 20)  # must match ScenarioInjector.inject's bar fill

    def query(self, image, instruction: str, proposed_action: str) -> str:
        del instruction, proposed_action
        if image.convert("RGB").getpixel((0, 0)) == self._HAZARD_RGB:
            return "VETO: injected hazard marker visible in the current frame"
        return "CLEAR"


def _make_veto_vision_client(kind: "str | None"):
    kind = (kind or os.environ.get("NAVILA_BRIDGE_VETO_CLIENT", "stub")).lower()
    if kind == "stub":
        return _RedBarStubVetoClient()
    if kind == "anthropic":
        # Deferred import, same reason claude_vision_client.py itself defers
        # 'anthropic': the stub/mock path must keep working with neither the
        # SDK nor an API key installed. Raises a clear RuntimeError (missing
        # package) if 'anthropic' isn't installed -- caught by the caller in
        # _build_veto_stack, which degrades to veto_agent=None rather than
        # crashing navila_start_episode.
        from navila_orca.veto.claude_vision_client import AnthropicVetoVisionClient

        return AnthropicVetoVisionClient()
    raise ValueError(f"unknown veto_client_kind {kind!r} (expected 'stub' or 'anthropic')")


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
        # Safety stack (rebuilt per episode). watchdog/logbook/sensor are None
        # when the safety stack failed to import or was disabled for the episode.
        self.watchdog = None
        self.logbook = None
        self._force_sensor = None
        self._watchdog_ticks = 0
        # Scheduled force-drop events survive close()/re-arm so a rehearsal loop
        # can inject once and run repeatedly. Cleared only by navila_clear_force_drops.
        self._force_events = getattr(self, "_force_events", [])
        # Hazard Veto Agent (Stage 3). Same persistence-across-reset shape as
        # the force-drop events above, cleared only by navila_clear_hazards.
        self.veto_agent = None
        self._hazard_injector = None
        self._hazard_events = getattr(self, "_hazard_events", [])
        # Set if veto_client_kind="anthropic" was requested but the client
        # failed to construct (missing 'anthropic' package, see
        # _make_veto_vision_client) -- episode still starts, just with
        # veto_agent=None, same silent-degrade-with-a-visible-reason shape as
        # safety_error/veto_error in _load_perstep().
        self._veto_client_error = None
        # WAYPOINT_STOP_OVERRIDE precedence (docs/PLAN.md, "C" item 2): see the
        # comment on stop_override_suppressed inside navigate_step.
        self.stop_override_suppressed = False

    # -- helpers --------------------------------------------------------------
    def _distance_to_goal(self):
        if self.goal_xy is None or self._state is None:
            return None
        px, py, _ = self._state.root_pos_world
        return math.hypot(self.goal_xy[0] - float(px), self.goal_xy[1] - float(py))

    def _snapshot(self) -> dict:
        snap = {
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
            "watchdog_enabled": self.watchdog is not None,
            "veto_enabled": self.veto_agent is not None,
            "veto_client_error": self._veto_client_error,
            "stop_override_suppressed": self.stop_override_suppressed,
        }
        if self.watchdog is not None:
            snap["watchdog_tripped"] = bool(self.watchdog.tripped)
            snap["watchdog_ticks"] = self._watchdog_ticks
            try:
                snap["harness_force"] = float(
                    self._force_sensor.read(step=self._watchdog_ticks)
                )
            except Exception:  # noqa: BLE001
                snap["harness_force"] = None
        return snap

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

        self._build_safety_stack(deps, kw)
        self._build_veto_stack(deps, kw)

        self._frames = [self._capture_frame(deps)]
        self.phase = "running"
        return {
            "ok": True,
            **self._snapshot(),
            "note": "episode armed; call navila_navigate_step until done=true",
        }

    def _build_safety_stack(self, deps: dict, kw: dict) -> None:
        """Wire A's SafetyWatchdog + MockForceSensor + DecisionLogbook onto the
        freshly-built backend. No-op (watchdog stays None) if the safety stack
        didn't import, the backend can't be emergency-stopped, or watchdog=False
        was passed to start_episode."""
        self.watchdog = None
        self.logbook = None
        self._force_sensor = None
        self._watchdog_ticks = 0

        if not kw.get("watchdog", True):
            return
        if "SafetyWatchdog" not in deps:
            return
        if not hasattr(self.backend, "emergency_stop"):
            return

        self._force_sensor = deps["MockForceSensor"]()
        for ev in self._force_events:
            self._force_sensor.schedule_drop(**ev)
        self.logbook = deps["DecisionLogbook"](sink=_logbook_sink)

        band = None
        lo, hi = kw.get("force_low"), kw.get("force_high")
        if lo is not None and hi is not None:
            band = deps["SafeForceBand"](low=float(lo), high=float(hi))
        self.watchdog = deps["SafetyWatchdog"](
            self.backend,
            band=band,
            debounce_ticks=int(kw.get("watchdog_debounce_ticks", 3)),
            force_reader=self._force_sensor.read,
            on_trip=self.logbook.record_watchdog_trip,
        )

    def _build_veto_stack(self, deps: dict, kw: dict) -> None:
        """Wire the Hazard Veto Agent + ScenarioInjector onto this episode.
        No-op (veto_agent stays None) if veto resolves False (kw['veto'], else
        NAVILA_BRIDGE_VETO env, default on) or the veto stack didn't import.

        MUST be called after _build_safety_stack, which unconditionally resets
        self.logbook=None at its own top -- reversing the order would drop a
        logbook built here. Reuses self.logbook if the watchdog already built
        one this episode (one merged STOP+VETO+CLEAR stream); otherwise builds
        its own, so veto works standalone with watchdog=False too.
        """
        self.veto_agent = None
        self._hazard_injector = None
        self._veto_client_error = None

        veto_kw = kw.get("veto")
        veto_enabled = (
            _env_flag("NAVILA_BRIDGE_VETO", True) if veto_kw is None else bool(veto_kw)
        )
        if not veto_enabled:
            return
        if "HazardVetoAgent" not in deps:
            return

        self._hazard_injector = deps["ScenarioInjector"]()
        for ev in self._hazard_events:
            self._hazard_injector.schedule(**ev)

        if self.logbook is None:
            self.logbook = deps["DecisionLogbook"](sink=_logbook_sink)

        try:
            client = _make_veto_vision_client(kw.get("veto_client_kind"))
        except Exception as exc:  # noqa: BLE001 -- a bad/missing veto client must
            # degrade the episode to veto_agent=None, not crash navila_start_episode
            # (e.g. veto_client_kind="anthropic" with the 'anthropic' package not
            # installed). Recorded on the session so it's visible in the start_episode
            # response instead of silently vanishing -- see A's own flagged finding
            # that a missing-key/package failure here would otherwise look like the
            # dog just refusing to move for no obvious reason.
            self._veto_client_error = f"{type(exc).__name__}: {exc}"
            return
        self.veto_agent = deps["HazardVetoAgent"](
            client, on_decision=self.logbook.record_veto_decision
        )

    def _capture_frame(self, deps: dict):
        """One frame for the driver VLM + veto gate: a real OrcaLab camera
        capture when the backend can provide one (C2's camera-capture-only
        fallback, docs/PLAN.md -- currently OrcaLabMirrorBackend.capture_frame
        with NAVILA_BRIDGE_ORCA_CAMERA on), else the mock's 8x8 placeholder.
        A capture failure never blocks the loop -- capture_frame() itself
        returns None on any error and this just falls back to the placeholder
        for that one frame."""
        capture = getattr(self.backend, "capture_frame", None)
        if capture is not None:
            try:
                frame = capture()
            except Exception:  # noqa: BLE001 -- capture is best-effort
                frame = None
            if frame is not None:
                return frame
        return deps["placeholder_frame"]()

    def _veto_frame(self, deps: dict):
        """PIL frame for the current decision's veto check: self._frames[-1]
        (the same frame the driver VLM just reasoned over), converted to PIL,
        with any scheduled ScenarioInjector hazard composited onto a COPY.
        Never written back into self._frames -- the driver's own frame history
        stays the raw capture, untouched by this test-harness marker."""
        raw = self._frames[-1]
        frame = raw if hasattr(raw, "convert") else deps["Image"].fromarray(raw).convert("RGB")
        if self._hazard_injector is not None:
            frame = self._hazard_injector.inject(frame, step=self.decision_index)
        return frame

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

        # WAYPOINT_STOP_OVERRIDE precedence (docs/PLAN.md, "C" item 2): D's
        # runner.py forward-nudge reflex (in NaVILA-Orca's NavigationRunner, a
        # separate CLI-driven loop this bridge doesn't call) forces one step of
        # forward motion when the VLM stops prematurely mid-waypoint, so a
        # frozen camera doesn't deadlock the VLM into "stop" forever. That must
        # NEVER override a real safety stop -- a watchdog trip or a veto is not
        # a "prematurely confused VLM," it's a legitimate reason to actually
        # stop. This flag is this bridge's per-step record of "did a safety
        # system end this step," reset every call, so a future port of the
        # override reflex into this loop has a ready-made suppression check
        # (`if session.stop_override_suppressed: don't nudge`). No consumer of
        # it exists in this file yet -- the per-step bridge has no waypoint
        # staging/forward-nudge logic of its own to suppress.
        self.stop_override_suppressed = False

        # An out-of-band emergency_stop (Stage 3 watchdog) latches this flag.
        if getattr(self.backend, "interrupted", False):
            self.phase = "stopped"
            self.termination_reason = "emergency_stop"
            self.stop_override_suppressed = True
            return {
                "ok": True,
                **self._snapshot(),
                "action": "stop",
                "note": "backend was interrupted before this step",
            }

        self._frames.append(self._capture_frame(deps))
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

        action_text = _action_text(cmd)

        # Hazard Veto Agent (Stage 3 differentiator): one tactical vision check
        # per decision, gating this proposed motion before any physics runs.
        # Never runs inside the ~20Hz tick loop below -- that's the reactive
        # watchdog's job; this is the ~1Hz tactical layer.
        if self.veto_agent is not None:
            decision = self.veto_agent.assess(self._veto_frame(deps), instruction, action_text)
            if not decision.is_clear:
                self.phase = "done"
                self.termination_reason = "veto"
                self.last_action = "stop"
                self.stop_override_suppressed = True
                return {
                    "ok": True,
                    **self._snapshot(),
                    "action": "stop",
                    "raw_vlm_text": raw,
                    "veto_reason": decision.reason,
                    "note": f"Hazard Veto Agent vetoed the proposed action: {decision.reason}",
                }

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
            # Reactive safety layer: poll harness force at physics cadence. A trip
            # calls backend.emergency_stop() itself (latching interrupted), so we
            # just re-check the flag and bail -- same path as an out-of-band stop.
            if self.watchdog is not None:
                self.watchdog.tick()
                self._watchdog_ticks += 1
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
        self._frames.append(self._capture_frame(deps))
        self.last_action = action_text

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
            self.stop_override_suppressed = True
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
        # Record orchestrator-initiated stops in the same log as watchdog trips,
        # so navila_get_logbook is a complete account of every stop.
        deps = _load_perstep()
        if self.logbook is not None and "WatchdogEvent" in deps:
            self.logbook.record_watchdog_trip(
                deps["WatchdogEvent"](
                    step=self._watchdog_ticks,
                    force=-1.0,
                    reason="orchestrator-initiated emergency stop (navila_emergency_stop)",
                )
            )
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

    # -- safety layer ----------------------------------------------------------
    def inject_force_drop(
        self, at_step: int, duration_steps: int = 20, value: float = 0.0
    ) -> dict:
        """Schedule a harness-force drop for the Safety Watchdog to catch. Step
        units are watchdog ticks (== physics steps once a step chunk is running),
        counted from episode start. duration_steps must exceed the watchdog's
        debounce (default 3) or the trip won't latch."""
        try:
            at_step = int(at_step)
            duration_steps = int(duration_steps)
            value = float(value)
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": f"bad argument: {exc}"}
        if at_step < 0 or duration_steps < 1:
            return {"ok": False, "error": "at_step >= 0 and duration_steps >= 1 required"}

        ev = {"at_step": at_step, "duration_steps": duration_steps, "value": value}
        self._force_events.append(ev)
        applied = self._force_sensor is not None
        if applied:
            self._force_sensor.schedule_drop(**ev)
        return {
            "ok": True,
            "scheduled": ev,
            "applied_to_live_sensor": applied,
            "all_scheduled": list(self._force_events),
            "note": (
                None if applied
                else "no active episode; will apply on the next navila_start_episode"
            ),
        }

    def clear_force_drops(self) -> dict:
        """Forget every scheduled force drop, including on the live sensor -- the
        harness reads nominal again from the next tick. Does not un-trip an
        already-fired watchdog (use navila_clear_stop for that)."""
        n = len(self._force_events)
        self._force_events.clear()
        live = self._force_sensor is not None
        if live:
            self._force_sensor._events.clear()
        return {
            "ok": True,
            "cleared": n,
            "applied_to_live_sensor": live,
            **self._snapshot(),
        }

    def inject_hazard(
        self, at_step: int, duration_steps: int = 1, label: str = "HAZARD"
    ) -> dict:
        """Schedule a ScenarioInjector hazard marker for the Hazard Veto Agent
        to catch. Step units are DECISION INDICES (navila_navigate_step calls,
        1 = the episode's first decision) -- NOT physics ticks like
        inject_force_drop, because the veto agent runs once per decision, not
        once per tick."""
        try:
            at_step = int(at_step)
            duration_steps = int(duration_steps)
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": f"bad argument: {exc}"}
        if at_step < 0 or duration_steps < 1:
            return {"ok": False, "error": "at_step >= 0 and duration_steps >= 1 required"}
        if not label:
            return {"ok": False, "error": "label must be non-empty"}

        ev = {"at_step": at_step, "duration_steps": duration_steps, "label": label}
        self._hazard_events.append(ev)
        applied = self._hazard_injector is not None
        if applied:
            self._hazard_injector.schedule(**ev)
        return {
            "ok": True,
            "scheduled": ev,
            "applied_to_live_injector": applied,
            "all_scheduled": list(self._hazard_events),
            "note": (
                None if applied
                else "no active episode / veto disabled; will apply on the next "
                "navila_start_episode with veto enabled"
            ),
        }

    def clear_hazards(self) -> dict:
        """Forget every scheduled hazard injection, including on the live
        ScenarioInjector -- frames read clean again from the next decision.
        Does not un-end an episode already finished with
        termination_reason='veto'."""
        n = len(self._hazard_events)
        self._hazard_events.clear()
        live = self._hazard_injector is not None
        if live:
            self._hazard_injector._events.clear()
        return {
            "ok": True,
            "cleared": n,
            "applied_to_live_injector": live,
            **self._snapshot(),
        }

    def clear_stop(self) -> dict:
        """Un-latch an emergency stop so the loop can resume from the current pose
        (the 'hazard cleared, dog proceeds' demo beat). Does NOT teleport -- that's
        navila_reset_episode."""
        if self.backend is None:
            return {"ok": False, "error": "no active episode"}
        cleared = []
        if getattr(self.backend, "interrupted", False):
            try:
                self.backend.interrupted = False
                cleared.append("backend.interrupted")
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"backend interrupt not clearable: {exc!r}"}
        if self.watchdog is not None and self.watchdog.tripped:
            self.watchdog.reset()
            cleared.append("watchdog latch")
        if self.phase == "stopped":
            self.phase = "running"
            self.termination_reason = None
            cleared.append("phase -> running")
        return {"ok": True, "cleared": cleared, **self._snapshot()}

    def get_logbook(self) -> dict:
        """The merged Safety Watchdog + Hazard Veto decision log for this episode."""
        if self.logbook is None:
            deps = _load_perstep()
            reason = (
                deps.get("safety_error")
                or deps.get("veto_error")
                or "no active episode / watchdog+veto disabled"
            )
            return {"ok": True, "entries": [], "text": "", "note": reason}
        entries = [
            {
                "timestamp_s": e.timestamp_s,
                "source": e.source,
                "kind": e.kind,
                "reason": e.reason,
            }
            for e in self.logbook.entries()
        ]
        return {"ok": True, "entries": entries, "text": self.logbook.dump()}


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
    watchdog: bool = True,
    watchdog_debounce_ticks: int = 3,
    force_low: float | None = None,
    force_high: float | None = None,
    veto: bool | None = None,
    veto_client_kind: str | None = None,
) -> dict:
    """Arm a per-step navigation episode. After this, call navila_navigate_step
    repeatedly until it returns done=true.

    instruction: natural-language navigation goal for NaVILA.
    goal_x / goal_y: optional world-frame target; when set, the episode ends with
        termination_reason 'goal_reached' once the robot is within goal_radius (m).
    max_decisions / max_control_steps: safety caps (0 = unlimited).
    backend_kind: 'mock' (default), 'mjlab', 'orcalab' (mjlab root pose mirrored
        into the OrcaLab GUI), 'orcalab-mock' (planar root pose mirrored into the
        GUI, no GPU), or 'orcalab-render' (real MJWarp gait with full articulated
        qpos pushed through OrcaLabRenderBridge). The root-only mirror kinds fall
        back to headless if the edit service on :50151 isn't reachable; the
        articulated renderer fails closed if OrcaLab is unavailable.
        vlm_kind: 'mock' (default) or 'tcp'.
    vlm_script: ';'-separated action phrases for the mock VLM, e.g.
        "move forward by 75 cm; turn left by 30 degrees; stop".
    vlm_timeout_s: per-decision socket timeout for vlm_kind='tcp' (default: the
        tested 120s whole-episode client default -- shorten this once real
        per-decision GPU latency is measured for a tighter live-loop budget).
        Ignored for vlm_kind='mock'. Any timeout/connection failure here
        degrades to a safe STOP (termination_reason='vlm_error'), never a hang
        or a crashed tool call.
    watchdog: attach A's SafetyWatchdog (harness-force reactive e-stop). Polls a
        MockForceSensor once per physics step; an out-of-band reading for
        watchdog_debounce_ticks consecutive ticks trips backend.emergency_stop()
        and ends the step with termination_reason 'emergency_stop'. Schedule the
        fault with navila_inject_force_drop; read the outcome with navila_get_logbook.
    force_low / force_high: safe harness-force band in newtons (default 20-80,
        nominal mock reading is 45). Pass both to override.
    veto: attach the Hazard Veto Agent (tactical, ~1Hz vision gate) via a
        VetoVisionClient (default: a self-contained stub that VETOes when
        ScenarioInjector's red hazard marker is present in the current frame --
        no API key needed). Omit to defer to NAVILA_BRIDGE_VETO (default on).
        A VETO ends the step with termination_reason='veto' and skips the
        motion chunk entirely; schedule the test hazard with
        navila_inject_hazard, read the outcome with navila_get_logbook.
    veto_client_kind: which VetoVisionClient backs the gate -- 'stub' (default,
        free, no API key, pixel-color detection) or 'anthropic' (one real
        Claude vision call per decision, model claude-haiku-4-5, needs
        ANTHROPIC_API_KEY and `pip install -e "NaVILA-Orca[veto]"` -- costs
        money and adds latency, so it is opt-in, never the default). Also
        settable via NAVILA_BRIDGE_VETO_CLIENT. A construction failure (e.g.
        the anthropic package missing) degrades the episode to veto_agent
        disabled rather than failing this call -- check the response's
        veto_enabled / veto_client_error fields.
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
            watchdog=watchdog,
            watchdog_debounce_ticks=watchdog_debounce_ticks,
            force_low=force_low,
            force_high=force_high,
            veto=veto,
            veto_client_kind=veto_client_kind,
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
    'truncated', 'emergency_stop', 'parse_error', 'veto') and stop calling this.

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


@mcp.tool()
def navila_inject_force_drop(
    at_step: int, duration_steps: int = 20, value: float = 0.0
) -> dict:
    """Schedule a harness-force drop the Safety Watchdog will catch (the disclosed
    'force drops to zero' fault-injection scenario).

    at_step: watchdog tick to start the drop at -- ticks == physics steps, counted
        from episode start. duration_steps: how long it lasts (must exceed the
        watchdog debounce, default 3, or the trip won't latch). value: the reported
        force during the window (0.0 = full harness-force loss).

    Callable before or during an episode; scheduled drops persist across
    navila_reset_episode until navila_clear_force_drops. When the drop fires the
    running navila_navigate_step ends with termination_reason 'emergency_stop';
    see navila_get_logbook for the timestamped reason.
    """
    return _jsonable(_SESSION.inject_force_drop(at_step, duration_steps, value))


@mcp.tool()
def navila_clear_force_drops() -> dict:
    """Forget every scheduled harness-force drop, on the live sensor too -- the
    harness reads nominal again from the next tick (the 'hazard cleared' beat).
    Does not un-trip an already-fired watchdog; pair with navila_clear_stop."""
    return _jsonable(_SESSION.clear_force_drops())


@mcp.tool()
def navila_inject_hazard(
    at_step: int, duration_steps: int = 1, label: str = "HAZARD"
) -> dict:
    """Schedule a ScenarioInjector hazard marker (red bar + label composited onto
    the frame) the Hazard Veto Agent will catch -- the disclosed automated-test
    fault-injection scenario for the differentiator gate.

    at_step: decision index to start the hazard at -- counts navila_navigate_step
        calls (1 = the episode's first decision), NOT physics ticks (contrast
        navila_inject_force_drop, which counts watchdog/physics ticks).
    duration_steps: how many consecutive decisions carry the marker.
    label: text drawn on the marker bar (cosmetic; detection is by pixel color).

    Callable before or during an episode; scheduled hazards persist across
    navila_reset_episode until navila_clear_hazards. When active and veto is
    enabled, the running navila_navigate_step ends with termination_reason
    'veto' and the motion chunk never executes; see navila_get_logbook for the
    reason.
    """
    return _jsonable(_SESSION.inject_hazard(at_step, duration_steps, label))


@mcp.tool()
def navila_clear_hazards() -> dict:
    """Forget every scheduled hazard injection, on the live ScenarioInjector too
    -- frames read clean again from the next decision. Does not un-end an
    episode already finished with termination_reason 'veto'; call
    navila_continue_episode or navila_reset_episode to proceed."""
    return _jsonable(_SESSION.clear_hazards())


def _trigger_scene_hazard_impl(
    actor_name: str, x: float, y: float, z: float, yaw_deg: float = 0.0
) -> dict:
    if not actor_name or not actor_name.strip():
        return {"ok": False, "error": "actor_name must be a non-empty string"}
    deps = _load_perstep()
    if "error" in deps:
        return {"ok": False, "error": deps["error"]}

    actor_name = actor_name.strip()
    yaw = math.radians(float(yaw_deg))
    rotation_wxyz = (math.cos(yaw / 2.0), 0.0, 0.0, math.sin(yaw / 2.0))
    try:
        deps["trigger_scene_hazard"](
            actor_name, (float(x), float(y), float(z)), rotation_wxyz
        )
    except Exception as exc:  # noqa: BLE001 -- surface any connection/write failure
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "note": "is the OrcaLab GUI + edit service (port 50151) running?",
        }
    return {
        "ok": True,
        "actor_name": actor_name,
        "position": {"x": float(x), "y": float(y), "z": float(z)},
        "yaw_deg": float(yaw_deg),
    }


@mcp.tool()
def navila_trigger_scene_hazard(
    actor_name: str, x: float, y: float, z: float, yaw_deg: float = 0.0
) -> dict:
    """Move an existing OrcaLab scene actor to a world pose -- the visibly-real,
    in-scene hazard trigger for the live demo (docs/PLAN.md 'C' item 5), as
    opposed to navila_inject_hazard's ScenarioInjector frame-overlay (that one
    is the disclosed automated-test path, composited onto captured frames --
    this one actually moves something in the scene judges can see).

    actor_name must already exist in the loaded scene, e.g. one of D's
    street.json hazard cast: blue_hatchback_car_1, traffic_light_1..4,
    female_pedestrian_model_1..4, supine_human_model_1. x/y/z are world-frame
    meters, yaw_deg is heading in degrees.

    Independent of the per-step episode/backend -- works whether or not
    navila_start_episode has been called, and regardless of backend_kind.
    Requires the OrcaLab GUI + edit service (port 50151) to be up; returns
    ok=False with the error message (never raises) if it isn't.
    """
    return _jsonable(_trigger_scene_hazard_impl(actor_name, x, y, z, yaw_deg))


def _reset_scene_layout_impl(actor_names: "str | None" = None) -> dict:
    deps = _load_perstep()
    if "error" in deps:
        return {"ok": False, "error": deps["error"]}

    names = None
    if actor_names and actor_names.strip():
        names = [n.strip() for n in actor_names.split(";") if n.strip()]

    try:
        restored = deps["reset_scene_layout"](actor_names=names)
    except Exception as exc:  # noqa: BLE001 -- surface any connection/write/lookup failure
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "note": "is the OrcaLab GUI + edit service (port 50151) running, and did every "
            "requested actor_name exist in the authored layout?",
        }
    return {"ok": True, "restored_actors": restored, "count": len(restored)}


@mcp.tool()
def navila_reset_scene_layout(actor_names: str | None = None) -> dict:
    """Restore scene actors to their authored transform from street.json -- the
    "reset to authored layout" rehearsal/judges reset (docs/PLAN.md 'C' item 6).
    Batches every restore into a single edit-service write.

    actor_names: optional ';'-separated list to restore only those actors
    (e.g. after navila_trigger_scene_hazard moved blue_hatchback_car_1, pass
    "blue_hatchback_car_1" to put just it back). Omit to restore EVERY actor
    in the authored layout except the per-step loop's robot actor
    (NAVILA_BRIDGE_ORCA_ROBOT_ACTOR, default quadruped_robot_1) -- the robot
    is excluded by default because its pose belongs to the per-step episode's
    backend, not this scene file; use navila_reset_episode for that instead,
    or this desyncs from the backend's own state and gets overwritten by the
    next navila_navigate_step's pose mirror anyway.

    Independent of the per-step episode/backend, same as
    navila_trigger_scene_hazard. Requires the OrcaLab GUI + edit service
    (port 50151) to be up; returns ok=False with the error message (never
    raises) if it isn't, or if a named actor doesn't exist in the layout.
    """
    return _jsonable(_reset_scene_layout_impl(actor_names))


@mcp.tool()
def navila_clear_stop() -> dict:
    """Un-latch an emergency stop (watchdog trip or navila_emergency_stop) so the
    loop can resume from the current pose -- the 'hazard cleared, dog proceeds'
    demo beat. Does NOT teleport the robot; that's navila_reset_episode."""
    return _jsonable(_SESSION.clear_stop())


@mcp.tool()
def navila_get_logbook() -> dict:
    """Return the merged Safety Watchdog + Hazard Veto decision log for the current
    episode: a timestamped list of every emergency stop and every veto.
    This is the 'how do you know it's making good decisions' answer for Q&A."""
    return _jsonable(_SESSION.get_logbook())


if __name__ == "__main__":
    mcp.run()
