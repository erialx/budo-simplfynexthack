"""Backend + VLM seams for the per-step MCP bridge (Stage 1 of PLAN.md).

`navila_bridge.py`'s per-step tools (`navila_navigate_step`, `navila_get_status`,
`navila_emergency_stop`) depend ONLY on the two Protocols defined here --
``StepBackend`` and ``StepVLM`` -- never on a concrete physics or VLM class.

This module also ships mock implementations so the whole per-step loop is
runnable and testable *today*, before:
  * A's ``RobotBackend`` / ``MockBackend`` land, and
  * the AWS SSM tunnel + GPU physics are available.

Contract for A (Stage 1 hand-off)
---------------------------------
A real backend must satisfy ``StepBackend``. ``MjlabGo2Backend`` already provides
``start`` / ``reset`` / ``set_velocity_command`` / ``step`` / ``control_dt`` /
``close`` with matching semantics. The only additions the bridge looks for are
``emergency_stop()`` and the ``interrupted`` flag -- both accessed through
``getattr`` and treated as optional, so a backend without them still works (the
bridge falls back to latching a zero ``VelocityCommand``).

Selection is by environment variable so nothing here has to change per machine:
  * ``NAVILA_BRIDGE_BACKEND``  -> ``mock`` (default) | ``mjlab`` |
    ``orcalab`` | ``orcalab-mock`` | ``orcalab-render``
  * ``NAVILA_BRIDGE_VLM``      -> ``mock`` (default) | ``tcp``
  * ``NAVILA_BRIDGE_VLM_SCRIPT`` -> optional ';'-separated action phrases for the
    mock VLM, e.g. "move forward by 75 cm; turn left by 30 degrees; stop"
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

# The per-step bridge needs the real navigation contracts (RobotState etc.).
# Phase 1's script-wrapping tools do not import this module, so adding the repo
# 'src' dir here does not affect them.
_NAVILA_SRC = Path(__file__).resolve().parent / "NaVILA-Orca" / "src"
if _NAVILA_SRC.is_dir() and str(_NAVILA_SRC) not in sys.path:
    sys.path.insert(0, str(_NAVILA_SRC))

import numpy as np

from navila_orca.contracts import PhysicsStep, RobotState, VelocityCommand

DEFAULT_CONTROL_DT = 0.02  # Go2 policy tick: 5 ms sim timestep x decimation 4.


# ---------------------------------------------------------------------------
# Seams
# ---------------------------------------------------------------------------

@runtime_checkable
class StepBackend(Protocol):
    """One locomotion backend, stepped one policy tick at a time."""

    control_dt: float

    def start(self) -> None: ...

    def reset(self, episode: Any | None = None) -> RobotState: ...

    def set_velocity_command(self, command: VelocityCommand) -> None: ...

    def step(self) -> "RobotState | PhysicsStep": ...

    def close(self) -> None: ...

    # Optional (Stage 3 Safety Watchdog seam) -- accessed via getattr:
    #   def emergency_stop(self) -> None: ...
    #   interrupted: bool


@runtime_checkable
class StepVLM(Protocol):
    """Given the current context, return one NaVILA action phrase (raw text)."""

    def next_action(
        self,
        *,
        instruction: str,
        state: RobotState,
        frames: Sequence[Any],
        goal_xy: "tuple[float, float] | None" = None,
    ) -> str: ...


# ---------------------------------------------------------------------------
# Mock backend -- planar unicycle kinematics, no GPU, no MuJoCo.
# ---------------------------------------------------------------------------

def _yaw_to_quat_wxyz(yaw: float) -> np.ndarray:
    half = yaw / 2.0
    return np.array([math.cos(half), 0.0, 0.0, math.sin(half)])


class MockBackend:
    """Deterministic planar stand-in for ``MjlabGo2Backend``.

    Integrates the latched body-frame velocity command with a simple unicycle
    model. Good enough to exercise the per-step loop, goal-reaching, timeouts,
    emergency-stop, and physics-termination handling.
    """

    def __init__(
        self,
        *,
        control_dt: float = DEFAULT_CONTROL_DT,
        start_xy: tuple[float, float] = (0.0, 0.0),
        start_yaw: float = 0.0,
        z_height: float = 0.42,
        terminate_after_steps: int | None = None,
    ) -> None:
        self.control_dt = float(control_dt)
        self._start = (float(start_xy[0]), float(start_xy[1]), float(start_yaw))
        self._z = float(z_height)
        self._terminate_after_steps = terminate_after_steps
        self.interrupted = False
        self.started = False
        self._reset_pose()

    # -- lifecycle ------------------------------------------------------------
    def _reset_pose(self) -> None:
        self._x, self._y, self._yaw = self._start
        self._vx = self._vy = self._wz = 0.0
        self._step_id = 0

    def start(self) -> None:
        self.started = True

    def reset(self, episode: Any | None = None) -> RobotState:
        del episode
        self.started = True
        self.interrupted = False
        self._reset_pose()
        return self._state()

    def close(self) -> None:
        self.started = False

    # -- control ------------------------------------------------------------
    def set_velocity_command(self, command: VelocityCommand) -> None:
        self._vx = float(command.vx)
        self._vy = float(command.vy)
        self._wz = float(command.wz)

    def emergency_stop(self) -> None:
        """Stage 3 seam: latch zero velocity and raise the interrupt flag."""
        self._vx = self._vy = self._wz = 0.0
        self.interrupted = True

    def step(self) -> PhysicsStep:
        if not self.started:
            raise RuntimeError("MockBackend.step() before start()/reset()")
        dt = self.control_dt
        self._yaw += self._wz * dt
        # body-frame (vx forward, vy left) -> world
        cos_y, sin_y = math.cos(self._yaw), math.sin(self._yaw)
        self._x += (self._vx * cos_y - self._vy * sin_y) * dt
        self._y += (self._vx * sin_y + self._vy * cos_y) * dt
        self._step_id += 1
        terminated = (
            self._terminate_after_steps is not None
            and self._step_id >= self._terminate_after_steps
        )
        return PhysicsStep(
            state=self._state(),
            reward=0.0,
            terminated=bool(terminated),
            truncated=False,
            info={"auto_reset_state": bool(terminated), "mock": True},
        )

    # -- state ------------------------------------------------------------
    def _state(self) -> RobotState:
        return RobotState(
            step_id=self._step_id,
            sim_time_s=self._step_id * self.control_dt,
            root_pos_world=np.array([self._x, self._y, self._z]),
            root_quat_wxyz=_yaw_to_quat_wxyz(self._yaw),
            body_ang_vel=np.array([0.0, 0.0, self._wz]),
            base_rpy=np.array([0.0, 0.0, self._yaw]),
            joint_pos=np.zeros(12),
            joint_vel=np.zeros(12),
            last_raw_action=np.zeros(12),
        )


# ---------------------------------------------------------------------------
# OrcaLab pose mirror -- makes the per-step loop visible in the OrcaLab GUI
# ---------------------------------------------------------------------------

def _load_orca_edit_runtime():
    """Lazily import the three OrcaLab edit-service symbols the mirror needs.

    Kept separate from navila_orca.render.orca_camera's fuller loader so this
    module has no hard dependency on it (and so a plain import stays cheap).
    """

    import importlib

    try:
        transform_mod = importlib.import_module("orcalab.transform")
    except ModuleNotFoundError:
        transform_mod = importlib.import_module("orcalab.math")
    path_mod = importlib.import_module("orcalab.path")
    wrapper_mod = importlib.import_module("orcalab.protos.edit_service_wrapper")
    return wrapper_mod.EditServiceWrapper, transform_mod.Transform, path_mod.Path


class OrcaLabMirrorBackend:
    """A ``StepBackend`` that runs physics in an inner backend and mirrors the
    robot's world pose into a running OrcaLab scene after every step, so the
    per-step MCP loop is visible in the OrcaLab GUI.

    Pose-only: pushes root translation + wxyz orientation via the edit service's
    ``set_actor_transform_batch`` (A's verified path). Leg joints are NOT
    articulated -- the dog glides rather than walks. Real gait + ego-camera
    frames need ``OrcaLabRenderBridge`` (Phase 2 / open item); this is the
    minimal "see the dog move, see it freeze on an e-stop" bridge for the
    Stage 2 demo.

    OrcaLab is a passive viewer here: if the edit service is unreachable or the
    actor path is wrong, mirroring disables itself (logged once to stderr) and
    physics keeps running headless -- the loop and the watchdog are unaffected.

    Config (env-overridable):
      * ``NAVILA_BRIDGE_ORCA_EDIT_ADDRESS``  (default ``127.0.0.1:50151``)
      * ``NAVILA_BRIDGE_ORCA_ROBOT_ACTOR``   (default ``quadruped_robot_1`` --
        the Go2 actor name in D_street.json; check your scene outline)
      * ``NAVILA_BRIDGE_ORCA_INNER``         (default ``mjlab``; ``mock`` for a
        GPU-free GUI demo -- also selectable as backend kind ``orcalab-mock``)
    """

    def __init__(
        self,
        *,
        inner: "StepBackend | None" = None,
        inner_kind: str = "mjlab",
        edit_address: str | None = None,
        robot_actor_name: str | None = None,
        **inner_kwargs: Any,
    ) -> None:
        self._inner = (
            inner if inner is not None else make_backend(inner_kind, **inner_kwargs)
        )
        self._edit_address = edit_address or os.environ.get(
            "NAVILA_BRIDGE_ORCA_EDIT_ADDRESS", "127.0.0.1:50151"
        )
        self._robot_actor_name = robot_actor_name or os.environ.get(
            "NAVILA_BRIDGE_ORCA_ROBOT_ACTOR", "quadruped_robot_1"
        )
        self._runtime_transform = None
        self._service = None
        self._robot_path = None
        self._loop = None
        self._loop_thread = None
        self._call = None
        self._mirror_disabled = False
        self._mirror_error: str | None = None
        self._mirror_failures = 0

    # -- StepBackend surface (delegate to inner) -------------------------------
    @property
    def control_dt(self) -> float:
        return self._inner.control_dt

    @property
    def interrupted(self) -> bool:
        return bool(getattr(self._inner, "interrupted", False))

    @interrupted.setter
    def interrupted(self, value: bool) -> None:
        if hasattr(self._inner, "interrupted"):
            self._inner.interrupted = bool(value)

    def start(self) -> None:
        self._inner.start()
        self._connect()

    def reset(self, episode: Any | None = None) -> RobotState:
        state = self._inner.reset(episode)
        self._mirror(state)
        return state

    def set_velocity_command(self, command: VelocityCommand) -> None:
        self._inner.set_velocity_command(command)

    def step(self) -> "RobotState | PhysicsStep":
        result = self._inner.step()
        self._mirror(getattr(result, "state", result))
        return result

    def emergency_stop(self) -> None:
        if hasattr(self._inner, "emergency_stop"):
            self._inner.emergency_stop()
        else:
            self._inner.set_velocity_command(
                VelocityCommand(0.0, 0.0, 0.0, 0.0, stop=True)
            )

    def close(self) -> None:
        try:
            self._inner.close()
        finally:
            self._disconnect()

    # -- OrcaLab mirror -----------------------------------------------------
    # grpc.aio binds a channel to the loop that created it, and the MCP server
    # runs sync tools on a rotating thread pool -- so the mirror owns one loop on
    # its own thread and EVERY edit-service call (construction, init_grpc, aloha,
    # transform push, destroy) runs on that loop via run_coroutine_threadsafe.
    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        import asyncio

        if asyncio.iscoroutine(value):
            return await value
        return value

    def _connect(self) -> None:
        if self._mirror_disabled or self._service is not None:
            return
        try:
            import asyncio
            import threading

            service_factory, transform_type, path_type = _load_orca_edit_runtime()

            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def _run_loop() -> None:
                asyncio.set_event_loop(loop)
                ready.set()
                loop.run_forever()

            thread = threading.Thread(
                target=_run_loop, name="orcalab-mirror-loop", daemon=True
            )
            thread.start()
            ready.wait(timeout=5.0)
            self._loop = loop
            self._loop_thread = thread
            self._call = lambda coro: asyncio.run_coroutine_threadsafe(
                coro, loop
            ).result(timeout=15.0)

            edit_address = self._edit_address
            actor_name = self._robot_actor_name

            async def _setup() -> Any:
                service = service_factory()
                await self._maybe_await(service.init_grpc(edit_address))
                ok = await self._maybe_await(service.aloha())
                if not ok:
                    # Close the channel we just opened before bubbling up.
                    if hasattr(service, "destroy_grpc"):
                        try:
                            await self._maybe_await(service.destroy_grpc())
                        except Exception:  # noqa: BLE001
                            pass
                    raise RuntimeError(
                        f"OrcaLab edit service not reachable at {edit_address}"
                    )
                return service

            self._service = self._call(_setup())
            self._runtime_transform = transform_type
            self._robot_path = path_type(f"/{actor_name}")
        except Exception as exc:  # noqa: BLE001 -- mirror is best-effort
            self._mirror_disabled = True
            self._mirror_error = f"{type(exc).__name__}: {exc}"
            self._stop_loop()
            self._service = None
            print(
                f"[orcalab-mirror] disabled -- {self._mirror_error}. "
                "Physics runs headless; the OrcaLab GUI will not update.",
                file=sys.stderr,
                flush=True,
            )

    def _mirror(self, state: Any) -> None:
        if self._mirror_disabled or self._service is None or state is None:
            return
        try:
            pos = np.asarray(state.root_pos_world, dtype=np.float64).reshape(3)
            quat = np.asarray(state.root_quat_wxyz, dtype=np.float64).reshape(4)
            transform = self._runtime_transform(
                position=pos.copy(), rotation=quat.copy(), scale=1.0
            )
            service = self._service
            path = self._robot_path

            async def _push() -> None:
                await self._maybe_await(
                    service.set_actor_transform_batch([path], [transform])
                )

            self._call(_push())
        except Exception as exc:  # noqa: BLE001 -- a render hiccup must not kill the loop
            self._mirror_failures += 1
            if self._mirror_failures == 1:
                print(
                    f"[orcalab-mirror] push failed ({type(exc).__name__}: {exc}); "
                    "continuing headless, retrying each step.",
                    file=sys.stderr,
                    flush=True,
                )

    def _stop_loop(self) -> None:
        loop, thread = self._loop, self._loop_thread
        self._loop = self._loop_thread = self._call = None
        if loop is None:
            return
        try:
            import asyncio

            async def _drain() -> None:
                pending = [
                    t
                    for t in asyncio.all_tasks(loop)
                    if t is not asyncio.current_task()
                ]
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

            try:
                asyncio.run_coroutine_threadsafe(_drain(), loop).result(timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
            loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join(timeout=2.0)
            loop.close()
        except Exception:  # noqa: BLE001
            pass

    def _disconnect(self) -> None:
        service = self._service
        try:
            if service is not None and self._call is not None and hasattr(
                service, "destroy_grpc"
            ):
                self._call(self._maybe_await(service.destroy_grpc()))
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._stop_loop()
            self._service = self._robot_path = self._runtime_transform = None


# ---------------------------------------------------------------------------
# Articulated OrcaLab renderer -- real MJLab gait + complete qpos mirroring
# ---------------------------------------------------------------------------

class _PosePushOnlyCamera:
    """Satisfy OrcaLabRenderBridge lifecycle without requiring an RGB stream.

    C2's gait half only calls ``push_state``. Marking this adapter as a
    pull-capture camera prevents ``push_state`` from waiting on an otherwise
    unused WebSocket frame; the separate camera-capture backend can inject its
    real camera factory through ``OrcaLabRenderBackend(camera_factory=...)``.
    """

    pull_capture = True

    def __init__(self, _name: str, _port: int) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def get_frame(self, *, format: str = "rgb24") -> tuple[np.ndarray, int]:
        del format
        raise RuntimeError(
            "orcalab-render is pose-only; configure a real camera factory "
            "before calling capture()"
        )


class OrcaLabRenderBackend:
    """Run the Go2 policy in MJLab and mirror its full articulated pose.

    Unlike :class:`OrcaLabMirrorBackend`, which writes only the actor's root
    transform through the edit service, this backend forwards MJLab's complete
    ``qpos_batch`` to :class:`navila_orca.render.orca.OrcaLabRenderBridge`
    after reset and after every policy step.  The renderer can therefore move
    all twelve Go2 joints and show the policy's real gait in OrcaLab.

    The construction order intentionally matches ``cli.py::_run`` and
    ``cli.py::_make_renderer``: start MJLab first so its joint-qpos addresses
    exist, then assemble the OrcaLab renderer from those addresses.  ``inner``
    and ``renderer``/``renderer_factory`` are injection seams for unit tests;
    production always defaults to ``MjlabGo2Backend`` with the bundled
    ``go2_flat.pt`` checkpoint.
    """

    def __init__(
        self,
        *,
        inner: "StepBackend | None" = None,
        renderer: Any | None = None,
        renderer_factory: Any | None = None,
        camera_factory: Any | None = None,
        checkpoint: str | Path | None = None,
        device: str | None = None,
        num_envs: int = 1,
        deterministic_play: bool = True,
        warmup_steps: int = 100,
        orcagym_address: str | None = None,
        camera_port: int | None = None,
        camera_name: str | None = None,
        render_timeout_s: float | None = None,
        robot_actor_name: str | None = None,
        robot_asset_path: str | None = None,
        terrain_asset_path: str | None = None,
        publish: bool = False,
        anchor_to_scene: bool = True,
        scene_timestep: float | None = None,
        scene_profile: str | None = None,
    ) -> None:
        if int(num_envs) != 1:
            raise ValueError("orcalab-render currently requires num_envs=1")
        if inner is None:
            from navila_orca.backends.mjlab_go2 import (
                DEFAULT_CHECKPOINT,
                MjlabGo2Backend,
            )

            inner = MjlabGo2Backend(
                checkpoint=DEFAULT_CHECKPOINT if checkpoint is None else checkpoint,
                device=device or os.environ.get("NAVILA_ORCA_DEVICE", "cpu"),
                num_envs=1,
                deterministic_play=deterministic_play,
                warmup_steps=warmup_steps,
            )
        self._inner = inner
        self._renderer = renderer
        self._renderer_factory = renderer_factory
        self._camera_factory = camera_factory or _PosePushOnlyCamera
        self._orcagym_address = orcagym_address or os.environ.get(
            "NAVILA_BRIDGE_ORCAGYM_ADDRESS", "127.0.0.1:50051"
        )
        self._camera_port = int(
            camera_port
            if camera_port is not None
            else os.environ.get("NAVILA_BRIDGE_ORCA_CAMERA_PORT", "7070")
        )
        self._camera_name = camera_name or os.environ.get(
            "NAVILA_BRIDGE_ORCA_CAMERA_NAME", "navila_ego"
        )
        self._render_timeout_s = float(
            render_timeout_s
            if render_timeout_s is not None
            else os.environ.get("NAVILA_BRIDGE_ORCA_RENDER_TIMEOUT", "10.0")
        )
        self._robot_actor_name = robot_actor_name or os.environ.get(
            "NAVILA_BRIDGE_ORCA_ROBOT_ACTOR", "auto"
        )
        self._robot_asset_path = robot_asset_path
        self._terrain_asset_path = terrain_asset_path
        self._publish = bool(publish)
        self._anchor_to_scene = bool(anchor_to_scene)
        self._scene_timestep = scene_timestep
        self._scene_profile = scene_profile or os.environ.get(
            "NAVILA_BRIDGE_ORCA_SCENE_PROFILE", "orca-train"
        )

    @property
    def control_dt(self) -> float:
        return self._inner.control_dt

    @property
    def interrupted(self) -> bool:
        return bool(getattr(self._inner, "interrupted", False))

    @interrupted.setter
    def interrupted(self, value: bool) -> None:
        if hasattr(self._inner, "interrupted"):
            self._inner.interrupted = bool(value)

    @property
    def qpos_batch(self) -> np.ndarray:
        return self._inner.qpos_batch

    @property
    def joint_qpos_addr(self) -> dict[str, int]:
        return self._inner.joint_qpos_addr

    @property
    def alignment_report(self) -> dict[str, Any] | None:
        return getattr(self._inner, "alignment_report", None)

    def start(self) -> None:
        self._inner.start()
        self._ensure_renderer()

    def reset(self, episode: Any | None = None) -> RobotState:
        self._inner.start()
        self._ensure_renderer()
        state = self._inner.reset(episode)
        self._push_state(state)
        return state

    def set_velocity_command(self, command: VelocityCommand) -> None:
        self._inner.set_velocity_command(command)

    def step(self) -> "RobotState | PhysicsStep":
        result = self._inner.step()
        state = getattr(result, "state", result)
        self._push_state(state)
        return result

    def emergency_stop(self) -> None:
        if hasattr(self._inner, "emergency_stop"):
            self._inner.emergency_stop()
        else:
            self._inner.set_velocity_command(
                VelocityCommand(0.0, 0.0, 0.0, 0.0, stop=True)
            )

    def close(self) -> None:
        try:
            if self._renderer is not None:
                self._renderer.close()
        finally:
            self._renderer = None
            self._inner.close()

    def _ensure_renderer(self) -> None:
        if self._renderer is not None:
            return
        factory = self._renderer_factory
        if factory is None:
            from navila_orca.render.orca import (
                DEFAULT_GO2_ASSET,
                OrcaLabRenderBridge,
            )

            factory = OrcaLabRenderBridge
            asset_path = self._robot_asset_path or DEFAULT_GO2_ASSET
        else:
            # Keep the production default visible to injected factories too.
            from navila_orca.render.orca import DEFAULT_GO2_ASSET

            asset_path = self._robot_asset_path or DEFAULT_GO2_ASSET

        discover_agents = self._robot_actor_name == "auto" and not self._publish
        agent_name = None if discover_agents else self._robot_actor_name
        if agent_name == "auto":
            agent_name = "go2_000"
        self._renderer = factory(
            orcagym_address=self._orcagym_address,
            camera_port=self._camera_port,
            camera_name=self._camera_name,
            timeout_s=self._render_timeout_s,
            num_envs=1,
            joint_qpos_addr=self._inner.joint_qpos_addr,
            agent_name=agent_name,
            discover_agents=discover_agents,
            asset_path=asset_path,
            terrain_asset_path=self._terrain_asset_path,
            publish=self._publish,
            anchor_to_scene=self._anchor_to_scene,
            scene_timestep=self._scene_timestep,
            scene_profile=self._scene_profile,
            camera_factory=self._camera_factory,
        )

    def _push_state(self, state: RobotState) -> None:
        self._ensure_renderer()
        self._renderer.push_state(state, self._inner.qpos_batch)


# ---------------------------------------------------------------------------
# Mock VLM
# ---------------------------------------------------------------------------

class MockVLM:
    """Returns NaVILA action phrases with no network and no model.

    Two modes:
      * ``script`` given -> replay it phrase by phrase; ``"stop"`` once exhausted.
      * no script -> greedy goal-seeking from ``state`` + ``goal_xy``: face the
        goal (turn 15/30/45 deg), then close distance (move 25/50/75 cm), then
        ``"stop"`` inside ``stop_radius``.
    """

    def __init__(
        self,
        *,
        script: Sequence[str] | None = None,
        stop_radius: float = 0.4,
        heading_tolerance_deg: float = 12.0,
    ) -> None:
        self._script = list(script) if script else None
        self._i = 0
        self.stop_radius = float(stop_radius)
        self.heading_tolerance_deg = float(heading_tolerance_deg)

    def next_action(
        self,
        *,
        instruction: str,
        state: RobotState,
        frames: Sequence[Any],
        goal_xy: tuple[float, float] | None = None,
    ) -> str:
        del instruction, frames
        if self._script is not None:
            if self._i >= len(self._script):
                return "stop"
            phrase = self._script[self._i]
            self._i += 1
            return phrase
        if goal_xy is None:
            return "stop"

        px, py, _ = state.root_pos_world
        gx, gy = goal_xy
        dx, dy = gx - px, gy - py
        dist = math.hypot(dx, dy)
        if dist <= self.stop_radius:
            return "stop"

        yaw = float(state.base_rpy[2])
        desired = math.atan2(dy, dx)
        err_deg = math.degrees((desired - yaw + math.pi) % (2 * math.pi) - math.pi)
        if abs(err_deg) > self.heading_tolerance_deg:
            direction = "left" if err_deg > 0 else "right"
            mag = 45 if abs(err_deg) >= 45 else (30 if abs(err_deg) >= 20 else 15)
            return f"turn {direction} by {mag} degrees"
        cm = 75 if dist > 1.0 else (50 if dist > 0.6 else 25)
        return f"move forward by {cm} cm"


# ---------------------------------------------------------------------------
# Real TCP VLM adapter (thin -- reuses the tested client + sample_history)
# ---------------------------------------------------------------------------

class TcpVLM:
    """Adapts ``LengthPrefixedJsonVLMClient`` to the ``StepVLM`` seam.

    Requires real ego frames in ``frames`` (OrcaLab camera capture -- Phase 2).
    Raises if handed the mock's placeholder frames so a black-frame request is
    never silently sent to NaVILA.
    """

    def __init__(
        self, *, host: str = "127.0.0.1", port: int = 54321, timeout_s: float = 120.0
    ) -> None:
        from navila_orca.vlm_client import LengthPrefixedJsonVLMClient

        self._client = LengthPrefixedJsonVLMClient(
            host=host, port=port, timeout_s=timeout_s
        )

    def next_action(
        self,
        *,
        instruction: str,
        state: RobotState,
        frames: Sequence[Any],
        goal_xy: tuple[float, float] | None = None,
    ) -> str:
        del state, goal_xy
        from navila_orca.frames import sample_history

        real = [f for f in frames if not _is_placeholder_frame(f)]
        if not real:
            raise RuntimeError(
                "TcpVLM needs real ego camera frames; none wired yet "
                "(OrcaLab capture is Phase 2). Use NAVILA_BRIDGE_VLM=mock for now."
            )
        return self._client.infer(sample_history(real), instruction)


def _is_placeholder_frame(frame: Any) -> bool:
    arr = getattr(frame, "shape", None)
    return bool(arr is not None and tuple(frame.shape[:2]) == (8, 8))


def placeholder_frame() -> np.ndarray:
    """Cheap stand-in frame for the mock loop (8x8 black RGB)."""
    return np.zeros((8, 8, 3), dtype=np.uint8)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def make_backend(kind: str | None = None, **kwargs: Any) -> StepBackend:
    kind = (kind or os.environ.get("NAVILA_BRIDGE_BACKEND", "mock")).lower()
    if kind == "mock":
        return MockBackend(**kwargs)
    if kind == "mjlab":
        from navila_orca.backends.mjlab_go2 import MjlabGo2Backend

        params = {"num_envs": 1, "device": os.environ.get("NAVILA_ORCA_DEVICE", "cpu")}
        params.update(kwargs)
        return MjlabGo2Backend(**params)
    if kind == "orcalab-render":
        params = {
            "num_envs": 1,
            "device": os.environ.get("NAVILA_ORCA_DEVICE", "cpu"),
        }
        params.update(kwargs)
        return OrcaLabRenderBackend(**params)
    if kind in ("orcalab", "orcalab-mock"):
        # 'orcalab'      -> real physics (mjlab inner) mirrored into the GUI
        # 'orcalab-mock' -> planar physics (mock inner) mirrored into the GUI, no GPU
        inner_kind = (
            "mock"
            if kind == "orcalab-mock"
            else os.environ.get("NAVILA_BRIDGE_ORCA_INNER", "mjlab")
        )
        return OrcaLabMirrorBackend(inner_kind=inner_kind, **kwargs)
    raise ValueError(
        f"unknown backend kind {kind!r} "
        "(expected 'mock', 'mjlab', 'orcalab', 'orcalab-mock', "
        "or 'orcalab-render')"
    )


def make_vlm(kind: str | None = None, **kwargs: Any) -> StepVLM:
    kind = (kind or os.environ.get("NAVILA_BRIDGE_VLM", "mock")).lower()
    if kind == "mock":
        script = kwargs.pop("script", None)
        if script is None:
            raw = os.environ.get("NAVILA_BRIDGE_VLM_SCRIPT", "").strip()
            if raw:
                script = [p.strip() for p in raw.split(";") if p.strip()]
        return MockVLM(script=script, **kwargs)
    if kind == "tcp":
        return TcpVLM(**kwargs)
    raise ValueError(f"unknown vlm kind {kind!r} (expected 'mock' or 'tcp')")
