import math

import numpy as np
import pytest

from navila_orca.robot_backend import (
    EmergencyStopActive,
    MockBackend,
    MockBackendConfig,
    RobotBackend,
    RobotPose,
)


def test_mock_backend_satisfies_robot_backend_protocol():
    backend = MockBackend()
    assert isinstance(backend, RobotBackend)
    backend.close()


def test_move_integrates_pose_forward():
    backend = MockBackend(MockBackendConfig(control_dt=1.0))
    backend.move(vx=1.0, vy=0.0, vyaw=0.0)
    pose = backend.get_pose()
    assert pose.position == pytest.approx([1.0, 0.0, 0.0])


def test_move_turns_then_translates_in_body_frame():
    backend = MockBackend(MockBackendConfig(control_dt=1.0))
    backend.move(vx=0.0, vy=0.0, vyaw=math.pi / 2)
    backend.move(vx=1.0, vy=0.0, vyaw=0.0)
    pose = backend.get_pose()
    assert pose.position == pytest.approx([0.0, 1.0, 0.0], abs=1e-9)


def test_emergency_stop_latches_and_rejects_move():
    backend = MockBackend()
    backend.move(vx=1.0, vy=0.0, vyaw=0.0)
    backend.emergency_stop()
    assert backend.is_estopped
    with pytest.raises(EmergencyStopActive):
        backend.move(vx=1.0, vy=0.0, vyaw=0.0)


def test_reset_clears_estop_and_returns_to_start_pose():
    backend = MockBackend()
    backend.move(vx=1.0, vy=0.0, vyaw=0.0)
    backend.emergency_stop()
    backend.reset()
    assert not backend.is_estopped
    pose = backend.get_pose()
    assert pose.position == pytest.approx([0.0, 0.0, 0.0])
    backend.move(vx=0.1, vy=0.0, vyaw=0.0)  # should not raise


def test_read_harness_force_defaults_to_nominal():
    backend = MockBackend(MockBackendConfig(nominal_force=42.0))
    assert backend.read_harness_force() == pytest.approx(42.0)


def test_force_drop_event_is_visible_through_backend():
    backend = MockBackend()
    backend.force_sensor.schedule_drop(at_step=2, duration_steps=2, value=0.0)
    readings = [backend.read_harness_force() for _ in range(5)]
    assert readings[2] == pytest.approx(0.0)
    assert readings[3] == pytest.approx(0.0)
    assert readings[0] != pytest.approx(0.0)
    assert readings[4] != pytest.approx(0.0)


def test_robot_pose_rejects_zero_quaternion():
    with pytest.raises(ValueError):
        RobotPose(position=np.zeros(3), quat_wxyz=np.zeros(4))
