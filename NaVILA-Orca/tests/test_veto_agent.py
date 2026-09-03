import numpy as np
from PIL import Image
import pytest

from navila_orca.veto import (
    HazardVetoAgent,
    VetoDecision,
    VetoParseError,
    parse_veto_response,
)


def _blank_frame() -> Image.Image:
    return Image.fromarray(np.zeros((32, 32, 3), dtype=np.uint8))


class StubClient:
    """A VetoVisionClient stub: returns canned text, or raises, per test."""

    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.calls = []

    def query(self, image, instruction, proposed_action):
        self.calls.append((image, instruction, proposed_action))
        if self._exc is not None:
            raise self._exc
        return self._response


# --- parse_veto_response -----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "verdict", "reason"),
    [
        ("CLEAR", "CLEAR", ""),
        ("clear", "CLEAR", ""),
        ("  CLEAR  ", "CLEAR", ""),
        ("CLEAR: path is empty", "CLEAR", "path is empty"),
        ("VETO: pedestrian in crosswalk", "VETO", "pedestrian in crosswalk"),
        ("veto: red signal ahead", "VETO", "red signal ahead"),
    ],
)
def test_parse_veto_response_canonical(text, verdict, reason):
    decision = parse_veto_response(text)
    assert decision.verdict == verdict
    assert decision.reason == reason
    assert decision.raw_response == text


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "maybe",
        "VETO",  # missing reason
        "VETO:",
        "VETO:   ",
        "The action seems fine, CLEAR",  # extra prose, not exact
        "CLEAR\nVETO: also this",  # multiple lines
    ],
)
def test_parse_veto_response_rejects_malformed(text):
    with pytest.raises(VetoParseError):
        parse_veto_response(text)


def test_veto_decision_rejects_invalid_verdict():
    with pytest.raises(ValueError):
        VetoDecision(verdict="MAYBE", reason="x")


# --- HazardVetoAgent -----------------------------------------------------


def test_assess_returns_clear_and_logs_it():
    client = StubClient(response="CLEAR")
    agent = HazardVetoAgent(client)
    decision = agent.assess(_blank_frame(), "go to the door", "move forward 25 cm")
    assert decision.is_clear
    assert agent.log == [decision]
    assert client.calls[0][1:] == ("go to the door", "move forward 25 cm")


def test_assess_returns_veto_with_reason():
    client = StubClient(response="VETO: person crossing")
    agent = HazardVetoAgent(client)
    decision = agent.assess(_blank_frame(), "go forward", "move forward 25 cm")
    assert not decision.is_clear
    assert decision.reason == "person crossing"


def test_assess_defaults_to_veto_on_unparseable_response():
    client = StubClient(response="uh, sure, go ahead I guess")
    agent = HazardVetoAgent(client)
    decision = agent.assess(_blank_frame(), "go forward", "move forward 25 cm")
    assert decision.verdict == "VETO"
    assert "unparseable" in decision.reason


def test_assess_defaults_to_veto_on_client_exception():
    client = StubClient(exc=RuntimeError("network timeout"))
    agent = HazardVetoAgent(client)
    decision = agent.assess(_blank_frame(), "go forward", "move forward 25 cm")
    assert decision.verdict == "VETO"
    assert "network timeout" in decision.reason


def test_assess_never_raises_even_on_client_failure():
    client = StubClient(exc=ValueError("boom"))
    agent = HazardVetoAgent(client)
    # must not propagate -- the tactical loop can't be allowed to crash
    agent.assess(_blank_frame(), "go forward", "move forward 25 cm")


def test_on_decision_callback_fires():
    seen = []
    client = StubClient(response="CLEAR")
    agent = HazardVetoAgent(client, on_decision=seen.append)
    agent.assess(_blank_frame(), "go", "move forward 25 cm")
    assert len(seen) == 1


def test_gate_runs_move_only_when_clear():
    ran = []
    clear_agent = HazardVetoAgent(StubClient(response="CLEAR"))
    veto_agent = HazardVetoAgent(StubClient(response="VETO: hazard"))

    clear_decision = clear_agent.assess(_blank_frame(), "go", "move forward 25 cm")
    assert clear_agent.gate(clear_decision, lambda: ran.append("moved")) is True
    assert ran == ["moved"]

    veto_decision = veto_agent.assess(_blank_frame(), "go", "move forward 25 cm")
    assert veto_agent.gate(veto_decision, lambda: ran.append("should not happen")) is False
    assert ran == ["moved"]
