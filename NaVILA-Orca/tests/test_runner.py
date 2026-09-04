import numpy as np
import pytest

import navila_orca.traffic_crossing as traffic_crossing
from navila_orca.contracts import EpisodeSpec, PhysicsStep, RenderFrame, RobotState
from navila_orca.runner import NavigationRunner, TimingError, duration_to_ticks
from navila_orca.traffic_crossing import traffic_light_crossing_waypoints


def _state(step_id: int, time_s: float, position) -> RobotState:
    zeros3 = np.zeros(3)
    zeros12 = np.zeros(12)
    return RobotState(
        step_id=step_id,
        sim_time_s=time_s,
        root_pos_world=np.asarray(position),
        root_quat_wxyz=np.array([1.0, 0.0, 0.0, 0.0]),
        body_ang_vel=zeros3,
        base_rpy=zeros3,
        joint_pos=zeros12,
        joint_vel=zeros12,
        last_raw_action=zeros12,
    )


class FakePhysics:
    control_dt = 0.02

    def __init__(self):
        self.state = _state(0, 0.0, [0.0, 0.0, 0.0])
        self.qpos_batch = np.zeros((1, 19))
        self.command = None
        self.command_updates = 0

    def reset(self, episode):
        self.state = _state(0, 0.0, episode.start_position)
        return self.state

    def set_velocity_command(self, command):
        self.command = command
        self.command_updates += 1

    def step(self):
        position = self.state.root_pos_world.copy()
        position[0] += self.command.vx * self.control_dt
        self.state = _state(
            self.state.step_id + 1, self.state.sim_time_s + self.control_dt, position
        )
        return self.state

    def close(self):
        pass


class FakeRenderer:
    def __init__(self):
        self.steps = []

    def render(self, state, qpos_batch=None):
        assert qpos_batch.shape == (1, 19)
        self.steps.append(state.step_id)
        rgb = np.full((8, 8, 3), state.step_id % 255, dtype=np.uint8)
        return RenderFrame(
            state.step_id, state.sim_time_s, "ego", rgb, str(state.step_id)
        )

    def close(self):
        pass


class ScriptedVLM:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.requests = []

    def infer(self, images, instruction):
        self.requests.append((images, instruction))
        assert len(images) == 8
        return next(self.outputs)


def _episode():
    return EpisodeSpec(
        episode_id="1",
        scene_id="synthetic",
        instruction="move to the target",
        start_position=np.array([0.0, 0.0, 0.0]),
        start_quat_wxyz=np.array([1.0, 0.0, 0.0, 0.0]),
        goal_position=np.array([0.75, 0.0, 0.0]),
        goal_radius=0.1,
        reference_path=np.array([[0.0, 0.0, 0.0], [0.75, 0.0, 0.0]]),
        gt_locations=np.array([[0.0, 0.0, 0.0], [0.75, 0.0, 0.0]]),
    )


def test_runner_executes_exact_ticks_samples_history_and_stops_normally():
    physics = FakePhysics()
    renderer = FakeRenderer()
    vlm = ScriptedVLM(
        [
            "The next action is move forward 75 cm.",
            "The next action is turn left 30 degrees.",
            "The next action is stop.",
        ]
    )
    result = NavigationRunner(
        physics,
        renderer,
        vlm,
        scene_fidelity=False,
    ).run(_episode())

    assert result.termination_reason == "stop"
    assert result.control_steps == 75 + 50
    assert result.decisions == 3
    assert renderer.steps == [0, 25, 50, 75, 100, 125]
    assert len(vlm.requests) == 3
    assert result.metrics["scene_fidelity"] is False
    assert result.metrics["success"] == 1.0
    assert result.metrics["spl"] == pytest.approx(1.0)
    assert result.metrics["path_length"] == pytest.approx(0.75)
    assert physics.command_updates == 2
    assert len(result.motion_chunks) == 2
    forward, turn = result.motion_chunks
    assert forward.target_distance_m == pytest.approx(0.75)
    assert forward.measured_distance_m == pytest.approx(0.75)
    assert forward.distance_error_m == pytest.approx(0.0)
    assert forward.forward_progress_m == pytest.approx(0.75)
    assert forward.target_forward_velocity_mps == pytest.approx(0.5)
    assert forward.measured_forward_velocity_mps == pytest.approx(0.5)
    assert forward.forward_velocity_error_mps == pytest.approx(0.0)
    assert forward.cumulative_forward_error_m == pytest.approx(0.0)
    assert turn.target_yaw_deg == pytest.approx(30.0)
    assert turn.measured_yaw_deg == pytest.approx(0.0)
    assert turn.yaw_error_deg == pytest.approx(-30.0)
    assert turn.target_yaw_rate_rad_s == pytest.approx(np.pi / 6.0)
    assert turn.measured_yaw_rate_rad_s == pytest.approx(0.0)
    assert turn.yaw_rate_error_rad_s == pytest.approx(-np.pi / 6.0)
    assert turn.cumulative_signed_yaw_error_deg == pytest.approx(-30.0)
    assert turn.cumulative_yaw_magnitude_error_deg == pytest.approx(-30.0)


def test_empty_waypoint_sequence_disables_staged_mode():
    vlm = ScriptedVLM(["stop"])
    result = NavigationRunner(
        FakePhysics(),
        FakeRenderer(),
        vlm,
        scene_fidelity=False,
        waypoint_instructions=(),
    ).run(_episode())

    assert result.termination_reason == "stop"
    assert result.waypoint_count == 0
    assert result.waypoints_completed == 0
    assert vlm.requests[0][1] == "move to the target"


def test_runner_refreshes_file_backed_instruction_between_decisions():
    current = {"instruction": "Move toward the orange bin."}

    class UpdatingVLM(ScriptedVLM):
        def infer(self, images, instruction):
            output = super().infer(images, instruction)
            if len(self.requests) == 1:
                current["instruction"] = "Turn right toward the blue barrel."
            return output

    vlm = UpdatingVLM(["move forward 25 cm", "stop"])
    result = NavigationRunner(
        FakePhysics(),
        FakeRenderer(),
        vlm,
        scene_fidelity=False,
        instruction_provider=lambda: current["instruction"],
    ).run(_episode())

    assert result.termination_reason == "stop"
    assert [request[1] for request in vlm.requests] == [
        "Move toward the orange bin.",
        "Turn right toward the blue barrel.",
    ]


def test_runner_treats_intermediate_waypoint_stops_as_stage_completion():
    physics = FakePhysics()
    vlm = ScriptedVLM(
        [
            "move forward 25 cm",
            "stop",
            "move forward 25 cm",
            "stop",
            "move forward 25 cm",
            "stop",
        ]
    )
    result = NavigationRunner(
        physics,
        FakeRenderer(),
        vlm,
        scene_fidelity=False,
        waypoint_instructions=(
            "Reach the orange bin, then stop.",
            "Reach the blue barrel, then stop.",
            "Reach the yellow truck, then stop.",
        ),
    ).run(_episode())

    assert result.termination_reason == "stop"
    assert result.control_steps == 75
    assert result.decisions == 6
    assert result.waypoints_completed == 3
    assert result.waypoint_count == 3
    assert result.waypoint_stop_rejections == 0
    assert [request[1] for request in vlm.requests] == [
        "Waypoint 1 of 3. Reach the orange bin, then stop.",
        "Waypoint 1 of 3. Reach the orange bin, then stop.",
        "Waypoint 2 of 3. Reach the blue barrel, then stop.",
        "Waypoint 2 of 3. Reach the blue barrel, then stop.",
        "Waypoint 3 of 3. Reach the yellow truck, then stop.",
        "Waypoint 3 of 3. Reach the yellow truck, then stop.",
    ]
    # Two intermediate zero commands plus three forward commands.
    assert physics.command_updates == 5


def test_runner_rejects_consecutive_stop_after_waypoint_switch():
    physics = FakePhysics()
    vlm = ScriptedVLM(
        [
            "move forward 25 cm",
            "stop",
            "stop",
            "move forward 25 cm",
            "stop",
            "move forward 25 cm",
            "stop",
        ]
    )
    result = NavigationRunner(
        physics,
        FakeRenderer(),
        vlm,
        scene_fidelity=False,
        waypoint_instructions=(
            "Reach the orange bin, then stop.",
            "Reach the blue barrel, then stop.",
            "Reach the yellow truck, then stop.",
        ),
    ).run(_episode())

    assert result.termination_reason == "stop"
    assert result.control_steps == 75
    assert result.decisions == 7
    assert result.waypoints_completed == 3
    assert result.waypoint_stop_rejections == 1
    rejected_retry_instruction = vlm.requests[3][1]
    assert rejected_retry_instruction.startswith(
        "Waypoint 2 of 3. Reach the blue barrel, then stop."
    )
    assert "previous stop was rejected" in rejected_retry_instruction


def test_waypoint_requires_five_forward_decisions_before_accepting_stop():
    vlm = ScriptedVLM(
        [
            "move forward 25 cm",
            "move forward 25 cm",
            "stop",
            "move forward 25 cm",
            "turn left 15 degrees",
            "stop",
            "move forward 25 cm",
            "move forward 25 cm",
            "stop",
        ]
    )
    result = NavigationRunner(
        FakePhysics(),
        FakeRenderer(),
        vlm,
        scene_fidelity=False,
        waypoint_instructions=("Reach the crossing center.",),
        min_waypoint_forward_decisions=5,
        max_decisions=20,
    ).run(_episode())

    assert result.termination_reason == "stop"
    assert result.waypoints_completed == 1
    assert result.waypoint_stop_rejections == 2
    assert result.decisions == 9
    assert len(result.motion_chunks) == 6
    assert "0 of 5 required forward motion decisions" in vlm.requests[0][1]
    assert "3 more forward motion decisions" in vlm.requests[3][1]
    assert "2 more forward motion decisions" in vlm.requests[6][1]


def test_duration_conversion_rejects_fractional_control_ticks():
    assert duration_to_ticks(1.5, 0.02) == 75
    with pytest.raises(TimingError):
        duration_to_ticks(0.03, 0.02)


def test_split_renderer_streams_pose_faster_than_vlm_capture():
    class StreamingRenderer:
        def __init__(self):
            self.pushes = []
            self.captures = []

        def push_state(self, state, qpos_batch=None):
            assert qpos_batch.shape == (1, 19)
            self.pushes.append(state.step_id)

        def capture(self, state, qpos_batch=None):
            self.captures.append(state.step_id)
            rgb = np.full((8, 8, 3), state.step_id % 255, dtype=np.uint8)
            return RenderFrame(
                state.step_id, state.sim_time_s, "ego", rgb, str(state.step_id)
            )

        def close(self):
            pass

    physics = FakePhysics()
    renderer = StreamingRenderer()
    vlm = ScriptedVLM(["move forward 25 cm", "stop"])
    result = NavigationRunner(
        physics,
        renderer,
        vlm,
        scene_fidelity=False,
        state_stream_interval_s=0.04,
    ).run(_episode())

    assert result.control_steps == 25
    assert renderer.captures == [0, 25]
    assert renderer.pushes == [0, *range(2, 25, 2), 25]


def test_realtime_visual_sync_paces_every_control_tick():
    sleeps = []

    result = NavigationRunner(
        FakePhysics(),
        FakeRenderer(),
        ScriptedVLM(["move forward 25 cm", "stop"]),
        scene_fidelity=False,
        realtime_visual_sync=True,
        sleep_fn=sleeps.append,
    ).run(_episode())

    assert result.control_steps == 25
    assert sleeps == [pytest.approx(0.02)] * 25


def test_traffic_crossing_recovery_command_moves_forward():
    command = traffic_crossing.premature_stop_recovery_command()

    assert command.vx == pytest.approx(0.5)
    assert command.vy == pytest.approx(0.0)
    assert command.wz == pytest.approx(0.0)
    assert command.duration_s == pytest.approx(0.5)
    assert command.stop is False


def test_rejected_traffic_stop_executes_forward_recovery():
    physics = FakePhysics()
    result = NavigationRunner(
        physics,
        FakeRenderer(),
        ScriptedVLM(["stop", "stop", "stop"]),
        scene_fidelity=False,
        waypoint_instructions=("Reach the crossing center.",),
        min_waypoint_forward_decisions=2,
        premature_stop_recovery_command=(
            traffic_crossing.premature_stop_recovery_command()
        ),
        max_decisions=3,
    ).run(_episode())

    assert result.termination_reason == "stop"
    assert result.waypoint_stop_rejections == 2
    assert result.waypoints_completed == 1
    assert result.control_steps == 50
    assert len(result.motion_chunks) == 2
    assert all(chunk.target_forward_velocity_mps == 0.5 for chunk in result.motion_chunks)
    assert physics.state.root_pos_world[0] == pytest.approx(0.5)


def test_traffic_crossing_waypoints_advance_as_positive_spatial_states():
    waypoints = traffic_light_crossing_waypoints(
        wait_waypoint="curb marker A",
        center_waypoint="zebra center marker B",
        exit_waypoint="far-side marker C",
    )
    vlm = ScriptedVLM(
        [
            "move forward 25 cm",
            "stop",
            "move forward 25 cm",
            "stop",
            "move forward 25 cm",
            "stop",
        ]
    )

    result = NavigationRunner(
        FakePhysics(),
        FakeRenderer(),
        vlm,
        scene_fidelity=False,
        waypoint_instructions=waypoints,
    ).run(_episode())

    assert result.waypoints_completed == 3
    assert result.waypoint_count == 3
    assert "curb marker A" in vlm.requests[0][1]
    assert "zebra center marker B" in vlm.requests[2][1]
    assert "far-side marker C" in vlm.requests[4][1]
    assert all("at least 2 meters" in prompt for prompt in waypoints)
    assert "two traffic-light poles" in waypoints[0]
    assert "white zebra-stripe corridor" in waypoints[1]
    assert "white zebra-stripe corridor" in waypoints[2]
    negative_phrases = ("do not", "don't", "avoid", "never", "must not")
    assert all(
        phrase not in prompt.lower()
        for prompt in waypoints
        for phrase in negative_phrases
    )


def test_auto_reset_terminal_state_does_not_add_reset_teleport_to_metrics():
    class AutoResetPhysics(FakePhysics):
        def step(self):
            reset_state = _state(1, self.control_dt, [100.0, 0.0, 0.0])
            return PhysicsStep(
                reset_state,
                terminated=True,
                info={"auto_reset_state": True},
            )

    physics = AutoResetPhysics()
    result = NavigationRunner(
        physics,
        FakeRenderer(),
        ScriptedVLM(["move forward 25 cm"]),
        scene_fidelity=False,
    ).run(_episode())

    assert result.termination_reason == "terminated"
    assert result.control_steps == 1
    assert result.metrics["path_length"] == 0.0


def test_exact_control_step_limit_does_not_request_an_extra_vlm_action():
    vlm = ScriptedVLM(["move forward 25 cm"])
    result = NavigationRunner(
        FakePhysics(),
        FakeRenderer(),
        vlm,
        scene_fidelity=False,
        max_control_steps=25,
    ).run(_episode())

    assert result.termination_reason == "max_control_steps"
    assert result.control_steps == 25
    assert result.decisions == 1
    assert len(vlm.requests) == 1


def test_zero_limits_run_until_vlm_stop():
    result = NavigationRunner(
        FakePhysics(),
        FakeRenderer(),
        ScriptedVLM(["move forward 25 cm", "stop"]),
        scene_fidelity=False,
        max_control_steps=0,
        max_decisions=0,
    ).run(_episode())

    assert result.termination_reason == "stop"
    assert result.control_steps == 25
    assert result.decisions == 2


def test_live_monitor_refreshes_faster_without_changing_vlm_history_interval():
    class FakeMonitor:
        def __init__(self):
            self.updates = []

        def update(self, frame, **details):
            self.updates.append((frame.step_id, details))

        def run_while_responsive(self, operation):
            return operation()

    monitor = FakeMonitor()
    renderer = FakeRenderer()
    vlm = ScriptedVLM(["move forward 25 cm", "stop"])
    result = NavigationRunner(
        FakePhysics(),
        renderer,
        vlm,
        scene_fidelity=False,
        image_interval_s=0.5,
        monitor=monitor,
        monitor_interval_s=0.1,
    ).run(_episode())

    assert result.termination_reason == "stop"
    assert renderer.steps == [0, 5, 10, 15, 20, 25]
    assert len(vlm.requests) == 2
    assert monitor.updates[-1][1]["status"] == "VLM requested stop"
