"""In-memory RobotBackend for this week's demo -- no OrcaLab/hardware dependency.

Integrates a simple unicycle kinematic model for pose (good enough for the
Orchestrator/Driver loop; real locomotion realism is MJLab's job via
:mod:`navila_orca.backends.mjlab_go2`, a different seam -- see the
:mod:`navila_orca.robot_backend` package docstring) and composes
:class:`~navila_orca.robot_backend.mock_force_sensor.MockForceSensor` for the harness
reading, so a test can script a force-drop and drive it entirely through this one
class without touching OrcaLab.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from navila_orca.robot_backend.backend import EmergencyStopActive, RobotPose
from navila_orca.robot_backend.mock_force_sensor import MockForceSensor


@dataclass(slots=True)
class MockBackendConfig:
    """Tunables for :class:`MockBackend`. Defaults match the ~20Hz Safety Watchdog tick."""

    control_dt: float = 0.05
    start_position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    start_yaw: float = 0.0
    nominal_force: float = 45.0


def _yaw_to_quat_wxyz(yaw: float) -> np.ndarray:
    half = yaw / 2.0
    return np.array([math.cos(half), 0.0, 0.0, math.sin(half)], dtype=np.float64)


class MockBackend:
    """A standalone, dependency-free implementation of
    :class:`navila_orca.robot_backend.backend.RobotBackend`.

    Implements the Protocol structurally (no explicit inheritance needed -- see
    ``RobotBackend``'s ``@runtime_checkable``) so it can be swapped for a future
    ``UnitreeBackend`` behind the same call sites.
    """

    def __init__(
        self,
        config: "MockBackendConfig | None" = None,
        force_sensor: "MockForceSensor | None" = None,
    ) -> None:
        self._config = config or MockBackendConfig()
        self._force_sensor = force_sensor or MockForceSensor(
            nominal_force=self._config.nominal_force
        )
        self._x, self._y, self._z = self._config.start_position
        self._yaw = self._config.start_yaw
        self._estopped = False
        self._last_command = (0.0, 0.0, 0.0)
        self._closed = False

    @property
    def force_sensor(self) -> MockForceSensor:
        """Exposed so a test (or the Safety Watchdog's own test) can call
        ``backend.force_sensor.schedule_drop(...)`` directly."""
        return self._force_sensor

    @property
    def is_estopped(self) -> bool:
        return self._estopped

    def move(self, vx: float, vy: float, vyaw: float) -> None:
        if self._closed:
            raise RuntimeError("move() called on a closed MockBackend")
        if self._estopped:
            raise EmergencyStopActive(
                "MockBackend is latched in emergency stop; call reset() first"
            )
        dt = self._config.control_dt
        cos_yaw, sin_yaw = math.cos(self._yaw), math.sin(self._yaw)
        self._x += (vx * cos_yaw - vy * sin_yaw) * dt
        self._y += (vx * sin_yaw + vy * cos_yaw) * dt
        self._yaw += vyaw * dt
        self._last_command = (vx, vy, vyaw)

    def emergency_stop(self) -> None:
        self._estopped = True
        self._last_command = (0.0, 0.0, 0.0)

    def reset(self) -> None:
        self._estopped = False
        self._x, self._y, self._z = self._config.start_position
        self._yaw = self._config.start_yaw
        self._last_command = (0.0, 0.0, 0.0)
        self._force_sensor.reset()

    def read_harness_force(self) -> float:
        return self._force_sensor.read()

    def get_pose(self) -> RobotPose:
        return RobotPose(
            position=np.array([self._x, self._y, self._z], dtype=np.float64),
            quat_wxyz=_yaw_to_quat_wxyz(self._yaw),
        )

    def close(self) -> None:
        self._closed = True
