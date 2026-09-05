"""Proves the safety wiring actually works through the REAL NavigationRunner,
REAL SafetyWatchdog, and REAL HazardVetoAgent -- only the physics backend, the
renderer, and the driver VLM are faked, matching how runner.py's own
constructor treats those three as pluggable dependencies.

Run with: PYTHONPATH=<repo>/NaVILA-Orca/src python3 -m pytest, same as every
other test in this directory (this module used to live under a_workspace/ as
A's pre-review scratch space -- now landed into navila_orca proper and wired
into cli.py, see safety_integration.py and cli.py's --safety/--veto-client
flags).
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from navila_orca.contracts import (
    EpisodeSpec,
    PhysicsStep,
    RenderFrame,
    RobotState,
    VelocityCommand,
)
from navila_orca.robot_backend.mock_force_sensor import MockForceSensor
from navila_orca.safety_watchdog import SafeForceBand

from navila_orca.safety_integration import build_safe_runner


# --- fakes: only the three seams NavigationRunner already treats as pluggable ---


class FakeVelocityPhysicsBackend:
    """A trivial straight-line unicycle model -- just enough for run() to have
    something real to step through. Deliberately NOT a robot_backend.MockBackend
    (that's a different Protocol, for the per-step bridge) -- this implements
    navila_orca.contracts.VelocityPhysicsBackend, the Protocol runner.py
    actually uses, with a fake .emergency_stop()/.interrupted pair matching
    MjlabGo2Backend's real "safety-watchdog seam" shape.
    """

    def __init__(self, control_dt: float = 0.1) -> None:
        self.control_dt = control_dt
        self.qpos_batch = None
        self.interrupted = False
        self._step_id = 0
        self._x = 0.0
        self._vx = 0.0

    def reset(self, episode=None) -> RobotState:
        self.interrupted = False
        self._step_id = 0
        self._x = 0.0
        self._vx = 0.0
        return self._state()

    def set_velocity_command(self, command: VelocityCommand) -> None:
        self._vx = command.vx

    def step(self) -> PhysicsStep:
        self._step_id += 1
        self._x += self._vx * self.control_dt
        return PhysicsStep(state=self._state())

    def emergency_stop(self) -> None:
        self.interrupted = True

    def close(self) -> None:
        pass

    def _state(self) -> RobotState:
        return RobotState(
            step_id=self._step_id,
            sim_time_s=self._step_id * self.control_dt,
            root_pos_world=np.array([self._x, 0.0, 0.0]),
            root_quat_wxyz=np.array([1.0, 0.0, 0.0, 0.0]),
            body_ang_vel=np.zeros(3),
            base_rpy=np.zeros(3),
            joint_pos=np.zeros(1),
            joint_vel=np.zeros(1),
            last_raw_action=np.zeros(1),
        )


class FakeRenderBridge:
    def render(self, state: RobotState, qpos_batch=None) -> RenderFrame:
        return RenderFrame(
            step_id=state.step_id,
            sim_time_s=state.sim_time_s,
            camera_id="fake",
            rgb=np.zeros((4, 4, 3), dtype=np.uint8),
        )

    def close(self) -> None:
        pass


class ScriptedVLMClient:
    """The 'inner' driver VLM SafeVLMClient wraps -- returns each scripted
    string in order, one per infer() call."""

    def __init__(self, script):
        self._script = list(script)

    def infer(self, images, instruction) -> str:
        return self._script.pop(0)


class StubVetoClient:
    """Returns each scripted verdict string in order via .query()."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def query(self, image, instruction, proposed_action):
        self.calls.append((instruction, proposed_action))
        return self._script.pop(0)


def _episode(instruction="go forward") -> EpisodeSpec:
    return EpisodeSpec(
        episode_id="ep1",
        scene_id="scene1",
        instruction=instruction,
        start_position=np.zeros(3),
        start_quat_wxyz=np.array([1.0, 0.0, 0.0, 0.0]),
        goal_position=np.array([100.0, 0.0, 0.0]),  # far away -- never "reached"
        goal_radius=0.1,
        reference_path=np.zeros((2, 3)),
        gt_locations=np.zeros((2, 3)),
    )


# --- tests ---------------------------------------------------------------


def test_normal_run_is_unaffected_by_the_safety_wrapping():
    physics = FakeVelocityPhysicsBackend()
    vlm = ScriptedVLMClient(["move forward 25 cm", "stop."])
    veto_client = StubVetoClient(["CLEAR"])

    runner, state, watchdog, veto_agent, logbook = build_safe_runner(
        physics,
        FakeRenderBridge(),
        vlm,
        veto_client,
        max_decisions=10,
        scene_fidelity=False,
        state_stream_interval_s=0.1,
    )

    result = runner.run(_episode())

    assert result.termination_reason == "stop"
    assert not watchdog.tripped
    assert result.final_state.root_pos_world[0] > 0.0  # it actually moved
    assert not state.suppress_override


def test_watchdog_trip_freezes_the_robot_and_ends_the_run():
    physics = FakeVelocityPhysicsBackend()
    sensor = MockForceSensor()
    sensor.schedule_drop(at_step=2, duration_steps=100)  # drops mid-chunk
    vlm = ScriptedVLMClient(["move forward 75cm"])  # a long chunk (1.5s / 15 ticks)
    veto_client = StubVetoClient(["CLEAR"] * 20)

    runner, state, watchdog, veto_agent, logbook = build_safe_runner(
        physics,
        FakeRenderBridge(),
        vlm,
        veto_client,
        band=SafeForceBand(low=20.0, high=80.0),
        debounce_ticks=2,
        force_reader=sensor.read,
        max_decisions=10,
        scene_fidelity=False,
        state_stream_interval_s=0.1,
    )

    result = runner.run(_episode())

    assert watchdog.tripped
    assert result.termination_reason == "terminated"
    # tripped partway through a 15-tick chunk -- it must not have run all 15
    assert result.control_steps < 15
    # frozen in place: position at the moment of the trip, not still advancing
    frozen_x = result.final_state.root_pos_world[0]
    assert frozen_x < 0.75  # nowhere near the full 25 cm/s * 1.5s = 0.375m... 
    assert any(entry.kind == "STOP" for entry in logbook.entries())


def test_veto_stops_the_decision_before_any_physics_runs():
    physics = FakeVelocityPhysicsBackend()
    vlm = ScriptedVLMClient(["move forward 25 cm"])
    veto_client = StubVetoClient(["VETO: person in the crosswalk"])

    runner, state, watchdog, veto_agent, logbook = build_safe_runner(
        physics, FakeRenderBridge(), vlm, veto_client, max_decisions=5, scene_fidelity=False,
        state_stream_interval_s=0.1,
    )

    result = runner.run(_episode())

    assert result.termination_reason == "stop"
    assert result.control_steps == 0
    assert result.final_state.root_pos_world[0] == pytest.approx(0.0)
    assert any(entry.kind == "VETO" for entry in logbook.entries())


def test_veto_suppresses_override_but_a_genuine_stop_still_gets_nudged():
    """The actual gap this module closes: a veto'd decision must not be
    overridden by WAYPOINT_STOP_OVERRIDE, but an ordinary premature VLM stop
    (nothing wrong, just early) must still work exactly as D designed it."""

    physics = FakeVelocityPhysicsBackend()
    # Decision 1: proposes forward motion -> vetoed -> becomes "stop." ->
    #   override must be suppressed (waypoint isn't done yet) -> no motion.
    # Decision 2: VLM itself says stop (nothing to veto, command already
    #   stop) -> override is NOT suppressed -> forced forward chunk runs.
    # Decision 3: waypoint's forward requirement (1) is now met -> stop
    #   actually ends the episode.
    vlm = ScriptedVLMClient(["move forward 25 cm", "stop.", "stop."])
    veto_client = StubVetoClient(["VETO: hazard"])

    recovery = VelocityCommand(vx=0.5, vy=0.0, wz=0.0, duration_s=0.5)
    runner, state, watchdog, veto_agent, logbook = build_safe_runner(
        physics,
        FakeRenderBridge(),
        vlm,
        veto_client,
        max_decisions=10,
        waypoint_instructions=["reach the door"],
        min_waypoint_forward_decisions=1,
        premature_stop_recovery_command=recovery,
        scene_fidelity=False,
        state_stream_interval_s=0.1,
    )

    result = runner.run(_episode())

    # The veto'd decision never moved the robot (override was suppressed).
    # The *second* decision's genuine stop got nudged forward by `recovery`
    # (0.5 m/s * 0.5s = 0.25m), and only the third stop actually ended it.
    assert result.final_state.root_pos_world[0] == pytest.approx(0.25, abs=1e-9)
    assert result.termination_reason == "stop"
    # Both the suppressed veto-stop (decision 1) and the later genuine
    # premature stop (decision 2, overridden) count as rejections -- 2 total.
    assert result.waypoint_stop_rejections == 2
    assert any(entry.kind == "VETO" for entry in logbook.entries())
