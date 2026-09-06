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
import threading
import time
from collections import deque
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
# NOTE: ORCALAB_PYTHON here is ONLY for navila_run_instruction's shell-out to
# run_orcalab_scene_locomotion.sh. The per-step tools + the live_monitor's cv2
# import run under whatever interpreter launched THIS MCP server -- per its
# `claude mcp` registration that's the 'orcalab' env, where GUI opencv-python
# 5.0.0.93 was installed 2026-09-05 (prebuilt manylinux wheel, links the runtime
# GTK/Qt already on the box) so navila_start_episode(live_monitor=true) can open
# the OpenCV ego window.
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
        spawn_camera_actor=bb.spawn_camera_actor,
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
    # The OpenCV ego "live monitor" (dog's-eye RGB + instruction/VLM panel) is
    # a Loop B / CLI feature (navila-orca run --live-monitor). Wiring it into
    # this per-step loop too is opt-in (NAVILA_BRIDGE_LIVE_MONITOR / the
    # live_monitor arg on navila_start_episode) and must never break an episode:
    # cv2 may be absent and there may be no DISPLAY. Import failure is recorded,
    # not raised.
    try:
        from navila_orca.live_monitor import LiveMonitorError, LiveNavigationMonitor

        _PERSTEP.update(
            LiveNavigationMonitor=LiveNavigationMonitor,
            LiveMonitorError=LiveMonitorError,
        )
    except Exception as exc:  # noqa: BLE001 -- monitor optional, surface the reason
        _PERSTEP["live_monitor_error"] = f"live monitor unavailable: {exc!r}"
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


def _cmd_text(cmd) -> str:
    """Body-frame velocity summary of a VelocityCommand, for the live feed."""
    if getattr(cmd, "stop", False):
        return "stop"
    return (
        f"vx={cmd.vx:.2f} m/s  vy={cmd.vy:.2f} m/s  "
        f"wz={cmd.wz:+.3f} rad/s  dur={cmd.duration_s:.2f} s"
    )


def _describe_frame(frame) -> str:
    """One-line description of the ego frame the driver/veto just reasoned over.

    Distinguishes a real OrcaLab camera capture from the mock's 8x8 black
    placeholder so the judges' feed shows whether the dog is actually 'seeing'
    the scene this step. No numpy import -- ndarray exposes .shape directly.
    """
    shape = getattr(frame, "shape", None)
    if shape is not None and len(shape) == 3:
        h, w = int(shape[0]), int(shape[1])
        if (h, w) == (8, 8):
            return "8x8 placeholder (no live OrcaLab camera this step)"
        return f"{w}x{h} RGB ego frame from the OrcaLab camera"
    size = getattr(frame, "size", None)  # PIL.Image -> (w, h)
    if size and len(size) == 2:
        return f"{int(size[0])}x{int(size[1])} RGB ego frame"
    return "ego frame"


class _MonitorFrame:
    """Minimal stand-in for contracts.RenderFrame -- the three attributes
    LiveNavigationMonitor.update() actually reads. Avoids importing the frozen
    dataclass (and its per-frame validating copy) just to show a preview."""

    __slots__ = ("rgb", "step_id", "sim_time_s")

    def __init__(self, rgb, step_id: int, sim_time_s: float) -> None:
        self.rgb = rgb
        self.step_id = int(step_id)
        self.sim_time_s = float(sim_time_s)


class _MonitorThread:
    """Runs a LiveNavigationMonitor on its OWN thread and pumps its window
    continuously.

    In Loop A there is no tight render loop -- control sits in the MCP stdio
    read between tool calls, so a window only touched during navigate_step()
    freezes and the desktop shows a "not responding / Force Quit or Wait"
    dialog. This thread fixes that: it owns the monitor, calls waitKey() every
    ~40ms so the window stays alive, and applies the latest frame handed to it
    via submit(). EVERY cv2 highgui call (namedWindow / imshow / waitKey /
    destroyWindow) happens on this one thread -- highgui is not cross-thread
    safe, so the tool handlers never touch cv2 directly.
    """

    def __init__(self, make_monitor) -> None:
        self._make_monitor = make_monitor
        self._lock = threading.Lock()
        self._pending = None  # (frame, kwargs) latest-wins
        self._stop = threading.Event()
        self._ready = threading.Event()
        self.error: "str | None" = None
        self._thread = threading.Thread(
            target=self._run, name="navila-live-monitor", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=8.0)

    def _run(self) -> None:
        try:
            mon = self._make_monitor()
        except Exception as exc:  # noqa: BLE001 -- cv2 missing / no DISPLAY / GUI build
            self.error = f"{type(exc).__name__}: {exc}"
            self._ready.set()
            return
        self._ready.set()
        try:
            while not self._stop.is_set():
                with self._lock:
                    item, self._pending = self._pending, None
                if item is not None:
                    frame, kwargs = item
                    try:
                        mon.update(frame, **kwargs)
                    except Exception as exc:  # noqa: BLE001
                        self.error = f"update failed: {type(exc).__name__}: {exc}"
                        break
                else:
                    try:
                        mon.pump()
                    except Exception:  # noqa: BLE001 -- keep pumping regardless
                        pass
                self._stop.wait(0.04)
        finally:
            try:
                mon.close()
            except Exception:  # noqa: BLE001
                pass

    def ok(self) -> bool:
        return self._thread.is_alive() and self.error is None

    def submit(self, frame, **kwargs) -> None:
        with self._lock:
            self._pending = (frame, kwargs)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3.0)


class _LiveStatusFeed:
    """Judge-facing running commentary of the per-step loop.

    Purely a formatter over data that already flows through navigate_step() and
    A's DecisionLogbook -- it makes ZERO model calls and never touches physics.
    It exists because Loop A's stdout is the MCP stdio transport, so the loop
    can't just print(): instead every line is buffered here (and mirrored to
    stderr / the MCP server log) and pulled by the navila_get_live_status tool,
    which the Orchestrator polls between steps and echoes to the audience.

    What it surfaces:
      * a per-decision trace -- what the Orchestrator asked for, what the ego
        frame was, what NaVILA decided, what the robot was commanded, how far it
        moved;
      * a HIGHLY VISIBLE banner the instant the SafetyWatchdog trips or the
        HazardVetoAgent issues a VETO, carrying that exact reason
        (``[VETO: red signal detected]``);
      * a one-line ``Status: CLEAR - Navigating`` heartbeat every ~3s while an
        episode is running and nothing is wrong, so the loop is visibly alive
        even during a slow NaVILA inference (a daemon thread covers the gaps
        between navigate_step calls).
    """

    _HEARTBEAT_INTERVAL_S = 3.0
    _MAX_LINES = 400

    def __init__(self, *, clock=time.time, mirror=True) -> None:
        self._clock = clock
        self._mirror = mirror
        self._lock = threading.RLock()
        self._lines: "deque[dict]" = deque(maxlen=self._MAX_LINES)
        self._seq = 0
        self._last_line_s = 0.0
        self._running = False
        self._active_alert: "str | None" = None
        self._thread: "threading.Thread | None" = None

    # -- line buffer --------------------------------------------------------
    def _emit(self, text: str, *, kind: str = "info") -> None:
        clock = time.strftime("%H:%M:%S", time.localtime(self._clock()))
        with self._lock:
            self._seq += 1
            seq = self._seq
            for chunk in str(text).splitlines() or [""]:
                self._lines.append(
                    {"seq": seq, "t": clock, "kind": kind, "text": chunk}
                )
            self._last_line_s = self._clock()
        if self._mirror:
            for chunk in str(text).splitlines() or [""]:
                print(f"[{clock}] {chunk}", file=sys.stderr, flush=True)

    def _banner(self, core: str, *, kind: str) -> None:
        rule = "!!! " + "=" * 60
        self._emit(f"{rule}\n!!! {core}\n{rule}", kind=kind)

    # -- lifecycle signals from the session -------------------------------
    def set_running(self, running: bool) -> None:
        with self._lock:
            self._running = bool(running)
        if running and self._thread is None:
            self._start_thread()

    def clear_alert(self) -> None:
        with self._lock:
            self._active_alert = None

    def note_episode_start(
        self, instruction: str, *, backend_kind, watchdog_on, veto_on, monitor
    ) -> None:
        self.clear_alert()
        self._emit("", kind="info")
        self._emit(
            f"=== EPISODE START === instruction: {instruction!r}", kind="episode"
        )
        self._emit(
            "    backend=%s  watchdog=%s  hazard-veto=%s  live-monitor=%s"
            % (
                backend_kind or "mock",
                "on" if watchdog_on else "off",
                "on" if veto_on else "off",
                monitor,
            ),
            kind="episode",
        )
        self.set_running(True)

    def note_episode_end(self, reason: "str | None") -> None:
        self.set_running(False)
        self._emit(f"=== EPISODE END === termination_reason: {reason}", kind="episode")
        self.clear_alert()

    def note_instruction_change(self, instruction: str) -> None:
        self.clear_alert()
        self._emit(
            f"--- NEW INSTRUCTION (pose kept) --- {instruction!r}", kind="episode"
        )
        self.set_running(True)

    # -- per-decision trace ---------------------------------------------
    def note_orchestrator_step(
        self, *, decision_index: int, instruction: str, frame_desc: str
    ) -> None:
        self._emit(
            f"-- decision {decision_index} " + "-" * 40, kind="decision"
        )
        self._emit(
            f"   Orchestrator -> NaVILA: {instruction!r}", kind="decision"
        )
        self._emit(f"   perception: {frame_desc}", kind="decision")

    def note_driver_decision(
        self, *, decision_index: int, raw_vlm_text: str, command_text: str
    ) -> None:
        self._emit(
            f"   NaVILA decided: {raw_vlm_text!r}", kind="decision"
        )
        self._emit(f"   robot command: {command_text}", kind="decision")

    def note_driver_fault(self, what: str, detail: str) -> None:
        self._banner(f"[DRIVER FAULT: {what} -> safe STOP] {detail}", kind="alert")
        self.set_running(False)

    def note_step_result(
        self,
        *,
        decision_index: int,
        moved_m: float,
        yaw_delta_deg: float,
        executed_ticks: int,
        done: bool,
        termination_reason: "str | None",
    ) -> None:
        self._emit(
            f"   result: moved {moved_m:.2f} m, yaw {yaw_delta_deg:+.1f} deg "
            f"over {executed_ticks} ticks",
            kind="decision",
        )
        if done:
            self.set_running(False)
            self._emit(
                f"   decision {decision_index} ended the episode "
                f"({termination_reason})",
                kind="decision",
            )
        else:
            self.maybe_heartbeat(running=True, force=True)

    # -- safety / veto callbacks (fired alongside the DecisionLogbook) ---
    def on_watchdog_trip(self, event) -> None:
        reason = event if isinstance(event, str) else getattr(event, "reason", str(event))
        with self._lock:
            self._active_alert = f"[EMERGENCY STOP: {reason}]"
        self._banner(f"[EMERGENCY STOP: {reason}]", kind="alert")
        self.set_running(False)

    def on_veto_decision(self, decision) -> None:
        if getattr(decision, "is_clear", True):
            return
        reason = getattr(decision, "reason", "") or "no reason given"
        with self._lock:
            self._active_alert = f"[VETO: {reason}]"
        self._banner(f"[VETO: {reason}]", kind="alert")
        self.set_running(False)

    # -- heartbeat -----------------------------------------------------
    def maybe_heartbeat(self, *, running: bool, force: bool = False) -> None:
        if not running:
            return
        now = self._clock()
        with self._lock:
            if self._active_alert is not None:
                return
            if not force and now - self._last_line_s < self._HEARTBEAT_INTERVAL_S:
                return
        self._emit("Status: CLEAR - Navigating", kind="heartbeat")

    def _start_thread(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._heartbeat_loop,
                name="navila-live-status",
                daemon=True,
            )
        self._thread.start()

    def _heartbeat_loop(self) -> None:
        while True:  # daemon -- dies with the interpreter
            time.sleep(1.0)
            try:
                self.maybe_heartbeat(running=self._running)
            except Exception:  # noqa: BLE001 -- a formatter must never crash the server
                pass

    # -- read side (navila_get_live_status) ---------------------------
    def status_line(self) -> str:
        with self._lock:
            if self._active_alert is not None:
                return f"Status: HALTED - {self._active_alert}"
            if self._running:
                return "Status: CLEAR - Navigating"
            return "Status: IDLE - no active episode"

    def snapshot(self, *, since_seq: int = 0, max_lines: int = 40) -> dict:
        with self._lock:
            rows = [r for r in self._lines if r["seq"] > since_seq]
            if max_lines and len(rows) > max_lines:
                rows = rows[-max_lines:]
            new_lines = [f"[{r['t']}] {r['text']}" for r in rows]
            next_seq = self._seq
            alert = self._active_alert
        return {
            "status_line": self.status_line(),
            "active_alert": alert,
            "new_lines": new_lines,
            "next_seq": next_seq,
        }


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
        # Judge-facing live commentary. One feed for the whole session lifetime
        # (scrollback survives reset), preserved across _reset_fields the same
        # way _force_events / _hazard_events are.
        self._live_status = getattr(self, "_live_status", None) or _LiveStatusFeed()
        # OpenCV ego "live monitor". The _MonitorThread (window + its own pump
        # loop) is SESSION-scoped, preserved across _reset_fields and reused
        # between episodes -- creating/destroying the cv2+Qt window per episode
        # triggers "Timers cannot be stopped from another thread" spam and a
        # visible flash. Per-episode state (error / disabled) still resets.
        self._monitor_thread = getattr(self, "_monitor_thread", None)
        self._live_monitor_error = None
        self._live_monitor_disabled = False

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
            "live_monitor": self._monitor_state_str(),
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
        if self.phase != "idle":
            self._live_status.note_episode_end(self.termination_reason)
        else:
            self._live_status.set_running(False)
        # NOTE: the monitor thread is session-scoped -- deliberately NOT stopped
        # here. It keeps pumping (showing the last frame) between episodes so the
        # window neither freezes nor flashes. It's a daemon thread; it dies with
        # the process. An explicit live_monitor=false on the next start_episode
        # is what tears it down.
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
            # Real ego-camera capture (C2's fallback): only the OrcaLab mirror
            # backends take a `camera` kwarg. Passing it to mock/mjlab would be a
            # TypeError, and orcalab-render streams its own ego camera already,
            # so gate on the two kinds that actually accept it.
            backend_kwargs: dict = {}
            _kind = (backend_kind or os.environ.get("NAVILA_BRIDGE_BACKEND", "mock")).lower()
            if _kind in ("orcalab", "orcalab-mock"):
                _cam = kw.get("camera")
                _cam_env = os.environ.get("NAVILA_BRIDGE_ORCA_CAMERA")
                _, _mon_on = self._live_monitor_request(kw)
                if _cam is not None:
                    backend_kwargs["camera"] = bool(_cam)
                elif _cam_env is None and _mon_on:
                    # The live monitor is on and this backend CAN capture -> give
                    # it a real dog's-eye frame instead of the 8x8 placeholder.
                    # Best-effort, same contract as capture itself: a missing
                    # camera actor / unreachable edit service just falls back to
                    # the placeholder with one stderr line, never an error.
                    backend_kwargs["camera"] = True
                    self._live_status._emit(
                        "live monitor on + orcalab backend -> enabling real "
                        "ego-camera capture (camera=true). Needs a "
                        "'mujococamera1080' actor in the loaded scene; falls "
                        "back to the 8x8 placeholder if it's missing.",
                        kind="info",
                    )
                if kw.get("camera_name"):
                    backend_kwargs["camera_name"] = str(kw["camera_name"])
            self.backend = deps["make_backend"](backend_kind, **backend_kwargs)
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
        self._build_live_monitor(deps, kw)

        self._frames = [self._capture_frame(deps)]
        self.phase = "running"
        self._live_status.note_episode_start(
            self.instruction,
            backend_kind=backend_kind,
            watchdog_on=self.watchdog is not None,
            veto_on=self.veto_agent is not None,
            monitor=self._monitor_state_str(),
        )
        self._monitor_update(status="ready")
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
            on_trip=self._on_watchdog_trip,
        )

    def _on_watchdog_trip(self, event) -> None:
        """SafetyWatchdog on_trip: record in A's DecisionLogbook AND raise the
        highly-visible banner on the judge feed, in that order."""
        if self.logbook is not None:
            self.logbook.record_watchdog_trip(event)
        self._live_status.on_watchdog_trip(event)

    def _on_veto_decision(self, decision):
        """HazardVetoAgent on_decision: record in A's DecisionLogbook (VETO
        always, CLEAR only if log_clear) AND, on a VETO, raise the banner."""
        entry = None
        if self.logbook is not None:
            entry = self.logbook.record_veto_decision(decision)
        self._live_status.on_veto_decision(decision)
        return entry

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
            client, on_decision=self._on_veto_decision
        )

    @staticmethod
    def _live_monitor_request(kw: dict) -> "tuple[bool, bool]":
        """(explicit, enabled) tri-state for the live monitor:
          * arg + env both unset      -> (False, True)  best-effort default
          * NAVILA_BRIDGE_LIVE_MONITOR -> (True, <flag>) explicit via env
          * live_monitor= arg          -> (True, bool(arg)) explicit via arg
        A failure is only announced loudly when explicit is True."""
        want = kw.get("live_monitor")
        env_raw = os.environ.get("NAVILA_BRIDGE_LIVE_MONITOR")
        if want is None and env_raw is None:
            return False, True
        if want is None:
            return True, _env_flag("NAVILA_BRIDGE_LIVE_MONITOR", False)
        return True, bool(want)

    def _build_live_monitor(self, deps: dict, kw: dict) -> None:
        """Ensure the session's OpenCV ego "live monitor" thread is running for
        this episode (see _MonitorThread -- window + its own pump loop, so it
        stays responsive between MCP tool calls). The thread is reused across
        episodes; only an explicit live_monitor=false tears it down. Best-effort
        by default (see _live_monitor_request). Never breaks the episode.
        """
        self._live_monitor_error = None
        self._live_monitor_disabled = False

        explicit, enabled = self._live_monitor_request(kw)
        if not enabled:
            if self._monitor_thread is not None:
                try:
                    self._monitor_thread.stop()
                finally:
                    self._monitor_thread = None
            return

        if self._monitor_thread is not None and self._monitor_thread.ok():
            return  # reuse the window from a previous episode

        def _degrade(reason: str) -> None:
            self._live_monitor_error = reason
            self._monitor_thread = None
            if explicit:
                self._live_status._emit(
                    f"live monitor requested but could not open ({reason}); "
                    "the episode still runs -- run navila_live_monitor_selftest "
                    "to debug",
                    kind="warn",
                )

        if "LiveNavigationMonitor" not in deps:
            _degrade(deps.get("live_monitor_error", "live monitor module unavailable"))
            return
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            _degrade("no DISPLAY / WAYLAND_DISPLAY in the MCP server's environment")
            return
        try:
            mt = _MonitorThread(
                lambda: deps["LiveNavigationMonitor"](
                    window_name="guide dog -- live monitor"
                )
            )
        except Exception as exc:  # noqa: BLE001
            _degrade(f"{type(exc).__name__}: {exc}")
            return
        if not mt.ok():
            _degrade(mt.error or "monitor thread failed to start")
            mt.stop()
            return
        self._monitor_thread = mt

    def _monitor_state_str(self) -> str:
        mt = self._monitor_thread
        if mt is not None and mt.ok() and not self._live_monitor_disabled:
            return "on"
        if self._live_monitor_disabled:
            return f"disabled mid-episode: {self._live_monitor_error}"
        if self._live_monitor_error is not None:
            return f"unavailable: {self._live_monitor_error}"
        return "off"

    def _monitor_update(
        self,
        *,
        status: str,
        vlm_output: "str | None" = None,
        command: "str | None" = None,
        chunk_result: "str | None" = None,
    ) -> None:
        """Hand the latest frame to the live monitor thread. No-op unless the
        monitor is running; if its thread has died, disable it for the rest of
        the episode rather than propagating into navila_navigate_step."""
        mt = self._monitor_thread
        if (
            mt is None
            or self._live_monitor_disabled
            or self._state is None
            or not self._frames
        ):
            return
        if not mt.ok():
            self._live_monitor_disabled = True
            self._live_monitor_error = mt.error or "monitor thread stopped"
            self._live_status._emit(
                f"live monitor stopped ({self._live_monitor_error}); "
                "continuing without it",
                kind="warn",
            )
            return
        mt.submit(
            _MonitorFrame(
                self._frames[-1], self._state.step_id, self._state.sim_time_s
            ),
            instruction=self.instruction,
            vlm_output=vlm_output
            or (self.last_vlm_text or "Waiting for first VLM decision..."),
            command=command or (self.last_action or "none"),
            status=status,
            decision=self.decision_index,
            chunk_result=chunk_result or "none completed yet",
        )

    def get_live_status(self, *, since_seq: int = 0, max_lines: int = 40) -> dict:
        """Judge-facing feed for navila_get_live_status -- see that tool's
        docstring. Merges the running commentary with a tail of A's
        DecisionLogbook."""
        snap = self._live_status.snapshot(since_seq=since_seq, max_lines=max_lines)
        logbook_tail = []
        if self.logbook is not None:
            for e in self.logbook.entries()[-8:]:
                logbook_tail.append(
                    {
                        "timestamp_s": e.timestamp_s,
                        "source": e.source,
                        "kind": e.kind,
                        "reason": e.reason,
                    }
                )
        return {
            "ok": True,
            "phase": self.phase,
            "termination_reason": self.termination_reason,
            "status_line": snap["status_line"],
            "active_alert": snap["active_alert"],
            "new_lines": snap["new_lines"],
            "next_seq": snap["next_seq"],
            "live_monitor": self._monitor_state_str(),
            "logbook_tail": logbook_tail,
            "note": (
                "poll again with since_seq=next_seq for only new lines; show "
                "new_lines (and active_alert, loudly) to the audience"
            ),
        }

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
            self._live_status.set_running(False)
            self._monitor_update(status="halted: backend interrupted before this step")
            return {
                "ok": True,
                **self._snapshot(),
                "action": "stop",
                "note": "backend was interrupted before this step",
            }

        self._frames.append(self._capture_frame(deps))
        self._live_status.note_orchestrator_step(
            decision_index=self.decision_index + 1,
            instruction=instruction,
            frame_desc=_describe_frame(self._frames[-1]),
        )
        self._monitor_update(status="waiting for VLM response")
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
            self._live_status.note_driver_fault("VLM call failed", repr(exc))
            self._monitor_update(status="VLM call failed -> safe stop", command="stop")
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
            self._live_status.note_driver_fault(
                "unparseable VLM output", f"{raw!r}: {exc}"
            )
            self._monitor_update(
                status="unparseable VLM output -> safe stop",
                vlm_output=raw,
                command="stop",
            )
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
            self._live_status.note_driver_decision(
                decision_index=self.decision_index,
                raw_vlm_text=raw,
                command_text="stop",
            )
            self._live_status.note_step_result(
                decision_index=self.decision_index,
                moved_m=0.0,
                yaw_delta_deg=0.0,
                executed_ticks=0,
                done=True,
                termination_reason="stop",
            )
            self._monitor_update(
                status="NaVILA requested stop", vlm_output=raw, command="stop"
            )
            return {"ok": True, **self._snapshot(), "action": "stop", "raw_vlm_text": raw}

        action_text = _action_text(cmd)
        self._live_status.note_driver_decision(
            decision_index=self.decision_index,
            raw_vlm_text=raw,
            command_text=_cmd_text(cmd),
        )

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
                self._monitor_update(
                    status=f"VETOED: {decision.reason}",
                    vlm_output=raw,
                    command="(blocked by Hazard Veto Agent)",
                    chunk_result="zero physics executed",
                )
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

        self._live_status.note_step_result(
            decision_index=self.decision_index,
            moved_m=moved_m,
            yaw_delta_deg=yaw_delta_deg,
            executed_ticks=executed,
            done=done,
            termination_reason=self.termination_reason,
        )
        self._monitor_update(
            status=(
                "motion chunk completed"
                if not done
                else f"episode done: {self.termination_reason}"
            ),
            vlm_output=raw,
            command=_cmd_text(cmd),
            chunk_result=(
                f"moved {moved_m:.2f} m, yaw {yaw_delta_deg:+.1f} deg "
                f"over {executed}/{ticks} ticks"
            ),
        )

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
        reason = "orchestrator-initiated emergency stop (navila_emergency_stop)"
        if self.logbook is not None and "WatchdogEvent" in deps:
            self.logbook.record_watchdog_trip(
                deps["WatchdogEvent"](
                    step=self._watchdog_ticks,
                    force=-1.0,
                    reason=reason,
                )
            )
        self._live_status.on_watchdog_trip(reason)
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
        self._live_status.note_instruction_change(self.instruction)
        self._monitor_update(status="new instruction; pose kept")
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
        self._live_status.clear_alert()
        self._live_status._emit(
            f"--- harness force back to nominal ({n} scheduled drop(s) cleared) ---",
            kind="episode",
        )
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
        self._live_status.clear_alert()
        self._live_status._emit(
            f"--- hazard cleared ({n} scheduled injection(s) cleared) ---",
            kind="episode",
        )
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
        if cleared:
            self._live_status.clear_alert()
            self._live_status._emit(
                "--- stop cleared; dog proceeds --- " + ", ".join(cleared),
                kind="episode",
            )
            if self.phase == "running":
                self._live_status.set_running(True)
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


_SELFTEST_MONITOR = None  # keep a ref so the diagnostic window isn't GC-orphaned


def _live_monitor_selftest_impl(keep_open: bool = True) -> dict:
    """Open the OpenCV window right now with a synthetic frame and report exactly
    what happened -- the one call to run when 'the live monitor didn't appear'."""
    global _SELFTEST_MONITOR
    import importlib

    out: dict = {
        "ok": False,
        "opened": False,
        "display": os.environ.get("DISPLAY"),
        "wayland_display": os.environ.get("WAYLAND_DISPLAY"),
        "server_python": sys.executable,
        "env_NAVILA_BRIDGE_LIVE_MONITOR": os.environ.get("NAVILA_BRIDGE_LIVE_MONITOR"),
    }

    try:
        cv2 = importlib.import_module("cv2")
        out["cv2_version"] = getattr(cv2, "__version__", "?")
        out["cv2_file"] = getattr(cv2, "__file__", "?")
        out["cv2_build"] = (
            "headless (no GUI functions)"
            if "headless" in (out["cv2_file"] or "")
            or not hasattr(cv2, "namedWindow")
            else "gui"
        )
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"cannot import cv2: {type(exc).__name__}: {exc}"
        out["hint"] = (
            "pip install --only-binary=:all: opencv-python==5.0.0.93 into the env "
            f"that runs THIS server ({sys.executable})"
        )
        return out

    if not (out["display"] or out["wayland_display"]):
        out["error"] = "no DISPLAY / WAYLAND_DISPLAY in the MCP server's environment"
        out["hint"] = (
            "the MCP server was spawned without an X/Wayland display in its env; "
            "add DISPLAY (and XAUTHORITY) to the server's `env` in its `claude mcp` "
            "registration, or relaunch the Claude session from a graphical terminal"
        )
        return out

    deps = _load_perstep()
    if "LiveNavigationMonitor" not in deps:
        out["error"] = deps.get("live_monitor_error", "LiveNavigationMonitor import failed")
        return out

    try:
        import numpy as _np

        # Same dedicated-thread wrapper an episode uses, so the kept-open window
        # keeps pumping and the WM never shows a "not responding" dialog.
        if _SELFTEST_MONITOR is not None:
            try:
                _SELFTEST_MONITOR.stop()
            except Exception:  # noqa: BLE001
                pass
            _SELFTEST_MONITOR = None
        mt = _MonitorThread(
            lambda: deps["LiveNavigationMonitor"](
                window_name="guide dog -- live monitor (self-test)"
            )
        )
        if not mt.ok():
            raise RuntimeError(mt.error or "monitor thread failed to start")
        rng = _np.random.default_rng(0)
        for i in range(6):
            frame = type(
                "_F",
                (),
                {
                    "rgb": rng.integers(0, 255, (360, 480, 3), dtype=_np.uint8),
                    "step_id": i,
                    "sim_time_s": float(i) * 0.1,
                },
            )()
            mt.submit(
                frame,
                instruction="live monitor self-test",
                vlm_output="(synthetic frame -- no episode running)",
                command="none",
                status="SELF-TEST OK -- this window is what an episode will use",
                decision=i,
                chunk_result="n/a",
            )
            time.sleep(0.15)
        if not mt.ok():
            raise RuntimeError(mt.error or "monitor thread died during update")
        out["ok"] = True
        out["opened"] = True
        if keep_open:
            _SELFTEST_MONITOR = mt  # leave the (pumped) window up
            out["note"] = (
                "a window titled 'guide dog -- live monitor' should be visible now "
                "and stay responsive. If you see it, the pipeline works -- start an "
                "episode (live_monitor defaults on) and it refreshes with the dog's "
                "view. For a REAL ego frame (not the 8x8 placeholder) use "
                "backend_kind=orcalab-mock + camera=true with the OrcaLab GUI up "
                "and a 'mujococamera1080' actor in the scene."
            )
        else:
            mt.stop()
            out["note"] = "opened and closed cleanly"
    except Exception as exc:  # noqa: BLE001
        import traceback

        out["error"] = f"{type(exc).__name__}: {exc}"
        out["traceback"] = traceback.format_exc()[-1500:]
        out["hint"] = (
            "cv2 imported but the window call failed -- usually a headless cv2 "
            "build ('The function is not implemented. Rebuild ... with GTK+'), or "
            "a Qt/xcb plugin problem. Check cv2_build above."
        )
    return out


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
    live_monitor: bool | None = None,
    camera: bool | None = None,
    camera_name: str | None = None,
) -> dict:
    """Arm a per-step navigation episode, then call navila_navigate_step until
    done=true. Poll navila_get_live_status between steps for the judge feed.

    instruction: natural-language goal for NaVILA.
    goal_x/goal_y (+goal_radius m): optional world target -> ends 'goal_reached'.
    max_decisions/max_control_steps: caps (0 = unlimited).
    backend_kind: 'mock' (default, planar, headless), 'mjlab' (real physics,
        headless), 'orcalab'/'orcalab-mock' (root pose mirrored into the OrcaLab
        GUI; -mock = no GPU), 'orcalab-render' (real articulated gait in OrcaLab).
    vlm_kind: 'mock' (default) or 'tcp'. vlm_script: ';'-separated phrases for the
        mock VLM, e.g. "move forward by 75 cm; turn left by 30 degrees; stop".
    vlm_timeout_s: per-decision tcp socket timeout (default 120s; failure -> STOP).
    watchdog (+watchdog_debounce_ticks, force_low/force_high N): A's harness-force
        reactive e-stop. Fault-inject with navila_inject_force_drop.
    veto (+veto_client_kind 'stub'|'anthropic'): Hazard Veto Agent vision gate,
        default on. 'anthropic' = one real Claude vision call/decision (needs
        ANTHROPIC_API_KEY + the 'veto' extra); a bad client degrades to veto
        disabled (see veto_enabled/veto_client_error). Inject with
        navila_inject_hazard.
    live_monitor: OpenCV dog's-eye window (ego RGB + instruction/NaVILA/command
        panel), on its own thread so it stays responsive between tool calls.
        DEFAULT best-effort -- opens automatically when cv2 + a DISPLAY are
        present, skips quietly otherwise. true forces it (failure warns loudly;
        debug with navila_live_monitor_selftest); false suppresses/closes it.
        Also NAVILA_BRIDGE_LIVE_MONITOR. Response `live_monitor` = 'on'/'off'/why.
    camera (+camera_name, default 'mujococamera1080'): show a REAL ego frame
        instead of the 8x8 placeholder. AUTO-ENABLED when live_monitor is on and
        backend_kind is 'orcalab'/'orcalab-mock'; pass camera=false to opt out.
        Needs the OrcaLab GUI + edit service (:50151) up AND that camera actor
        already in the loaded scene -- it is NOT in street.json, add the
        prefabs/mujococamera1080 prefab once per scene load or capture falls back
        to the placeholder. = NAVILA_BRIDGE_ORCA_CAMERA=1; also feeds the veto
        agent a real frame.
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
            live_monitor=live_monitor,
            camera=camera,
            camera_name=camera_name,
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


def _spawn_camera_impl(
    actor_name: str = "mujococamera1080",
    asset_path: str = "prefabs/mujococamera1080",
    x: float = 0.1,
    y: float = 0.0,
    z: float = 0.5,
    replace: bool = False,
) -> dict:
    deps = _load_perstep()
    if "error" in deps:
        return {"ok": False, "error": deps["error"]}
    if "spawn_camera_actor" not in deps:
        return {"ok": False, "error": "spawn helper unavailable in this build"}
    if not actor_name or not actor_name.strip():
        return {"ok": False, "error": "actor_name must be a non-empty string"}
    try:
        info = deps["spawn_camera_actor"](
            actor_name.strip(),
            (asset_path or "prefabs/mujococamera1080").strip(),
            (float(x), float(y), float(z)),
            replace=bool(replace),
        )
    except Exception as exc:  # noqa: BLE001 -- surface connection / prefab / RPC failure
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "note": (
                "is the OrcaLab GUI + edit service (port 50151) up? "
                "'prefabs/mujococamera1080' is a built-in OrcaLab prefab -- if the "
                "add is refused, the OrcaLab build may not ship it under that path; "
                "try adding the camera prefab from the OrcaLab GUI instead."
            ),
        }
    return {"ok": True, **info}


@mcp.tool()
def navila_spawn_camera(
    actor_name: str = "mujococamera1080",
    asset_path: str = "prefabs/mujococamera1080",
    x: float = 0.1,
    y: float = 0.0,
    z: float = 0.5,
    replace: bool = False,
) -> dict:
    """Add the persistent MuJoCo ego-camera actor to the loaded OrcaLab scene so
    `camera` capture / the live monitor have something real to show.

    'prefabs/mujococamera1080' is a BUILT-IN OrcaLab prefab -- there is nothing
    to download or obtain; this instantiates it as a root actor named
    `actor_name` at mount offset (x, y, z) via the edit service (:50151), the
    same connection the pose mirror uses. Idempotent: a no-op if the actor is
    already there (pass replace=true to delete + re-add). The add is LIVE ONLY
    and is not written to the scene file -- rerun this after every fresh scene
    load, or bake the actor into the scene.

    Independent of any episode/backend. Requires the OrcaLab GUI + edit service
    up; returns ok=False with the error (never raises) otherwise. Once the actor
    exists, an episode with backend_kind 'orcalab'/'orcalab-mock' + camera on
    moves it onto the dog before each capture (camera-follow), so the ego view
    tracks the robot.
    """
    return _jsonable(_spawn_camera_impl(actor_name, asset_path, x, y, z, replace))


@mcp.tool()
def navila_clear_stop() -> dict:
    """Un-latch an emergency stop (watchdog trip or navila_emergency_stop) so the
    loop can resume from the current pose -- the 'hazard cleared, dog proceeds'
    demo beat. Does NOT teleport the robot; that's navila_reset_episode."""
    return _jsonable(_SESSION.clear_stop())


@mcp.tool()
def navila_live_monitor_selftest(keep_open: bool = True) -> dict:
    """Diagnose the OpenCV dog's-eye "live monitor" window in one call -- run this
    when a live_monitor=true episode showed no window.

    Opens the window immediately with a synthetic frame (no episode needed) and
    returns a report: cv2 version + whether it's the GUI or -headless build,
    DISPLAY / WAYLAND_DISPLAY seen by the server, which Python runs the server,
    and on failure the exact exception + a hint (headless cv2, missing DISPLAY,
    Qt plugin, ...). keep_open=true (default) leaves the window up so you can
    confirm it visually; false opens and closes it.
    """
    return _jsonable(_live_monitor_selftest_impl(keep_open=keep_open))


@mcp.tool()
def navila_get_logbook() -> dict:
    """Return the merged Safety Watchdog + Hazard Veto decision log for the current
    episode: a timestamped list of every emergency stop and every veto.
    This is the 'how do you know it's making good decisions' answer for Q&A."""
    return _jsonable(_SESSION.get_logbook())


@mcp.tool()
def navila_get_live_status(since_seq: int = 0, max_lines: int = 40) -> dict:
    """Judge-facing LIVE commentary of the per-step loop -- poll this between
    navila_navigate_step calls and read `new_lines` out to the audience.

    It is a pure formatter over data that already flows through
    navila_navigate_step and A's DecisionLogbook -- it makes NO extra model
    calls. What it carries:

      * a per-decision trace: what the Orchestrator asked NaVILA for, whether
        the ego frame was a real OrcaLab capture or the 8x8 placeholder, what
        NaVILA decided, the exact velocity command sent to the robot, and how
        far the dog actually moved;
      * the instant the Safety Watchdog trips or the Hazard Veto Agent issues a
        VETO, a loud banner carrying that exact reason, e.g.
        `[VETO: red pedestrian signal detected]` -- also surfaced on its own as
        `active_alert`;
      * a `Status: CLEAR - Navigating` heartbeat roughly every 3s while an
        episode is running and nothing is wrong, so the loop is visibly alive
        even during a slow NaVILA inference.

    since_seq: pass the `next_seq` from your previous call to get only what's
        new since then (0 = from the start of the buffer).
    max_lines: cap on how many lines to return (newest kept).

    Returns: status_line, active_alert, new_lines (list of formatted strings),
    next_seq, live_monitor (state of the optional OpenCV window), and
    logbook_tail (the last few DecisionLogbook entries, structured).
    """
    return _jsonable(
        _SESSION.get_live_status(since_seq=int(since_seq), max_lines=int(max_lines))
    )


if __name__ == "__main__":
    mcp.run()
