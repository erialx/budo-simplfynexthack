import pytest

from navila_orca.decision_logbook import DecisionLogbook
from navila_orca.robot_backend import MockBackend
from navila_orca.safety_watchdog import SafetyWatchdog
from navila_orca.veto import HazardVetoAgent


class StubVetoClient:
    def __init__(self, responses):
        self._responses = list(responses)

    def query(self, image, instruction, proposed_action):
        return self._responses.pop(0)


def test_record_watchdog_trip_appends_and_prints(capsys):
    logbook = DecisionLogbook()
    backend = MockBackend()
    backend.force_sensor.schedule_drop(at_step=0, duration_steps=10)
    watchdog = SafetyWatchdog(
        backend, debounce_ticks=1, on_trip=logbook.record_watchdog_trip
    )

    watchdog.tick()

    entries = logbook.entries()
    assert len(entries) == 1
    assert entries[0].kind == "STOP"
    assert entries[0].source == "safety_watchdog"
    printed = capsys.readouterr().out
    assert "STOP" in printed
    assert "safety_watchdog" in printed


def test_veto_decisions_are_logged_but_clear_is_not_by_default():
    logbook = DecisionLogbook(sink=None)
    client = StubVetoClient(["CLEAR", "VETO: person crossing", "CLEAR"])
    agent = HazardVetoAgent(client, on_decision=logbook.record_veto_decision)

    for _ in range(3):
        agent.assess(None, "go", "move forward 25 cm")

    entries = logbook.entries()
    assert len(entries) == 1
    assert entries[0].kind == "VETO"
    assert entries[0].reason == "person crossing"


def test_log_clear_true_records_every_decision():
    logbook = DecisionLogbook(sink=None, log_clear=True)
    client = StubVetoClient(["CLEAR", "VETO: hazard"])
    agent = HazardVetoAgent(client, on_decision=logbook.record_veto_decision)

    agent.assess(None, "go", "move forward 25 cm")
    agent.assess(None, "go", "move forward 25 cm")

    kinds = [entry.kind for entry in logbook.entries()]
    assert kinds == ["CLEAR", "VETO"]


def test_entries_are_chronological_across_both_sources():
    ticks = iter([100.0, 100.5, 101.0])
    logbook = DecisionLogbook(clock=lambda: next(ticks), sink=None)

    backend = MockBackend()
    backend.force_sensor.schedule_drop(at_step=0, duration_steps=10)
    watchdog = SafetyWatchdog(backend, debounce_ticks=1, on_trip=logbook.record_watchdog_trip)
    client = StubVetoClient(["VETO: hazard one", "VETO: hazard two"])
    agent = HazardVetoAgent(client, on_decision=logbook.record_veto_decision)

    watchdog.tick()
    agent.assess(None, "go", "move forward 25 cm")
    agent.assess(None, "go", "move forward 25 cm")

    entries = logbook.entries()
    timestamps = [entry.timestamp_s for entry in entries]
    assert timestamps == sorted(timestamps)
    assert [entry.source for entry in entries] == [
        "safety_watchdog",
        "hazard_veto",
        "hazard_veto",
    ]


def test_dump_renders_every_entry():
    logbook = DecisionLogbook(sink=None)
    backend = MockBackend()
    backend.force_sensor.schedule_drop(at_step=0, duration_steps=10)
    watchdog = SafetyWatchdog(backend, debounce_ticks=1, on_trip=logbook.record_watchdog_trip)
    watchdog.tick()

    dumped = logbook.dump()
    assert "STOP" in dumped
    assert dumped.count("\n") == 0  # only one entry so far


def test_entries_returns_a_copy_not_the_live_list():
    logbook = DecisionLogbook(sink=None)
    backend = MockBackend()
    backend.force_sensor.schedule_drop(at_step=0, duration_steps=10)
    watchdog = SafetyWatchdog(backend, debounce_ticks=1, on_trip=logbook.record_watchdog_trip)
    watchdog.tick()

    snapshot = logbook.entries()
    snapshot.append("not real")
    assert len(logbook.entries()) == 1
