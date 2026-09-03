"""Print every stop/veto with a timestamp and a reason.

Cheap by design: it doesn't own or drive any loop, it just records events handed
to it by :class:`navila_orca.safety_watchdog.SafetyWatchdog` and
:class:`navila_orca.veto.veto_agent.HazardVetoAgent` (both already keep their own
``.log``; this class exists to merge the two into one chronological, human-readable
stream with wall-clock timestamps, since the watchdog's ~20Hz step counter and the
veto agent's ~1Hz step counter aren't comparable to each other).

Wire it in by passing its recording methods as the callbacks those two classes
already accept::

    logbook = DecisionLogbook()
    watchdog = SafetyWatchdog(backend, on_trip=logbook.record_watchdog_trip)
    veto_agent = HazardVetoAgent(client, on_decision=logbook.record_veto_decision)

This is exactly the answer to "how do you know it's making good decisions" in Q&A:
``logbook.dump()`` (or ``logbook.entries()`` for anything programmatic) is a
complete, timestamped account of every safety stop and every veto/clear call the
system made.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import time
from typing import Callable

from navila_orca.safety_watchdog import WatchdogEvent
from navila_orca.veto.veto_agent import VetoDecision


@dataclass(frozen=True, slots=True)
class LogbookEntry:
    """One recorded event, timestamped when the logbook received it (not when the
    underlying watchdog tick or veto call happened -- see the module docstring for
    why the two loops' own step counters aren't comparable)."""

    timestamp_s: float
    source: str  # "safety_watchdog" or "hazard_veto"
    kind: str  # "STOP", "VETO", or "CLEAR"
    reason: str

    def format(self) -> str:
        clock = datetime.fromtimestamp(self.timestamp_s).strftime("%H:%M:%S.%f")[:-3]
        return f"[{clock}] {self.kind:5s} ({self.source}): {self.reason}"


class DecisionLogbook:
    """Merges :class:`~navila_orca.safety_watchdog.WatchdogEvent` and
    :class:`~navila_orca.veto.veto_agent.VetoDecision` into one timestamped log.

    By default every entry is also printed as it's recorded (via ``sink``, which
    defaults to :func:`print`) -- that's the "plain text/log output is enough for
    the demo" rule from CLAUDE.md, kept behind this one seam so swapping in TTS
    later is a matter of passing a different ``sink``, not a rewrite.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        sink: "Callable[[str], None] | None" = print,
        log_clear: bool = False,
    ) -> None:
        self._clock = clock
        self._sink = sink
        self._log_clear = log_clear
        self._entries: list[LogbookEntry] = []

    def entries(self) -> list[LogbookEntry]:
        return list(self._entries)

    def _append(self, *, kind: str, source: str, reason: str) -> LogbookEntry:
        entry = LogbookEntry(
            timestamp_s=self._clock(), source=source, kind=kind, reason=reason
        )
        self._entries.append(entry)
        if self._sink is not None:
            self._sink(entry.format())
        return entry

    def record_watchdog_trip(self, event: WatchdogEvent) -> LogbookEntry:
        """Pass this directly as ``SafetyWatchdog(..., on_trip=...)``."""
        return self._append(kind="STOP", source="safety_watchdog", reason=event.reason)

    def record_veto_decision(self, decision: VetoDecision) -> "LogbookEntry | None":
        """Pass this directly as ``HazardVetoAgent(..., on_decision=...)``.

        Every VETO is recorded. A routine CLEAR is only recorded when the logbook
        was built with ``log_clear=True`` -- otherwise every ~1Hz tactical tick
        would flood the log with "all clear" lines, drowning out the events that
        actually matter. Returns ``None`` when a CLEAR was skipped.
        """
        if decision.is_clear and not self._log_clear:
            return None
        kind = "CLEAR" if decision.is_clear else "VETO"
        reason = decision.reason or "(no reason given)"
        return self._append(kind=kind, source="hazard_veto", reason=reason)

    def dump(self) -> str:
        """The full log as one printable block, oldest entry first."""
        return "\n".join(entry.format() for entry in self._entries)
