"""Backend + VLM seams for the per-step MCP bridge (Stage 1 of PLAN.md).

`navila_bridge.py`'s per-step tools (`navila_navigate_step`, `navila_get_status`,
`navila_emergency_stop`) depend ONLY on the two Protocols defined here --
``StepBackend`` and ``StepVLM`` -- never on a concrete physics or VLM class.

This module also ships mock implementations so the whole per-step loop is
runnable and testable *today*, before:
  * A's ``RobotBackend`` / ``MockBackend`` land, and
  * the AWS SSM tunnel + GPU physics are available.

Contract for A (Stage 1 hand-off)
---------------------------------
A real backend must satisfy ``StepBackend``. ``MjlabGo2Backend`` already provides
``start`` / ``reset`` / ``set_velocity_command`` / ``step`` / ``control_dt`` /
``close`` with matching semantics. The only additions the bridge looks for are
``emergency_stop()`` and the ``interrupted`` flag -- both accessed through
``getattr`` and treated as optional, so a backend without them still works (the
bridge falls back to latching a zero ``VelocityCommand``).

Selection is by environment variable so nothing here has to change per machine:
  * ``NAVILA_BRIDGE_BACKEND``  -> ``mock`` (default) | ``mjlab``
  * ``NAVILA_BRIDGE_VLM``      -> ``mock`` (default) | ``tcp``
  * ``NAVILA_BRIDGE_VLM_SCRIPT`` -> optional ';'-separated action phrases for the
    mock VLM, e.g. "move forward by 75 cm; turn left by 30 degrees; stop"
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

# The per-step bridge needs the real navigation contracts (RobotState etc.).
# Phase 1's script-wrapping tools do not import this module, so adding the repo
# 'src' dir here does not affect them.
_NAVILA_SRC = Path(__file__).resolve().parent / "NaVILA-Orca" / "src"
if _NAVILA_SRC.is_dir() and str(_NAVILA_SRC) not in sys.path:
    sys.path.insert(0, str(_NAVILA_SRC))

import numpy as np

from navila_orca.contracts import PhysicsStep, RobotState, VelocityCommand

DEFAULT_CONTROL_DT = 0.02  # Go2 policy tick: 5 ms sim timestep x decimation 4.


# ---------------------------------------------------------------------------
# Seams
# ---------------------------------------------------------------------------

@runtime_checkable
class StepBackend(Protocol):
    """One locomotion backend, stepped one policy tick at a time."""

    control_dt: float

    def start(self) -> None: ...

    def reset(self, episode: Any | None = None) -> RobotState: ...

    def set_velocity_command(self, command: VelocityCommand) -> None: ...

    def step(self) -> "RobotState | PhysicsStep": ...

    def close(self) -> None: ...

    # Optional (Stage 3 Safety Watchdog seam) -- accessed via getattr:
    #   def emergency_stop(self) -> None: ...
    #   interrupted: bool


@runtime_checkable
class StepVLM(Protocol):
    """Given the current context, return one NaVILA action phrase (raw text)."""

    def next_action(
        self,
        *,
        instruction: str,
        state: RobotState,
        frames: Sequence[Any],
        goal_xy: "tuple[float, float] | None" = None,
    ) -> str: ...


# ---------------------------------------------------------------------------
# Mock backend -- planar unicycle kinematics, no GPU, no MuJoCo.
# ---------------------------------------------------------------------------

def _yaw_to_quat_wxyz(yaw: float) -> np.ndarray:
    half = yaw / 2.0
    return np.array([math.cos(half), 0.0, 0.0, math.sin(half)])


class MockBackend:
    """Deterministic planar stand-in for ``MjlabGo2Backend``.

    Integrates the latched body-frame velocity command with a simple unicycle
    model. Good enough to exercise the per-step loop, goal-reaching, timeouts,
    emergency-stop, and physics-termination handling.
    """

    def __init__(
        self,
        *,
        control_dt: float = DEFAULT_CONTROL_DT,
        start_xy: tuple[float, float] = (0.0, 0.0),
        start_yaw: float = 0.0,
        z_height: float = 0.42,
        terminate_after_steps: int | None = None,
    ) -> None:
        self.control_dt = float(control_dt)
        self._start = (float(start_xy[0]), float(start_xy[1]), float(start_yaw))
        self._z = float(z_height)
        self._terminate_after_steps = terminate_after_steps
        self.interrupted = False
        self.started = False
        self._reset_pose()

    # -- lifecycle ------------------------------------------------------------
    def _reset_pose(self) -> None:
        self._x, self._y, self._yaw = self._start
        self._vx = self._vy = self._wz = 0.0
        self._step_id = 0

    def start(self) -> None:
        self.started = True

    def reset(self, episode: Any | None = None) -> RobotState:
        del episode
        self.started = True
        self.interrupted = False
        self._reset_pose()
        return self._state()

    def close(self) -> None:
        self.started = False

    # -- control ------------------------------------------------------------
    def set_velocity_command(self, command: VelocityCommand) -> None:
        self._vx = float(command.vx)
        self._vy = float(command.vy)
        self._wz = float(command.wz)

    def emergency_stop(self) -> None:
        """Stage 3 seam: latch zero velocity and raise the interrupt flag."""
        self._vx = self._vy = self._wz = 0.0
        self.interrupted = True

    def step(self) -> PhysicsStep:
        if not self.started:
            raise RuntimeError("MockBackend.step() before start()/reset()")
        dt = self.control_dt
        self._yaw += self._wz * dt
        # body-frame (vx forward, vy left) -> world
        cos_y, sin_y = math.cos(self._yaw), math.sin(self._yaw)
        self._x += (self._vx * cos_y - self._vy * sin_y) * dt
        self._y += (self._vx * sin_y + self._vy * cos_y) * dt
        self._step_id += 1
        terminated = (
            self._terminate_after_steps is not None
            and self._step_id >= self._terminate_after_steps
        )
        return PhysicsStep(
            state=self._state(),
            reward=0.0,
            terminated=bool(terminated),
            truncated=False,
            info={"auto_reset_state": bool(terminated), "mock": True},
        )

    # -- state ------------------------------------------------------------
    def _state(self) -> RobotState:
        return RobotState(
            step_id=self._step_id,
            sim_time_s=self._step_id * self.control_dt,
            root_pos_world=np.array([self._x, self._y, self._z]),
            root_quat_wxyz=_yaw_to_quat_wxyz(self._yaw),
            body_ang_vel=np.array([0.0, 0.0, self._wz]),
            base_rpy=np.array([0.0, 0.0, self._yaw]),
            joint_pos=np.zeros(12),
            joint_vel=np.zeros(12),
            last_raw_action=np.zeros(12),
        )


# ---------------------------------------------------------------------------
# Mock VLM
# ---------------------------------------------------------------------------

class MockVLM:
    """Returns NaVILA action phrases with no network and no model.

    Two modes:
      * ``script`` given -> replay it phrase by phrase; ``"stop"`` once exhausted.
      * no script -> greedy goal-seeking from ``state`` + ``goal_xy``: face the
        goal (turn 15/30/45 deg), then close distance (move 25/50/75 cm), then
        ``"stop"`` inside ``stop_radius``.
    """

    def __init__(
        self,
        *,
        script: Sequence[str] | None = None,
        stop_radius: float = 0.4,
        heading_tolerance_deg: float = 12.0,
    ) -> None:
        self._script = list(script) if script else None
        self._i = 0
        self.stop_radius = float(stop_radius)
        self.heading_tolerance_deg = float(heading_tolerance_deg)

    def next_action(
        self,
        *,
        instruction: str,
        state: RobotState,
        frames: Sequence[Any],
        goal_xy: tuple[float, float] | None = None,
    ) -> str:
        del instruction, frames
        if self._script is not None:
            if self._i >= len(self._script):
                return "stop"
            phrase = self._script[self._i]
            self._i += 1
            return phrase
        if goal_xy is None:
            return "stop"

        px, py, _ = state.root_pos_world
        gx, gy = goal_xy
        dx, dy = gx - px, gy - py
        dist = math.hypot(dx, dy)
        if dist <= self.stop_radius:
            return "stop"

        yaw = float(state.base_rpy[2])
        desired = math.atan2(dy, dx)
        err_deg = math.degrees((desired - yaw + math.pi) % (2 * math.pi) - math.pi)
        if abs(err_deg) > self.heading_tolerance_deg:
            direction = "left" if err_deg > 0 else "right"
            mag = 45 if abs(err_deg) >= 45 else (30 if abs(err_deg) >= 20 else 15)
            return f"turn {direction} by {mag} degrees"
        cm = 75 if dist > 1.0 else (50 if dist > 0.6 else 25)
        return f"move forward by {cm} cm"


# ---------------------------------------------------------------------------
# Real TCP VLM adapter (thin -- reuses the tested client + sample_history)
# ---------------------------------------------------------------------------

class TcpVLM:
    """Adapts ``LengthPrefixedJsonVLMClient`` to the ``StepVLM`` seam.

    Requires real ego frames in ``frames`` (OrcaLab camera capture -- Phase 2).
    Raises if handed the mock's placeholder frames so a black-frame request is
    never silently sent to NaVILA.
    """

    def __init__(
        self, *, host: str = "127.0.0.1", port: int = 54321, timeout_s: float = 120.0
    ) -> None:
        from navila_orca.vlm_client import LengthPrefixedJsonVLMClient

        self._client = LengthPrefixedJsonVLMClient(
            host=host, port=port, timeout_s=timeout_s
        )

    def next_action(
        self,
        *,
        instruction: str,
        state: RobotState,
        frames: Sequence[Any],
        goal_xy: tuple[float, float] | None = None,
    ) -> str:
        del state, goal_xy
        from navila_orca.frames import sample_history

        real = [f for f in frames if not _is_placeholder_frame(f)]
        if not real:
            raise RuntimeError(
                "TcpVLM needs real ego camera frames; none wired yet "
                "(OrcaLab capture is Phase 2). Use NAVILA_BRIDGE_VLM=mock for now."
            )
        return self._client.infer(sample_history(real), instruction)


def _is_placeholder_frame(frame: Any) -> bool:
    arr = getattr(frame, "shape", None)
    return bool(arr is not None and tuple(frame.shape[:2]) == (8, 8))


def placeholder_frame() -> np.ndarray:
    """Cheap stand-in frame for the mock loop (8x8 black RGB)."""
    return np.zeros((8, 8, 3), dtype=np.uint8)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def make_backend(kind: str | None = None, **kwargs: Any) -> StepBackend:
    kind = (kind or os.environ.get("NAVILA_BRIDGE_BACKEND", "mock")).lower()
    if kind == "mock":
        return MockBackend(**kwargs)
    if kind == "mjlab":
        from navila_orca.backends.mjlab_go2 import MjlabGo2Backend

        params = {"num_envs": 1, "device": os.environ.get("NAVILA_ORCA_DEVICE", "cpu")}
        params.update(kwargs)
        return MjlabGo2Backend(**params)
    raise ValueError(f"unknown backend kind {kind!r} (expected 'mock' or 'mjlab')")


def make_vlm(kind: str | None = None, **kwargs: Any) -> StepVLM:
    kind = (kind or os.environ.get("NAVILA_BRIDGE_VLM", "mock")).lower()
    if kind == "mock":
        script = kwargs.pop("script", None)
        if script is None:
            raw = os.environ.get("NAVILA_BRIDGE_VLM_SCRIPT", "").strip()
            if raw:
                script = [p.strip() for p in raw.split(";") if p.strip()]
        return MockVLM(script=script, **kwargs)
    if kind == "tcp":
        return TcpVLM(**kwargs)
    raise ValueError(f"unknown vlm kind {kind!r} (expected 'mock' or 'tcp')")
