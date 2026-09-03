"""High-level robot control interface for the guide-dog navigation loop.

This package is a different seam from :mod:`navila_orca.backends`, which hosts the
low-level MJLab/MJWarp physics + locomotion-policy backend used for training and
in-sim rollout. ``RobotBackend`` sits one layer up: it is the interface the Driver
issues velocity commands against (:meth:`RobotBackend.move`) and the Safety Watchdog
calls directly, bypassing the Orchestrator, to halt the robot
(:meth:`RobotBackend.emergency_stop`) and read the (mocked, this week) harness-force
sensor (:meth:`RobotBackend.read_harness_force`).

It is deliberately shaped after the real ``unitree_sdk2_python`` SDK's client surface
(``SportClient.Move(vx, vy, vyaw)`` / ``SportClient.StopMove()``) so that a real-hardware
swap later is a config change -- pick a ``UnitreeBackend`` implementation of this same
Protocol -- not a rewrite of the Driver or Safety Watchdog.
"""

from __future__ import annotations

from navila_orca.robot_backend.backend import (
    EmergencyStopActive,
    RobotBackend,
    RobotBackendError,
    RobotPose,
)
from navila_orca.robot_backend.mock_backend import MockBackend, MockBackendConfig
from navila_orca.robot_backend.mock_force_sensor import ForceDropEvent, MockForceSensor

__all__ = [
    "EmergencyStopActive",
    "RobotBackend",
    "RobotBackendError",
    "RobotPose",
    "MockBackend",
    "MockBackendConfig",
    "ForceDropEvent",
    "MockForceSensor",
]
