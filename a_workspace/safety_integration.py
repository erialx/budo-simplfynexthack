"""Ports A's SafetyWatchdog + HazardVetoAgent into NavigationRunner (runner.py)
WITHOUT editing runner.py, navila_bridge.py, bridge_backends.py, cli.py, or any
other file another teammate owns.

Background (see docs/PLAN.md, A's task list): D's live-demo command runs
through NavigationRunner (NaVILA-Orca/src/navila_orca/runner.py), a separate
CLI-driven loop from C's per-step MCP bridge. That loop has zero
SafetyWatchdog/HazardVetoAgent wiring today, so its WAYPOINT_STOP_OVERRIDE
forward-nudge reflex (runner.py's premature_stop_recovery_command) has nothing
stopping it from overriding a real safety stop. This module closes that gap by
composition, not by patching runner.py.

Why composition works here: NavigationRunner.run() already reads
self.physics, self.vlm_client, and self.premature_stop_recovery_command as
plain pluggable dependencies -- that's precisely the seam its own constructor
was built around. Wrapping those three reproduces the exact behaviour C
already verified for navila_bridge.py's navigate_step (docs/STAGE3_TESTING.md)
-- a watchdog trip or a veto both end the run as a real, non-overridable stop
-- with zero lines changed in any file this module doesn't own.

Three pieces, one shared SafetyState:

- SafeVelocityPhysicsBackend wraps a VelocityPhysicsBackend. It ticks a
  SafetyWatchdog on every physics tick (the reactive ~20Hz layer) and, once
  tripped, freezes the robot in place instead of advancing physics -- the same
  "dog glides, freezes on trip" behaviour already verified live for
  OrcaLabMirrorBackend, reproduced generically for any VelocityPhysicsBackend
  (this covers MjlabGo2Backend; NavigationRunner's other backend shape,
  JointActionPhysicsBackend, is out of scope for this pass -- the project only
  actually uses the velocity-facade path today).
- SafeVLMClient wraps a VLMClient. Once per decision (~1Hz, the tactical
  cadence from CLAUDE.md's architecture) it runs a HazardVetoAgent check
  against the frame the driver just reasoned over, before that decision's
  motion executes. A VETO -- or an already-tripped watchdog -- makes it return
  "stop." regardless of what the wrapped VLM actually said.
- SafeNavigationRunner subclasses NavigationRunner ONLY to override
  premature_stop_recovery_command as a property: when this decision's stop
  came from a trip or a veto, the property reads back None, so runner.py's own
  WAYPOINT_STOP_OVERRIDE reflex (unmodified, inherited as-is) sees "no
  recovery command configured" and just waits for the next decision instead of
  forcing forward motion over a real safety stop. Every other line of run() is
  inherited unchanged -- this subclass adds one property and nothing else.

Use build_safe_runner() to assemble all of this the same way C's
_build_safety_stack/_build_veto_stack assemble the per-step bridge's stack --
see the function's docstring below.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Sequence

from PIL import Image

from navila_orca.actions import ActionParseError, parse_velocity_command
from navila_orca.contracts import PhysicsStep, RobotState, VelocityPhysicsBackend, VLMClient
from navila_orca.decision_logbook import DecisionLogbook
from navila_orca.robot_backend.mock_force_sensor import MockForceSensor
from navila_orca.runner import NavigationRunner
from navila_orca.safety_watchdog import SafeForceBand, SafetyWatchdog
from navila_orca.veto.veto_agent import HazardVetoAgent, VetoVisionClient


@dataclass(slots=True)
class SafetyState:
    """Shared between SafeVelocityPhysicsBackend and SafeVLMClient for one
    episode's worth of decisions.

    ``suppress_override`` is read by SafeNavigationRunner's
    premature_stop_recovery_command property. Both wrappers below can set it;
    nothing else needs to touch it.
    """

    suppress_override: bool = False

    def mark_safety_stop(self) -> None:
        self.suppress_override = True

    def reset_for_next_decision(self) -> None:
        self.suppress_override = False


class SafeVelocityPhysicsBackend:
    """Wraps a VelocityPhysicsBackend, ticking ``watchdog`` on every physics
    tick (runner.py's inner ``for _ in range(command_ticks)`` loop calls
    ``.step()`` once per tick) and freezing the robot in place -- same
    position, incremented step_id/sim_time_s, ``terminated=True`` -- once it
    trips. runner.py's own tick loop already breaks out of its chunk on
    ``step.terminated`` and reports ``termination_reason="terminated"``; this
    wrapper doesn't add a new code path in runner.py, it just makes an
    existing one fire for the right reason.
    """

    def __init__(
        self, inner: VelocityPhysicsBackend, watchdog: SafetyWatchdog, state: SafetyState
    ) -> None:
        self._inner = inner
        self._watchdog = watchdog
        self._state = state
        self._last_state: RobotState | None = None

    @property
    def control_dt(self) -> float:
        return self._inner.control_dt

    @property
    def qpos_batch(self) -> Any:
        return getattr(self._inner, "qpos_batch", None)

    @property
    def interrupted(self) -> bool:
        return bool(getattr(self._inner, "interrupted", False)) or self._watchdog.tripped

    def reset(self, episode: Any | None = None) -> RobotState:
        self._watchdog.reset()
        self._state.reset_for_next_decision()
        state = self._inner.reset(episode)
        self._last_state = state
        return state

    def set_velocity_command(self, command: Any) -> None:
        if self.interrupted:
            # Never re-arm motion on the inner backend while latched -- mirrors
            # EmergencyStopActive's "reject, don't silently drop" rule in
            # robot_backend. In practice SafeVLMClient already turns every
            # decision into "stop." once interrupted, so runner.py never calls
            # this while tripped; this is a defensive second layer.
            return
        self._inner.set_velocity_command(command)

    def step(self) -> PhysicsStep:
        if self.interrupted:
            self._state.mark_safety_stop()
            return self._frozen_step()
        self._watchdog.tick()
        if self.interrupted:
            self._state.mark_safety_stop()
            return self._frozen_step()
        raw = self._inner.step()
        step = raw if isinstance(raw, PhysicsStep) else PhysicsStep(raw)
        self._last_state = step.state
        return step

    def _frozen_step(self) -> PhysicsStep:
        if self._last_state is None:
            raise RuntimeError(
                "SafeVelocityPhysicsBackend.step() called before reset()"
            )
        frozen = replace(
            self._last_state,
            step_id=self._last_state.step_id + 1,
            sim_time_s=self._last_state.sim_time_s + self.control_dt,
        )
        self._last_state = frozen
        return PhysicsStep(state=frozen, terminated=True)

    def close(self) -> None:
        self._inner.close()


class SafeVLMClient:
    """Wraps a VLMClient, gating each decision through a HazardVetoAgent
    before runner.py's own action_parser ever sees the text.

    Deliberately does not touch runner.py's action_parser at all -- vetoing
    happens one layer earlier, on the raw VLM text, so a VETO simply looks to
    runner.py like the VLM itself said "stop.", the exact same code path an
    ordinary VLM-issued stop takes (minus the override reflex, suppressed via
    SafetyState -- see SafeNavigationRunner below).
    """

    def __init__(
        self,
        inner: VLMClient,
        veto_agent: HazardVetoAgent,
        physics: SafeVelocityPhysicsBackend,
        state: SafetyState,
        *,
        action_parser: Callable[[str], Any] = parse_velocity_command,
    ) -> None:
        self._inner = inner
        self._veto_agent = veto_agent
        self._physics = physics
        self._state = state
        self._parse = action_parser

    def infer(self, images: Sequence[Image.Image], instruction: str) -> str:
        self._state.reset_for_next_decision()

        if self._physics.interrupted:
            self._state.mark_safety_stop()
            return "stop."

        raw = self._inner.infer(images, instruction)

        try:
            command = self._parse(raw)
        except ActionParseError:
            # Not this wrapper's job to fix malformed VLM output. runner.py's
            # own action_parser will raise on this exact text exactly as it
            # would without this wrapper -- nothing to veto if we can't even
            # tell what the proposed action is.
            return raw

        if command.stop:
            return raw  # already a stop; nothing to veto

        if not images:
            return raw

        decision = self._veto_agent.assess(images[-1], instruction, raw)
        if not decision.is_clear:
            self._state.mark_safety_stop()
            return "stop."
        return raw


class SafeNavigationRunner(NavigationRunner):
    """NavigationRunner with exactly one change: premature_stop_recovery_command
    becomes a property gated on SafetyState.suppress_override.

    Every other attribute and every line of run() is inherited unchanged from
    NavigationRunner -- this class does not override run().
    """

    def __init__(self, *args: Any, safety_state: SafetyState, **kwargs: Any) -> None:
        self._safety_state = safety_state
        self._configured_premature_stop_recovery_command: Any = None
        super().__init__(*args, **kwargs)

    @property
    def premature_stop_recovery_command(self) -> Any:
        if self._safety_state.suppress_override:
            return None
        return self._configured_premature_stop_recovery_command

    @premature_stop_recovery_command.setter
    def premature_stop_recovery_command(self, value: Any) -> None:
        self._configured_premature_stop_recovery_command = value


def build_safe_runner(
    physics: VelocityPhysicsBackend,
    renderer: Any,
    vlm_client: VLMClient,
    veto_client: VetoVisionClient,
    *,
    band: SafeForceBand | None = None,
    debounce_ticks: int = 3,
    force_reader: Callable[[], float] | None = None,
    logbook: DecisionLogbook | None = None,
    **runner_kwargs: Any,
) -> tuple[SafeNavigationRunner, SafetyState, SafetyWatchdog, HazardVetoAgent, DecisionLogbook]:
    """Assemble a SafeNavigationRunner the same way C assembled
    _build_safety_stack / _build_veto_stack for navila_bridge.py's per-step
    loop (see navila_bridge.py and docs/STAGE3_TESTING.md) -- but for
    runner.py's CLI-driven loop instead.

    ``physics`` is the real, unwrapped backend (e.g. MjlabGo2Backend) -- pass
    it here, not something already wrapped; this function does the wrapping.
    It must support ``.emergency_stop()`` (MjlabGo2Backend does -- see its
    "Safety-watchdog seam" comment); NavigationRunner's Protocol doesn't
    declare that method since it's an optional extension, same as
    navila_bridge.py's own ``hasattr(self.backend, "emergency_stop")`` check.

    ``force_reader`` defaults to a fresh MockForceSensor's ``.read`` -- the
    mocked-this-week harness sensor from CLAUDE.md -- since physics backends
    like MjlabGo2Backend have no real harness-force reading of their own.

    Any keyword NavigationRunner itself accepts (max_decisions,
    waypoint_instructions, premature_stop_recovery_command, ...) can be passed
    through via **runner_kwargs exactly as you'd construct a plain
    NavigationRunner.

    Returns the runner plus the watchdog, veto agent, and logbook so a caller
    can inspect ``.tripped`` / ``.log`` / ``.dump()`` the same way the
    per-step bridge's status tools do.
    """

    if logbook is None:
        logbook = DecisionLogbook()
    if force_reader is None:
        force_reader = MockForceSensor().read

    state = SafetyState()
    watchdog = SafetyWatchdog(
        physics,
        band=band,
        debounce_ticks=debounce_ticks,
        force_reader=force_reader,
        on_trip=logbook.record_watchdog_trip,
    )
    safe_physics = SafeVelocityPhysicsBackend(physics, watchdog, state)
    veto_agent = HazardVetoAgent(veto_client, on_decision=logbook.record_veto_decision)
    safe_vlm = SafeVLMClient(vlm_client, veto_agent, safe_physics, state)

    runner = SafeNavigationRunner(
        safe_physics,
        renderer,
        safe_vlm,
        safety_state=state,
        **runner_kwargs,
    )
    return runner, state, watchdog, veto_agent, logbook
