import pytest

from navila_orca.robot_backend.mock_force_sensor import ForceDropEvent, MockForceSensor


def test_nominal_reading_by_default():
    sensor = MockForceSensor(nominal_force=50.0)
    assert sensor.read(0) == pytest.approx(50.0)


def test_scheduled_drop_covers_window():
    sensor = MockForceSensor(nominal_force=50.0)
    sensor.schedule_drop(at_step=5, duration_steps=3, value=0.0)
    readings = {step: sensor.read(step) for step in range(10)}
    for step in (5, 6, 7):
        assert readings[step] == pytest.approx(0.0)
    for step in (0, 1, 2, 3, 4, 8, 9):
        assert readings[step] == pytest.approx(50.0)


def test_implicit_step_counter_advances_and_resets():
    sensor = MockForceSensor()
    sensor.schedule_drop(at_step=1, duration_steps=1, value=0.0)
    assert sensor.read() != pytest.approx(0.0)  # step 0
    assert sensor.read() == pytest.approx(0.0)  # step 1
    sensor.reset()
    assert sensor.read() != pytest.approx(0.0)  # step 0 again


def test_force_drop_event_rejects_bad_values():
    with pytest.raises(ValueError):
        ForceDropEvent(at_step=-1)
    with pytest.raises(ValueError):
        ForceDropEvent(at_step=0, duration_steps=0)
