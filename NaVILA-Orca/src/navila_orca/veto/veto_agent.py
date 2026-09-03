"""The Hazard Veto Agent -- this project's differentiator.

Tactical, ~1Hz. One vision call per step: "given this frame, is the proposed action
unsafe right now?" -> VETO/CLEAR + one-sentence reason. Gates whether the Driver's
next ``Move()`` actually goes out.

Mirrors the split already used for NaVILA's own inference
(:class:`navila_orca.contracts.VLMClient` returns raw text,
:mod:`navila_orca.actions` parses it strictly): :class:`VetoVisionClient` is a thin
Protocol that returns raw text from whatever vision call backs it, and
:func:`parse_veto_response` is a strict, hard-failing parser -- same philosophy as
``actions.py``: never silently guess. Unlike the Driver's action grammar, though,
a veto response that fails to parse (or a vision call that raises) must default to
VETO, not to some neutral "no-op" -- an unreadable answer from the safety layer is
exactly the situation this agent exists to be conservative about.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Protocol, runtime_checkable

from PIL import Image


class VetoParseError(ValueError):
    """Raised when a vision response cannot be mapped to VETO or CLEAR."""


_CLEAR_RE = re.compile(r"^clear\s*(?::\s*(?P<reason>.+))?$", re.IGNORECASE | re.DOTALL)
_VETO_RE = re.compile(r"^veto\s*:\s*(?P<reason>.+)$", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True, slots=True)
class VetoDecision:
    """One veto call's outcome, ready to be printed by the decision logbook."""

    verdict: str  # "VETO" or "CLEAR"
    reason: str
    raw_response: str = ""

    def __post_init__(self) -> None:
        if self.verdict not in ("VETO", "CLEAR"):
            raise ValueError(f"verdict must be 'VETO' or 'CLEAR', got {self.verdict!r}")

    @property
    def is_clear(self) -> bool:
        return self.verdict == "CLEAR"


def parse_veto_response(text: str) -> VetoDecision:
    """Strictly parse a vision response into a :class:`VetoDecision`.

    Expects exactly ``"CLEAR"``, ``"CLEAR: <reason>"``, or ``"VETO: <reason>"``
    (case-insensitive, surrounding whitespace ignored). Anything else -- extra
    prose, a missing reason on a VETO, a response that isn't one of the two
    tokens -- raises :class:`VetoParseError`. Callers must not treat a parse
    failure as CLEAR; see :meth:`HazardVetoAgent.assess`, which defaults to VETO.
    """

    if not isinstance(text, str) or not text.strip():
        raise VetoParseError("veto response is empty")

    stripped = text.strip()

    clear_match = _CLEAR_RE.match(stripped)
    if clear_match:
        reason = (clear_match.group("reason") or "").strip()
        return VetoDecision(verdict="CLEAR", reason=reason, raw_response=text)

    veto_match = _VETO_RE.match(stripped)
    if veto_match:
        reason = veto_match.group("reason").strip()
        if not reason:
            raise VetoParseError("VETO response is missing a reason")
        return VetoDecision(verdict="VETO", reason=reason, raw_response=text)

    raise VetoParseError(f"response is neither CLEAR nor VETO: {text!r}")


@runtime_checkable
class VetoVisionClient(Protocol):
    """A vision call that judges one proposed action against one frame.

    Implementations return raw text (see :func:`parse_veto_response` for the
    expected shape) rather than a parsed decision, matching
    :class:`navila_orca.contracts.VLMClient`'s split between "call the model" and
    "parse the result."
    """

    def query(self, image: Image.Image, instruction: str, proposed_action: str) -> str: ...


class HazardVetoAgent:
    """Owns the call-parse-gate sequence for one tactical tick.

    Standalone: takes any :class:`VetoVisionClient`, so it can be built and tested
    against a stub client and static test images (see ``tests/test_veto_agent.py``)
    before the real camera pipeline or an ``anthropic`` API key exists -- swap in
    :class:`navila_orca.veto.claude_vision_client.AnthropicVetoVisionClient` later
    without changing anything here.
    """

    def __init__(
        self,
        client: VetoVisionClient,
        *,
        on_decision: Callable[[VetoDecision], None] | None = None,
    ) -> None:
        self._client = client
        self._on_decision = on_decision
        self._log: list[VetoDecision] = []

    @property
    def log(self) -> list[VetoDecision]:
        """A copy of every decision made so far -- the decision logbook's source."""
        return list(self._log)

    def assess(
        self, frame: Image.Image, instruction: str, proposed_action: str
    ) -> VetoDecision:
        """Run one vision call and return a decision. Never raises.

        A vision-client exception or an unparseable response both default to
        VETO with a reason explaining why, rather than propagating an exception
        (which would crash the loop) or defaulting to CLEAR (which would be
        exactly backwards for a safety gate).
        """
        try:
            raw = self._client.query(frame, instruction, proposed_action)
        except Exception as exc:  # noqa: BLE001 - any client failure must default to VETO
            decision = VetoDecision(
                verdict="VETO",
                reason=f"vision client error, defaulting to VETO: {exc}",
                raw_response="",
            )
        else:
            try:
                decision = parse_veto_response(raw)
            except VetoParseError as exc:
                decision = VetoDecision(
                    verdict="VETO",
                    reason=f"unparseable veto response, defaulting to VETO: {exc}",
                    raw_response=raw,
                )
        self._log.append(decision)
        if self._on_decision is not None:
            self._on_decision(decision)
        return decision

    def gate(self, decision: VetoDecision, move: Callable[[], None]) -> bool:
        """Call ``move`` only if ``decision`` is CLEAR. Returns whether it ran.

        ``move`` is a zero-argument callable so the caller decides what "the
        Driver's next Move()" means, e.g. ``agent.gate(decision, lambda:
        backend.move(vx, vy, vyaw))``.
        """
        if decision.is_clear:
            move()
            return True
        return False
