"""Reactive safety layer -- watches harness force, zero LLM calls in this loop.

Design constraint from CLAUDE.md: this loop must stay dumb and fast (threshold +
debounce, nothing else) so it is actually independent of whatever the deliberative
Orchestrator or tactical Hazard Veto Agent are doing at any given moment -- it has to
keep working even if those LLM layers are stuck, mid-inference, or wrong. It must be
able to preempt the Driver directly by calling
:meth:`navila_orca.robot_backend.backend.RobotBackend.emergency_stop` itself, without
routing through the Orchestrator.

Runs at ~20Hz via repeated calls to :meth:`SafetyWatchdog.tick`; the caller owns the
loop/timer, this class only owns the threshold-and-debounce decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from navila_orca.robot_backend.backend import RobotBackend


@dataclass(frozen=True, slots=True)
class SafeForceBand:
    """The harness-force range considered safe, in newtons."""

    low: float = 20.0
    high: float = 80.0

    def __post_init__(self) -> None:
        if self.low >= self.high:
            raise ValueError(
                f"low ({self.low}) must be less than high ({self.high})"
            )

    def contains(self, force: float) -> bool:
        return self.low <= force <= self.high


@dataclass(frozen=True, slots=True)
class WatchdogEvent:
    """One emergency-stop trip, for the decision logbook and for tests."""

    step: int
    force: float
    reason: str


class SafetyWatchdog:
    """Polls a harness-force reading every tick; trips ``emergency_stop()`` on the
    backend after ``debounce_ticks`` consecutive out-of-band readings.

    Deliberately minimal: no LLM calls, no calls into the Orchestrator, no
    dependency on the rest of the pipeline. It can be constructed and unit-tested
    against a bare :class:`~navila_orca.robot_backend.mock_backend.MockBackend`
    with a scripted force drop -- see ``tests/test_safety_watchdog.py``.
    """

    def __init__(
        self,
        backend: RobotBackend,
        band: SafeForceBand | None = None,
        debounce_ticks: int = 3,
        force_reader: Callable[[], float] | None = None,
        on_trip: Callable[[WatchdogEvent], None] | None = None,
    ) -> None:
        if debounce_ticks < 1:
            raise ValueError("debounce_ticks must be >= 1")
        self._backend = backend
        self._band = band or SafeForceBand()
        self._debounce_ticks = debounce_ticks
        # Defaults to reading straight off the backend, but a caller can pass a
        # separate sensor callable (e.g. to poll a MockForceSensor that isn't wired
        # into the backend it's watching).
        self._read_force = force_reader or backend.read_harness_force
        self._on_trip = on_trip
        self._out_of_band_streak = 0
        self._step = 0
        self._tripped = False
        self._log: list[WatchdogEvent] = []

    @property
    def tripped(self) -> bool:
        return self._tripped

    @property
    def log(self) -> list[WatchdogEvent]:
        """A copy of every trip event recorded so far -- the decision logbook reads
        this (or its own ``on_trip`` callback) to print stop/veto lines with a
        timestamp and reason."""
        return list(self._log)

    def tick(self) -> WatchdogEvent | None:
        """Run one poll. Call this at ~20Hz.

        Returns the :class:`WatchdogEvent` if *this* tick is the one that tripped
        the emergency stop, else ``None`` -- including on every tick after the
        watchdog is already latched, so a caller can safely call ``tick()`` in a
        tight loop without re-triggering ``emergency_stop()`` over and over.
        """
        force = self._read_force()
        step = self._step
        self._step += 1

        if self._band.contains(force):
            self._out_of_band_streak = 0
            return None

        self._out_of_band_streak += 1
        if self._tripped:
            return None
        if self._out_of_band_streak < self._debounce_ticks:
            return None

        event = WatchdogEvent(
            step=step,
            force=force,
            reason=(
                f"harness force {force:.2f}N outside safe band "
                f"[{self._band.low}, {self._band.high}]N for "
                f"{self._out_of_band_streak} consecutive ticks"
            ),
        )
        self._tripped = True
        self._log.append(event)
        self._backend.emergency_stop()
        if self._on_trip is not None:
            self._on_trip(event)
        return event

    def reset(self) -> None:
        """Clear the watchdog's own latch and streak counter.

        Deliberately does **not** call ``backend.reset()`` -- clearing an
        emergency stop on the robot is a separate, deliberate action from
        clearing the watchdog's internal bookkeeping, so the two are never
        silently coupled.
        """
        self._tripped = False
        self._out_of_band_streak = 0

    def run_for(self, ticks: int) -> list[WatchdogEvent]:
        """Convenience for tests/demos: call ``tick()`` ``ticks`` times, return
        every trip event produced along the way (normally zero or one)."""
        events: list[WatchdogEvent] = []
        for _ in range(ticks):
            event = self.tick()
            if event is not None:
                events.append(event)
        return events


def _run_demo(drop_at_step: int, drop_duration: int, debounce_ticks: int) -> None:
    from navila_orca.robot_backend import MockBackend

    backend = MockBackend()
    backend.force_sensor.schedule_drop(
        at_step=drop_at_step, duration_steps=drop_duration
    )
    watchdog = SafetyWatchdog(backend, debounce_ticks=debounce_ticks)

    for step in range(drop_at_step + drop_duration + debounce_ticks + 5):
        event = watchdog.tick()
        if event is not None:
            print(f"step={step:4d} TRIPPED: {event.reason}")
        else:
            print(f"step={step:4d} ok (tripped={watchdog.tripped})")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drop-at-step", type=int, default=10)
    parser.add_argument("--drop-duration", type=int, default=10)
    parser.add_argument("--debounce-ticks", type=int, default=3)
    args = parser.parse_args()
    _run_demo(args.drop_at_step, args.drop_duration, args.debounce_ticks)


if __name__ == "__main__":
    main()
