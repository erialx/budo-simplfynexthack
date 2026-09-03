"""Composites a visibly-fake hazard marker onto frames -- automated-testing half of
fault injection (see CLAUDE.md's "Fault injection" section).

Deliberately crude and obvious (a solid red bar top and bottom of frame, plus a text
label) rather than a subtle pixel change: this is a disclosed test harness, not a
hidden bug, and the pitch says so openly. The live-demo trigger is a separate,
"visibly real" thing (an in-scene OrcaLab object or a physical prop) -- this class is
only for automated tests and offline development of the Hazard Veto Agent.

Same step-indexed event-scheduling shape as
:class:`navila_orca.robot_backend.mock_force_sensor.MockForceSensor`, so the two
fault-injection mechanisms (force events, hazard frames) are used the same way in
tests even though they operate on unrelated data.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw


@dataclass(frozen=True, slots=True)
class HazardInjectionEvent:
    """A scripted window during which injected frames carry a hazard marker."""

    at_step: int
    duration_steps: int = 1
    label: str = "HAZARD"

    def __post_init__(self) -> None:
        if self.at_step < 0:
            raise ValueError("at_step must be non-negative")
        if self.duration_steps < 1:
            raise ValueError("duration_steps must be >= 1")
        if not self.label:
            raise ValueError("label must be non-empty")

    def covers(self, step: int) -> bool:
        return self.at_step <= step < self.at_step + self.duration_steps


class ScenarioInjector:
    """Returns frames unchanged, except during scheduled windows where a hazard
    marker is composited on top of a copy of the frame."""

    def __init__(self, events: "list[HazardInjectionEvent] | None" = None) -> None:
        self._events: list[HazardInjectionEvent] = list(events) if events else []

    def schedule(
        self, at_step: int, duration_steps: int = 1, label: str = "HAZARD"
    ) -> HazardInjectionEvent:
        event = HazardInjectionEvent(
            at_step=at_step, duration_steps=duration_steps, label=label
        )
        self._events.append(event)
        return event

    def active_event(self, step: int) -> "HazardInjectionEvent | None":
        for event in self._events:
            if event.covers(step):
                return event
        return None

    def inject(self, frame: Image.Image, step: int) -> Image.Image:
        """Return ``frame`` unchanged unless ``step`` falls in a scheduled window,
        in which case return a *new* image (the input is never mutated) with a
        visible hazard marker composited on top."""
        event = self.active_event(step)
        if event is None:
            return frame

        marked = frame.convert("RGB").copy()
        draw = ImageDraw.Draw(marked)
        width, height = marked.size
        bar_height = max(4, height // 12)
        draw.rectangle([0, 0, width, bar_height], fill=(220, 20, 20))
        draw.rectangle([0, height - bar_height, width, height], fill=(220, 20, 20))
        draw.text((4, 4), event.label, fill=(255, 255, 255))
        return marked
