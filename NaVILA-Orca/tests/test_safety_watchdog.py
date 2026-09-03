import pytest

from navila_orca.robot_backend import EmergencyStopActive, MockBackend
from navila_orca.safety_watchdog import SafeForceBand, SafetyWatchdog


def test_no_trip_while_force_stays_nominal():
    backend = MockBackend()
    watchdog = SafetyWatchdog(backend, debounce_ticks=3)
    events = watchdog.run_for(10)
    assert events == []
    assert not watchdog.tripped
    assert not backend.is_estopped


def test_trips_on_the_debounce_ticks_th_consecutive_bad_reading():
    backend = MockBackend()
    backend.force_sensor.schedule_drop(at_step=5, duration_steps=10)
    watchdog = SafetyWatchdog(backend, debounce_ticks=3)

    # Steps 0-4: nominal. Steps 5,6: first two bad readings, not yet tripped.
    events = watchdog.run_for(7)
    assert events == []
    assert not watchdog.tripped
    assert not backend.is_estopped

    # Step 7 is the 3rd consecutive bad reading -> trips.
    event = watchdog.tick()
    assert event is not None
    assert event.step == 7
    assert watchdog.tripped
    assert backend.is_estopped


def test_emergency_stop_actually_preempts_the_backend():
    backend = MockBackend()
    backend.force_sensor.schedule_drop(at_step=0, duration_steps=100)
    watchdog = SafetyWatchdog(backend, debounce_ticks=1)

    watchdog.tick()
    assert backend.is_estopped
    with pytest.raises(EmergencyStopActive):
        backend.move(vx=1.0, vy=0.0, vyaw=0.0)


def test_transient_bad_reading_does_not_trip_before_debounce_window():
    backend = MockBackend()
    backend.force_sensor.schedule_drop(at_step=2, duration_steps=1)  # single blip
    watchdog = SafetyWatchdog(backend, debounce_ticks=3)

    events = watchdog.run_for(6)
    assert events == []
    assert not watchdog.tripped


def test_streak_resets_if_reading_returns_in_band_before_tripping():
    backend = MockBackend()
    # Bad at steps 2,3 (streak=2), back in band at 4, bad again at 5,6 (streak
    # would only be 2 again) -- with debounce_ticks=3 this must never trip.
    backend.force_sensor.schedule_drop(at_step=2, duration_steps=2)
    backend.force_sensor.schedule_drop(at_step=5, duration_steps=2)
    watchdog = SafetyWatchdog(backend, debounce_ticks=3)

    events = watchdog.run_for(8)
    assert events == []
    assert not watchdog.tripped


def test_only_trips_once_even_if_bad_readings_continue():
    backend = MockBackend()
    backend.force_sensor.schedule_drop(at_step=0, duration_steps=100)
    watchdog = SafetyWatchdog(backend, debounce_ticks=2)

    events = watchdog.run_for(20)
    assert len(events) == 1
    assert len(watchdog.log) == 1


def test_on_trip_callback_is_invoked():
    backend = MockBackend()
    backend.force_sensor.schedule_drop(at_step=0, duration_steps=10)
    seen = []
    watchdog = SafetyWatchdog(backend, debounce_ticks=1, on_trip=seen.append)

    watchdog.tick()
    assert len(seen) == 1
    assert seen[0].reason.startswith("harness force")


def test_watchdog_reset_does_not_clear_backend_estop():
    backend = MockBackend()
    backend.force_sensor.schedule_drop(at_step=0, duration_steps=10)
    watchdog = SafetyWatchdog(backend, debounce_ticks=1)

    watchdog.tick()
    assert watchdog.tripped
    assert backend.is_estopped

    watchdog.reset()
    assert not watchdog.tripped
    # Clearing the watchdog's own latch must not silently clear the robot's
    # emergency stop -- that has to stay a separate, deliberate action.
    assert backend.is_estopped
    with pytest.raises(EmergencyStopActive):
        backend.move(vx=0.1, vy=0.0, vyaw=0.0)


def test_custom_safe_band_is_respected():
    backend = MockBackend()
    backend.force_sensor.schedule_drop(at_step=0, duration_steps=10, value=90.0)
    watchdog = SafetyWatchdog(
        backend, band=SafeForceBand(low=0.0, high=100.0), debounce_ticks=1
    )
    events = watchdog.run_for(3)
    assert events == []
    assert not watchdog.tripped


def test_invalid_band_rejected():
    with pytest.raises(ValueError):
        SafeForceBand(low=50.0, high=50.0)
    with pytest.raises(ValueError):
        SafeForceBand(low=80.0, high=20.0)


def test_invalid_debounce_ticks_rejected():
    backend = MockBackend()
    with pytest.raises(ValueError):
        SafetyWatchdog(backend, debounce_ticks=0)
