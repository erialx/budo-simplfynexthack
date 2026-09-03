"""Simulator/hardware-independent robot control contract.

This is a different seam from :mod:`navila_orca.backends`, which hosts the low-level
MJLab/MJWarp physics + locomotion-policy backend used for training and in-sim
rollout. ``RobotBackend`` sits one layer up: it is the interface the Driver issues
velocity commands against (:meth:`RobotBackend.move`) and the Safety Watchdog calls
directly, bypassing the Orchestrator, to halt the robot
(:meth:`RobotBackend.emergency_stop`) and read the (mocked, this week) harness-force
sensor (:meth:`RobotBackend.read_harness_force`).

It is deliberately shaped after the real ``unitree_sdk2_python`` SDK's client surface
(``SportClient.Move(vx, vy, vyaw)`` / ``SportClient.StopMove()``) so that a real-hardware
swap later is a config change — implement this same Protocol as ``UnitreeBackend`` —
not a rewrite of the Driver or Safety Watchdog.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.floating]


class RobotBackendError(RuntimeError):
    """Raised for backend-level failures (e.g. hardware/sim faults)."""


class EmergencyStopActive(RobotBackendError):
    """Raised by :meth:`RobotBackend.move` while latched in an emergency-stop state.

    Mirrors the "safe default is STOP, never silent retry" rule from CLAUDE.md: once
    the Safety Watchdog calls :meth:`RobotBackend.emergency_stop`, every subsequent
    ``move()`` is rejected loudly (not silently dropped) until something explicitly
    calls :meth:`RobotBackend.reset`.
    """


def _float_array(value, *, shape: tuple[int, ...], name: str) -> FloatArray:
    array = np.asarray(value, dtype=np.float64)
    if array.shape != shape:
        raise ValueError(f"{name} must have shape {shape}, got {array.shape}")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must contain only finite values")
    array = array.copy()
    array.setflags(write=False)
    return array


@dataclass(frozen=True, slots=True)
class RobotPose:
    """A robot pose in the right-handed world frame, wxyz quaternion order.

    Matches the convention documented at the top of :mod:`navila_orca.contracts` so
    the two seams (physics backend, robot backend) never disagree about frames.
    """

    position: FloatArray
    quat_wxyz: FloatArray

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "position", _float_array(self.position, shape=(3,), name="position")
        )
        quat = _float_array(self.quat_wxyz, shape=(4,), name="quat_wxyz")
        if np.linalg.norm(quat) <= 1.0e-12:
            raise ValueError("quat_wxyz must have non-zero norm")
        object.__setattr__(self, "quat_wxyz", quat)


@runtime_checkable
class RobotBackend(Protocol):
    """The interface the Driver and Safety Watchdog issue commands against.

    Modeled on ``unitree_sdk2_python``'s ``SportClient`` surface so a real-hardware
    ``UnitreeBackend`` can implement this same Protocol later without touching any
    calling code:

    - ``move`` mirrors ``SportClient.Move(vx, vy, vyaw)`` — a continuous body-frame
      velocity command, re-issued every control tick, not a stepped/duration command
      (that's :class:`navila_orca.contracts.VelocityCommand`, one layer lower, for the
      physics backend).
    - ``emergency_stop`` mirrors ``SportClient.StopMove()``, but is guaranteed callable
      directly by the Safety Watchdog, bypassing the Orchestrator, and latches: once
      called, every subsequent ``move()`` raises :class:`EmergencyStopActive` until
      ``reset()``.
    - ``read_harness_force`` has no real-SDK analogue (the harness force sensor is
      this project's own hardware); it's mocked this week and is what the Safety
      Watchdog polls at ~20Hz.
    """

    def move(self, vx: float, vy: float, vyaw: float) -> None:
        """Issue one continuous body-frame velocity command."""
        ...

    def emergency_stop(self) -> None:
        """Immediately zero velocity and latch into a stopped state. Idempotent."""
        ...

    def reset(self) -> None:
        """Clear a latched emergency stop and return to the backend's start pose."""
        ...

    def read_harness_force(self) -> float:
        """Return the current harness-force reading, in newtons."""
        ...

    def get_pose(self) -> RobotPose:
        """Return the robot's current pose."""
        ...

    def close(self) -> None:
        """Release backend resources. Safe to call multiple times."""
        ...
