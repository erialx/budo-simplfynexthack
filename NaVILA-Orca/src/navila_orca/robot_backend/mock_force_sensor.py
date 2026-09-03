"""A scripted harness-force sensor for testing the Safety Watchdog.

Produces a steady in-band reading by default, with force-drop events injected at
specific step indices -- the "force drops to zero" fault-injection scenario described
in CLAUDE.md. Fully standalone: does not import anything from
:mod:`navila_orca.robot_backend`, so the Safety Watchdog can be developed and unit
tested against it before :class:`~navila_orca.robot_backend.mock_backend.MockBackend`
exists, and ``MockBackend`` composes this class rather than duplicating its scheduling
logic.

Run directly to print a stream of readings with one scripted drop, e.g. for a quick
manual sanity check::

    python -m navila_orca.robot_backend.mock_force_sensor
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import time


@dataclass(frozen=True, slots=True)
class ForceDropEvent:
    """A scripted window during which the sensor reports ``value`` instead of nominal.

    ``at_step`` and ``duration_steps`` are in units of :meth:`MockForceSensor.read`
    calls (i.e. control ticks), not wall-clock time, so tests stay deterministic
    regardless of how fast they run.
    """

    at_step: int
    duration_steps: int = 1
    value: float = 0.0

    def __post_init__(self) -> None:
        if self.at_step < 0:
            raise ValueError("at_step must be non-negative")
        if self.duration_steps < 1:
            raise ValueError("duration_steps must be >= 1")

    def covers(self, step: int) -> bool:
        return self.at_step <= step < self.at_step + self.duration_steps


class MockForceSensor:
    """A harness-force sensor that reads ``nominal_force`` except during scheduled events.

    Not thread-safe; call ``read`` from a single loop (the Safety Watchdog's ~20Hz
    poll), same as a real sensor driver would be.
    """

    def __init__(
        self,
        nominal_force: float = 45.0,
        events: "list[ForceDropEvent] | None" = None,
    ) -> None:
        self.nominal_force = float(nominal_force)
        self._events: list[ForceDropEvent] = list(events) if events else []
        self._step = 0

    def schedule_drop(
        self, at_step: int, duration_steps: int = 1, value: float = 0.0
    ) -> ForceDropEvent:
        """Convenience wrapper for the common case: schedule a force-drop-to-``value`` event."""
        event = ForceDropEvent(at_step=at_step, duration_steps=duration_steps, value=value)
        self._events.append(event)
        return event

    def read(self, step: "int | None" = None) -> float:
        """Return the force reading for ``step``.

        If ``step`` is omitted, an internal counter is used and advanced by one on
        every such call -- convenient for a live polling loop that just wants "the
        next reading."
        """
        if step is None:
            step = self._step
            self._step += 1
        for event in self._events:
            if event.covers(step):
                return event.value
        return self.nominal_force

    def reset(self) -> None:
        """Rewind the internal step counter. Scheduled events are kept."""
        self._step = 0


def _run_demo(hz: float, drop_at_step: int, drop_duration: int) -> None:
    sensor = MockForceSensor()
    sensor.schedule_drop(at_step=drop_at_step, duration_steps=drop_duration)
    period_s = 1.0 / hz
    step = 0
    try:
        while True:
            reading = sensor.read(step)
            flag = " <-- DROP" if reading != sensor.nominal_force else ""
            print(f"step={step:4d} force={reading:6.2f}N{flag}")
            step += 1
            time.sleep(period_s)
    except KeyboardInterrupt:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hz", type=float, default=20.0,
        help="poll rate, Hz (default: 20, matches the Safety Watchdog)",
    )
    parser.add_argument(
        "--drop-at-step", type=int, default=40,
        help="step index at which force drops to zero",
    )
    parser.add_argument(
        "--drop-duration", type=int, default=10,
        help="how many steps the drop lasts",
    )
    args = parser.parse_args()
    _run_demo(args.hz, args.drop_at_step, args.drop_duration)


if __name__ == "__main__":
    main()
