from __future__ import annotations

import asyncio
from dataclasses import dataclass

import numpy as np

from navila_orca.contracts import PhysicsStep, RobotState, VelocityCommand


@dataclass
class _EventLog:
    events: list[str]


class _FakeInner:
    def __init__(self, log: _EventLog | None = None) -> None:
        self.control_dt = 0.02
        self.num_envs = 1
        self.interrupted = False
        self.joint_qpos_addr = {f"joint_{index}": index + 7 for index in range(12)}
        self.qpos_batch = np.zeros((1, 19), dtype=np.float64)
        self.qpos_batch[0, 2] = 0.42
        self.qpos_batch[0, 3] = 1.0
        self._step_id = 0
        self._log = log

    def start(self) -> None:
        if self._log:
            self._log.events.append("inner.start")

    def reset(self, episode=None) -> RobotState:
        del episode
        self._step_id = 0
        return self._state()

    def set_velocity_command(self, command: VelocityCommand) -> None:
        self.command = command

    def step(self) -> PhysicsStep:
        self._step_id += 1
        self.qpos_batch[0, 7:] = np.arange(12, dtype=np.float64) + self._step_id
        return PhysicsStep(
            state=self._state(),
            reward=0.0,
            terminated=False,
            truncated=False,
            info={"fake": True},
        )

    def emergency_stop(self) -> None:
        self.interrupted = True

    def close(self) -> None:
        if self._log:
            self._log.events.append("inner.close")

    def _state(self) -> RobotState:
        return RobotState(
            step_id=self._step_id,
            sim_time_s=self._step_id * self.control_dt,
            root_pos_world=self.qpos_batch[0, :3].copy(),
            root_quat_wxyz=self.qpos_batch[0, 3:7].copy(),
            body_ang_vel=np.zeros(3),
            base_rpy=np.zeros(3),
            joint_pos=self.qpos_batch[0, 7:].copy(),
            joint_vel=np.zeros(12),
            last_raw_action=np.zeros(12),
        )


class _FakeRenderer:
    def __init__(self, log: _EventLog | None = None, **kwargs) -> None:
        self.kwargs = kwargs
        self.pushes: list[tuple[RobotState, np.ndarray]] = []
        self._log = log

    def push_state(self, state: RobotState, qpos_batch: np.ndarray) -> None:
        self.pushes.append((state, np.array(qpos_batch, copy=True)))

    def close(self) -> None:
        if self._log:
            self._log.events.append("renderer.close")


def test_orcalab_render_pushes_full_qpos_after_reset_and_every_step():
    from bridge_backends import OrcaLabRenderBackend

    inner = _FakeInner()
    renderer = _FakeRenderer()
    backend = OrcaLabRenderBackend(inner=inner, renderer=renderer)

    backend.start()
    reset_state = backend.reset()
    result = backend.step()

    assert renderer.pushes[0][0] is reset_state
    assert renderer.pushes[0][1].shape == (1, 19)
    assert renderer.pushes[1][0] is result.state
    np.testing.assert_array_equal(renderer.pushes[1][1][0, 7:], np.arange(12) + 1)
    assert result.info == {"fake": True}


def test_orcalab_render_assembles_renderer_like_cli_and_delegates_safety():
    from bridge_backends import OrcaLabRenderBackend

    inner = _FakeInner()
    made: list[_FakeRenderer] = []

    def factory(**kwargs):
        renderer = _FakeRenderer(**kwargs)
        made.append(renderer)
        return renderer

    backend = OrcaLabRenderBackend(
        inner=inner,
        renderer_factory=factory,
        orcagym_address="127.0.0.1:50051",
        camera_port=7070,
        robot_actor_name="auto",
    )
    backend.start()
    backend.reset()

    assert made[0].kwargs["joint_qpos_addr"] == inner.joint_qpos_addr
    assert made[0].kwargs["num_envs"] == 1
    assert made[0].kwargs["agent_name"] is None
    assert made[0].kwargs["discover_agents"] is True
    assert made[0].kwargs["publish"] is False
    assert made[0].kwargs["anchor_to_scene"] is True
    push_only_camera = made[0].kwargs["camera_factory"]("unused", 7070)
    assert push_only_camera.pull_capture is True
    push_only_camera.start()
    push_only_camera.stop()
    assert backend.control_dt == inner.control_dt

    backend.emergency_stop()
    assert backend.interrupted is True


def test_orcalab_render_closes_renderer_before_physics():
    from bridge_backends import OrcaLabRenderBackend

    log = _EventLog([])
    backend = OrcaLabRenderBackend(
        inner=_FakeInner(log), renderer=_FakeRenderer(log)
    )
    backend.close()
    assert log.events == ["renderer.close", "inner.close"]


class _LoopBoundRenderer:
    """Mimics ``OrcaLabBatchRenderer``: owns an asyncio loop created at
    construction and drives it with ``run_until_complete`` on every push.

    On a thread that already has a *running* event loop this raises
    ``RuntimeError('Cannot run the event loop while another loop is running')``
    -- the exact failure ``navila_start_episode`` hit for ``orcalab-render``.
    """

    def __init__(self, log: _EventLog | None = None, **kwargs) -> None:
        self.kwargs = kwargs
        self.pushes: list[tuple[RobotState, np.ndarray]] = []
        self._log = log
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def push_state(self, state: RobotState, qpos_batch: np.ndarray) -> None:
        async def _noop() -> None:
            return None

        self.loop.run_until_complete(_noop())
        self.pushes.append((state, np.array(qpos_batch, copy=True)))

    def close(self) -> None:
        self.loop.close()


def test_orcalab_render_survives_being_called_from_a_running_event_loop():
    """Regression: FastMCP runs the sync ``navila_start_episode`` tool inline on
    its running event-loop thread. The backend must marshal renderer work onto
    a loop-free thread so ``run_until_complete`` does not blow up."""
    from bridge_backends import OrcaLabRenderBackend

    inner = _FakeInner()
    backend = OrcaLabRenderBackend(inner=inner, renderer_factory=_LoopBoundRenderer)

    async def drive() -> RobotState:
        # A live loop is running on this very thread, exactly like FastMCP.
        backend.start()
        state = backend.reset()
        backend.step()
        return state

    reset_state = asyncio.run(drive())

    assert reset_state.step_id == 0
    renderer = backend._renderer
    assert len(renderer.pushes) == 2  # once after reset, once after the step
    backend.close()


def test_orcalab_render_factory_uses_bundled_go2_flat_checkpoint(monkeypatch):
    from bridge_backends import OrcaLabRenderBackend, make_backend
    from navila_orca.backends import mjlab_go2

    captured = {}

    class FakeMjlab(_FakeInner):
        def __init__(self, **kwargs) -> None:
            super().__init__()
            captured.update(kwargs)

    monkeypatch.setattr(mjlab_go2, "MjlabGo2Backend", FakeMjlab)
    backend = make_backend("orcalab-render", device="cuda:0")

    assert isinstance(backend, OrcaLabRenderBackend)
    assert str(captured["checkpoint"]).replace("\\", "/").endswith(
        "/assets/checkpoints/go2_flat.pt"
    )
    assert captured["device"] == "cuda:0"
    assert captured["num_envs"] == 1
