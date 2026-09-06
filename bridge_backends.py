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

import json
import math
import os
import sys
import tempfile
import time
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


def _env_flag(name: str, default: bool) -> bool:
    """Parse an on/off env var, e.g. NAVILA_BRIDGE_ORCA_CAMERA. Unset -> default."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


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
    articulated -- the dog glides rather than walks. Real gait needs
    ``OrcaLabRenderBridge`` (full qpos push, split to D as C2's GPU half, see
    docs/PLAN.md). Real ego-camera frames (C2's GPU-free half, C's job) ARE
    available here via ``capture_frame()`` -- see below.

    OrcaLab is a passive viewer here: if the edit service is unreachable or the
    actor path is wrong, mirroring disables itself (logged once to stderr) and
    physics keeps running headless -- the loop and the watchdog are unaffected.

    Config (env-overridable):
      * ``NAVILA_BRIDGE_ORCA_EDIT_ADDRESS``  (default ``127.0.0.1:50151``)
      * ``NAVILA_BRIDGE_ORCA_ROBOT_ACTOR``   (default ``quadruped_robot_1`` --
        the Go2 actor name in D_street.json; check your scene outline)
      * ``NAVILA_BRIDGE_ORCA_INNER``         (default ``mjlab``; ``mock`` for a
        GPU-free GUI demo -- also selectable as backend kind ``orcalab-mock``)
      * ``NAVILA_BRIDGE_ORCA_CAMERA``        (default off) -- when on,
        ``capture_frame()`` pulls a real RGB frame from a persistent OrcaLab
        MuJoCo camera actor via the same edit-service connection used for pose
        pushes (``EditServiceWrapper.get_camera_png``, confirmed working in
        CLAUDE.md's "Known technical facts"). No GPU/MJLab needed -- this is
        C2's camera-capture-only fallback (see docs/PLAN.md), independent of
        D's real-gait half. The camera actor itself (default name
        ``mujococamera1080``) must already exist in the loaded scene; this
        class does not spawn one.
      * ``NAVILA_BRIDGE_ORCA_CAMERA_NAME``   (default ``mujococamera1080``)
    """

    def __init__(
        self,
        *,
        inner: "StepBackend | None" = None,
        inner_kind: str = "mjlab",
        edit_address: str | None = None,
        robot_actor_name: str | None = None,
        camera: bool | None = None,
        camera_name: str | None = None,
        camera_timeout_s: float = 10.0,
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
        self._camera_enabled = (
            _env_flag("NAVILA_BRIDGE_ORCA_CAMERA", False)
            if camera is None
            else bool(camera)
        )
        self._camera_name = camera_name or os.environ.get(
            "NAVILA_BRIDGE_ORCA_CAMERA_NAME", "mujococamera1080"
        )
        self._camera_timeout_s = float(camera_timeout_s)
        self._camera_output_dir: str | None = None
        self._camera_request_index = 0
        self._camera_failures = 0
        # Camera-follow: when the ego camera is enabled, also push its world
        # transform to (robot pose (+) mount offset) every step so capture_frame
        # returns an actual head-mounted view, not a static shot from wherever
        # the actor was spawned. Best-effort and pushed in its OWN edit-service
        # call (not batched with the robot) so a missing camera actor can never
        # stall the body mirror. Default on whenever the camera is on; disable
        # with NAVILA_BRIDGE_ORCA_CAMERA_FOLLOW=0.
        self._camera_follow = self._camera_enabled and _env_flag(
            "NAVILA_BRIDGE_ORCA_CAMERA_FOLLOW", True
        )
        self._camera_stabilize = _env_flag(
            "NAVILA_BRIDGE_ORCA_CAMERA_STABILIZE", True
        )
        self._camera_path = None
        self._compose_camera_pose = None
        self._camera_follow_failures = 0
        self._last_state = None

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
            if self._camera_follow:
                try:
                    from navila_orca.render.orca_camera import compose_camera_pose

                    self._compose_camera_pose = compose_camera_pose
                    self._camera_path = path_type(f"/{self._camera_name}")
                except Exception as exc:  # noqa: BLE001 -- follow is optional
                    self._camera_follow = False
                    print(
                        f"[orcalab-mirror] camera-follow disabled ({exc!r}); "
                        "capture will use the camera's spawned pose.",
                        file=sys.stderr,
                        flush=True,
                    )
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
        # Stash for camera-follow, which is applied lazily in capture_frame()
        # rather than here -- the camera only has to be in position when a frame
        # is actually pulled (~2x per navigate_step), not on every physics tick
        # (50-150x), and each edit-service RPC costs real latency.
        self._last_state = state
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

    def _follow_camera_to_robot(self) -> None:
        """Push the ego camera actor to (robot pose (+) mount offset) so the next
        capture is a head-mounted view. Best-effort, its own edit-service call --
        a missing/renamed camera actor must never stall the loop. Called from
        capture_frame(), not _mirror()."""
        state = self._last_state
        if not (
            self._camera_follow
            and self._camera_path is not None
            and self._service is not None
            and state is not None
        ):
            return
        try:
            pos = np.asarray(state.root_pos_world, dtype=np.float64).reshape(3)
            quat = np.asarray(state.root_quat_wxyz, dtype=np.float64).reshape(4)
            cam_pos, cam_quat = self._compose_camera_pose(
                pos, quat, stabilize_horizon=self._camera_stabilize
            )
            cam_transform = self._runtime_transform(
                position=np.asarray(cam_pos, dtype=np.float64).copy(),
                rotation=np.asarray(cam_quat, dtype=np.float64).copy(),
                scale=1.0,
            )
            service = self._service
            cam_path = self._camera_path

            async def _push_cam() -> None:
                await self._maybe_await(
                    service.set_actor_transform_batch([cam_path], [cam_transform])
                )

            self._call(_push_cam())
        except Exception as exc:  # noqa: BLE001 -- follow is best-effort
            self._camera_follow_failures += 1
            if self._camera_follow_failures == 1:
                print(
                    f"[orcalab-mirror] camera-follow push failed "
                    f"({type(exc).__name__}: {exc}); the ego view will not track "
                    f"the dog. Is the {self._camera_name!r} actor in the scene "
                    "(navila_spawn_camera)?",
                    file=sys.stderr,
                    flush=True,
                )

    def capture_frame(self) -> "np.ndarray | None":
        """Pull one real RGB frame from OrcaLab's persistent MuJoCo camera actor
        (C2's GPU-free camera fallback, docs/PLAN.md). Reuses the same
        edit-service connection + event loop as the pose mirror -- no second
        gRPC channel.

        Returns None if the camera is disabled, the mirror never connected, or
        the capture fails for any reason (unreachable actor, stale/unreadable
        PNG, ...) -- the caller (``navila_bridge._capture_frame``) falls back
        to the mock placeholder frame in that case, same "never block the
        loop" contract as the pose mirror above.
        """
        if not self._camera_enabled or self._mirror_disabled or self._service is None:
            return None
        self._follow_camera_to_robot()  # move the ego camera onto the dog first
        try:
            from PIL import Image

            if self._camera_output_dir is None:
                self._camera_output_dir = tempfile.mkdtemp(prefix="navila_bridge_camera_")

            service = self._service
            camera_name = self._camera_name
            output_dir = self._camera_output_dir
            filename = f"{camera_name}_{self._camera_request_index}.png"
            self._camera_request_index += 1

            async def _capture() -> bool:
                return bool(
                    await self._maybe_await(
                        service.get_camera_png(camera_name, output_dir, filename)
                    )
                )

            if not self._call(_capture()):
                raise RuntimeError(f"OrcaLab camera {camera_name!r} refused PNG capture")

            path = os.path.join(output_dir, filename)
            deadline = time.monotonic() + self._camera_timeout_s
            last_error: Exception | None = None
            while True:
                try:
                    with Image.open(path) as image:
                        return np.asarray(image.convert("RGB"), dtype=np.uint8).copy()
                except (OSError, SyntaxError, ValueError) as exc:
                    last_error = exc
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            f"camera PNG not readable within {self._camera_timeout_s:.1f}s: "
                            f"{path}: {last_error}"
                        ) from exc
                    time.sleep(0.01)
        except Exception as exc:  # noqa: BLE001 -- capture is best-effort
            self._camera_failures += 1
            if self._camera_failures == 1:
                print(
                    f"[orcalab-camera] capture failed ({type(exc).__name__}: {exc}); "
                    "falling back to placeholder frames, retrying each call.",
                    file=sys.stderr,
                    flush=True,
                )
            return None

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
# In-scene hazard trigger -- the "visibly real, not composited" live-demo
# trigger (docs/PLAN.md "C" item 5), distinct from ScenarioInjector's
# frame-overlay test harness.
# ---------------------------------------------------------------------------

def trigger_scene_hazard(
    actor_name: str,
    position: Sequence[float],
    rotation_wxyz: Sequence[float] = (1.0, 0.0, 0.0, 0.0),
    *,
    edit_address: str | None = None,
) -> None:
    """Teleport an existing OrcaLab scene actor to a world pose -- move a
    hazard (e.g. ``blue_hatchback_car_1``) into the Go2's path for the live
    demo. ``actor_name`` must already exist in the loaded scene (D's
    street.json hazard cast: ``blue_hatchback_car_1``, ``traffic_light_1..4``,
    ``female_pedestrian_model_1..4``, ``supine_human_model_1``, ...) -- this
    moves an actor via the same verified ``set_actor_transform_batch`` path as
    the pose mirror, it does not spawn a new one.

    Deliberately independent of ``StepBackend``/``OrcaLabMirrorBackend``: this
    fires once per demo beat (a judge-facing trigger), not once per physics
    tick, and must work no matter which ``backend_kind`` the per-step loop is
    using (mock, mjlab, orcalab, orcalab-mock), headless included. Opens and
    closes its own edit-service connection per call rather than holding one
    open -- this fires rarely (once or twice a demo run), so a fresh
    connection is simpler than managing a long-lived background thread's
    lifecycle for something this infrequent.

    Unlike the pose mirror's "never block the loop, degrade silently"
    contract, failures here are RAISED, not swallowed: a demo trigger the
    operator can't see fire needs to surface an error (OrcaLab not running,
    wrong actor name, ...), not silently do nothing.
    """
    import asyncio
    import threading

    address = edit_address or os.environ.get(
        "NAVILA_BRIDGE_ORCA_EDIT_ADDRESS", "127.0.0.1:50151"
    )
    service_factory, transform_type, path_type = _load_orca_edit_runtime()

    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def _run_loop() -> None:
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    thread = threading.Thread(
        target=_run_loop, name="orcalab-hazard-trigger", daemon=True
    )
    thread.start()
    ready.wait(timeout=5.0)

    async def _maybe_await(value: Any) -> Any:
        if asyncio.iscoroutine(value):
            return await value
        return value

    async def _run() -> None:
        service = service_factory()
        try:
            await _maybe_await(service.init_grpc(address))
            if not await _maybe_await(service.aloha()):
                raise RuntimeError(f"OrcaLab edit service not reachable at {address}")
            pos = np.asarray(position, dtype=np.float64).reshape(3)
            rot = np.asarray(rotation_wxyz, dtype=np.float64).reshape(4)
            transform = transform_type(position=pos.copy(), rotation=rot.copy(), scale=1.0)
            path = path_type(f"/{actor_name}")
            await _maybe_await(service.set_actor_transform_batch([path], [transform]))
        finally:
            if hasattr(service, "destroy_grpc"):
                try:
                    await _maybe_await(service.destroy_grpc())
                except Exception:  # noqa: BLE001 -- best-effort cleanup only
                    pass

    try:
        asyncio.run_coroutine_threadsafe(_run(), loop).result(timeout=15.0)
    finally:
        try:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=2.0)
            loop.close()
        except Exception:  # noqa: BLE001 -- loop teardown is best-effort
            pass


# ---------------------------------------------------------------------------
# Spawn the persistent MuJoCo ego-camera actor -- so camera=true / the live
# monitor have something to capture from. 'prefabs/mujococamera1080' is a
# built-in OrcaLab prefab (nothing to download); this instantiates it as a
# root actor via the SAME edit-service the pose mirror uses. Mirrors the
# add_actor_batch call verified in navila_orca.render.orca_camera's
# OrcaMujocoCameraFollower._start_async.
# ---------------------------------------------------------------------------

def _load_orca_spawn_runtime():
    """Import the OrcaLab symbols needed to add a prefab actor. Separate from
    _load_orca_edit_runtime (which only needs Transform/Path) so a missing
    orcalab.actor / orcalab.scene_edit_types surfaces its own clear error."""
    import importlib

    try:
        transform_mod = importlib.import_module("orcalab.transform")
    except ModuleNotFoundError:
        transform_mod = importlib.import_module("orcalab.math")
    path_mod = importlib.import_module("orcalab.path")
    actor_mod = importlib.import_module("orcalab.actor")
    edit_types_mod = importlib.import_module("orcalab.scene_edit_types")
    wrapper_mod = importlib.import_module("orcalab.protos.edit_service_wrapper")
    return (
        wrapper_mod.EditServiceWrapper,
        transform_mod.Transform,
        path_mod.Path,
        actor_mod.AssetActor,
        edit_types_mod.AddActorRequest,
    )


def spawn_camera_actor(
    actor_name: str = "mujococamera1080",
    asset_path: str = "prefabs/mujococamera1080",
    position: Sequence[float] = (0.1, 0.0, 0.5),
    rotation_wxyz: Sequence[float] = (1.0, 0.0, 0.0, 0.0),
    *,
    replace: bool = False,
    edit_address: str | None = None,
) -> dict:
    """Add the persistent MuJoCo ego camera to the loaded OrcaLab scene.

    'prefabs/mujococamera1080' ships with OrcaLab -- there is nothing to
    download; this instantiates that prefab as a root actor named
    ``actor_name``. Idempotent: if the actor already exists it is left alone
    (``replace=True`` deletes and re-adds it). The add is live-only and does
    NOT persist to the scene file -- redo it after each fresh scene load, or
    bake the actor into the scene.

    Same one-shot connect/call/disconnect + own-event-loop-thread pattern as
    trigger_scene_hazard, and like it, failures are RAISED (OrcaLab not
    running, prefab path wrong, ...), not swallowed. Returns a small dict:
    {actor_name, asset_path, created: bool, note}.
    """
    import asyncio
    import threading

    address = edit_address or os.environ.get(
        "NAVILA_BRIDGE_ORCA_EDIT_ADDRESS", "127.0.0.1:50151"
    )
    (
        service_factory,
        transform_type,
        path_type,
        asset_actor_type,
        add_actor_request_type,
    ) = _load_orca_spawn_runtime()

    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def _run_loop() -> None:
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    thread = threading.Thread(
        target=_run_loop, name="orcalab-camera-spawn", daemon=True
    )
    thread.start()
    ready.wait(timeout=5.0)

    async def _maybe_await(value: Any) -> Any:
        if asyncio.iscoroutine(value):
            return await value
        return value

    def _root_path():
        rp = getattr(path_type, "root_path", None)
        return rp() if callable(rp) else path_type("/")

    async def _run() -> dict:
        service = service_factory()
        try:
            await _maybe_await(service.init_grpc(address))
            if not await _maybe_await(service.aloha()):
                raise RuntimeError(f"OrcaLab edit service not reachable at {address}")
            actor_path = path_type(f"/{actor_name}")

            exists = False
            try:
                await _maybe_await(
                    service.get_actor_property_groups_batch([actor_path])
                )
                exists = True
            except Exception:  # noqa: BLE001 -- absent actor -> the add below
                exists = False

            if exists and not replace:
                return {
                    "actor_name": actor_name,
                    "asset_path": asset_path,
                    "created": False,
                    "note": "actor already present; pass replace=true to recreate it",
                }
            if exists and replace:
                try:
                    await _maybe_await(service.delete_actor_batch([actor_path]))
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError(f"could not delete existing {actor_name!r}: {exc}")

            pos = np.asarray(position, dtype=np.float64).reshape(3)
            rot = np.asarray(rotation_wxyz, dtype=np.float64).reshape(4)
            actor = asset_actor_type(actor_name, asset_path)
            actor.transform = transform_type(
                position=pos.copy(), rotation=rot.copy(), scale=1.0
            )
            request = add_actor_request_type(actor, _root_path())
            try:
                await _maybe_await(service.add_actor_batch([request]))
            except TypeError:
                added, errors = await _maybe_await(
                    service.add_actor_batch([request], True)
                )
                if not added:
                    raise RuntimeError(
                        "OrcaLab refused add_actor_batch: " + "; ".join(errors or [])
                    )
            return {
                "actor_name": actor_name,
                "asset_path": asset_path,
                "created": True,
                "note": "added to the live scene (not saved to the scene file)",
            }
        finally:
            if hasattr(service, "destroy_grpc"):
                try:
                    await _maybe_await(service.destroy_grpc())
                except Exception:  # noqa: BLE001 -- best-effort cleanup
                    pass

    try:
        return asyncio.run_coroutine_threadsafe(_run(), loop).result(timeout=20.0)
    finally:
        try:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=2.0)
            loop.close()
        except Exception:  # noqa: BLE001 -- loop teardown is best-effort
            pass


# ---------------------------------------------------------------------------
# Scene reset reliability -- repeatable "reset to authored layout" for
# rehearsal + judges (docs/PLAN.md "C" item 6).
#
# Deliberately built on the same verified set_actor_transform_batch write-back
# used by trigger_scene_hazard/the pose mirror, NOT EditServiceWrapper's
# save_state()/restore_state() -- those take zero arguments (confirmed via
# inspect.signature against the live orcalab package, no docstring), which
# means a single global, unnamed checkpoint slot with unknown scope (does it
# snapshot the whole editor session? interact with Play mode? touch anything
# on disk?). Calling an unverified, undocumented, no-argument "restore"
# against a scene someone is actively rehearsing in is too risky to guess at;
# transform write-back is the mechanism this codebase has already verified
# and shipped (see CLAUDE.md's "Known technical facts" and the Go2
# pose-reset-between-runs fix).
# ---------------------------------------------------------------------------

# The repo-root street.json is D's current live demo scene (183 actors,
# portable_road_ramp_1/2) -- confirmed 2026-09-05 to be what's actually loaded
# in the running OrcaLab GUI, as opposed to the older, smaller, git-tracked
# NaVILA-Orca/hackathon_assets/street.json (70 actors). Untracked on purpose,
# same as private_asset_transfer/ -- see CLAUDE.md's "Do not commit" note on
# proprietary OrcaStudio scene content.
DEFAULT_SCENE_LAYOUT_PATH = Path(__file__).resolve().parent / "street.json"


def load_scene_layout(path: "str | Path | None" = None) -> "dict[str, dict[str, Any]]":
    """Parse a street.json-shaped scene file into
    ``{actor_name: {"position": (x,y,z), "rotation_wxyz": (w,x,y,z), "scale": s}}``
    for every actor with a name + transform -- the ground-truth "authored
    layout" scene reset restores actors back to. Reads fresh from disk on
    every call (no caching) so it always reflects whichever street.json is
    currently checked out, not a stale snapshot from process start.

    ``path`` defaults to ``DEFAULT_SCENE_LAYOUT_PATH`` (the repo-root
    ``street.json``, D's current live demo scene) -- override via the
    ``path`` arg or ``NAVILA_BRIDGE_SCENE_LAYOUT`` env var (checked when
    ``path`` is None) for a different scene file, e.g.
    ``NaVILA-Orca/hackathon_assets/street.json``.
    """
    if path is None:
        path = os.environ.get("NAVILA_BRIDGE_SCENE_LAYOUT")
    p = Path(path) if path is not None else DEFAULT_SCENE_LAYOUT_PATH
    with open(p, encoding="utf-8") as f:
        data = json.load(f)

    layout: "dict[str, dict[str, Any]]" = {}
    for actor in data.get("actors", []):
        name = actor.get("name")
        transform = actor.get("transform")
        if not name or not transform:
            continue
        pos = transform.get("position")
        rot = transform.get("rotation")
        if pos is None or rot is None:
            continue
        layout[name] = {
            "position": tuple(float(v) for v in pos),
            "rotation_wxyz": tuple(float(v) for v in rot),
            "scale": float(transform.get("scale", 1.0)),
        }
    return layout


def authored_robot_start_pose(
    actor_name: str | None = None, layout_path: "str | Path | None" = None
) -> "tuple[float, float, float] | None":
    """(x, y, yaw_rad) for the robot actor's AUTHORED spawn transform in the
    scene layout, so the per-step loop's planar mock physics starts facing
    the way the scene's own hazard cast (traffic lights, zebra crossing,
    cars) is actually laid out, instead of always resetting to world origin
    facing +X regardless of the street's real heading -- confirmed as the
    root cause of the dog walking perpendicular to the street/crossing
    instead of along it (street.json's quadruped_robot_1 is authored at
    yaw ~86 deg, not 0). Returns None (never raises) if the layout file or
    the actor entry isn't available; callers then fall back to (0, 0), yaw 0
    -- today's pre-fix behavior, unchanged for scenes without this actor.
    """
    try:
        layout = load_scene_layout(layout_path)
        name = actor_name or os.environ.get(
            "NAVILA_BRIDGE_ORCA_ROBOT_ACTOR", "quadruped_robot_1"
        )
        entry = layout.get(name)
        if entry is None:
            return None
        x, y, _z = entry["position"]
        w, _qx, _qy, qz = entry["rotation_wxyz"]
        return float(x), float(y), 2.0 * math.atan2(qz, w)
    except Exception:  # noqa: BLE001 -- anchoring is best-effort, never blocks start
        return None


def reset_scene_layout(
    layout: "dict[str, dict[str, Any]] | None" = None,
    *,
    actor_names: "Sequence[str] | None" = None,
    scene_path: "str | Path | None" = None,
    edit_address: str | None = None,
    exclude_actors: "Sequence[str] | None" = None,
) -> "list[str]":
    """Batch-restore scene actors to their authored transform -- the "reset
    to authored layout" rehearsal/judges reset. Opens its own one-shot
    edit-service connection (same pattern as ``trigger_scene_hazard``: this
    fires between demo runs, not every physics tick) and writes every
    restored actor's transform in a SINGLE ``set_actor_transform_batch`` call.

    ``layout`` defaults to ``load_scene_layout(scene_path)`` -- pass a
    pre-loaded layout to skip re-reading the file, or to reset to a layout
    captured at some other point instead of whatever is on disk now.

    ``actor_names`` restores only those actors (raises ``KeyError`` if any
    name isn't in the layout) -- e.g. after ``trigger_scene_hazard`` moved
    just ``blue_hatchback_car_1``, reset just that one. Omit it to restore
    EVERY actor in the layout except ``exclude_actors``.

    ``exclude_actors`` defaults to the per-step loop's robot actor
    (``NAVILA_BRIDGE_ORCA_ROBOT_ACTOR``, default ``quadruped_robot_1``) --
    only applied to a full-layout reset (ignored when ``actor_names`` is
    given explicitly, since an explicit request should never be silently
    filtered). The robot is excluded by default because its pose is owned by
    the per-step episode/backend, not this scene file: writing it here would
    desync from that backend's internal state, and the very next
    ``navila_navigate_step`` would overwrite this write anyway via the pose
    mirror. Use ``navila_reset_episode`` to reset the robot instead.

    Raises (never swallows) on a missing actor name, a connection failure, or
    a write failure -- a rehearsal reset that silently does nothing would be
    worse than a loud error. Returns the list of actor names actually
    restored.
    """
    if layout is None:
        layout = load_scene_layout(scene_path)

    if actor_names is not None:
        names = list(actor_names)
        missing = [n for n in names if n not in layout]
        if missing:
            raise KeyError(f"actor(s) not found in the authored layout: {missing}")
    else:
        skip = set(exclude_actors) if exclude_actors is not None else {
            os.environ.get("NAVILA_BRIDGE_ORCA_ROBOT_ACTOR", "quadruped_robot_1")
        }
        names = [n for n in layout if n not in skip]

    if not names:
        return []

    import asyncio
    import threading

    address = edit_address or os.environ.get(
        "NAVILA_BRIDGE_ORCA_EDIT_ADDRESS", "127.0.0.1:50151"
    )
    service_factory, transform_type, path_type = _load_orca_edit_runtime()

    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def _run_loop() -> None:
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    thread = threading.Thread(
        target=_run_loop, name="orcalab-scene-reset", daemon=True
    )
    thread.start()
    ready.wait(timeout=5.0)

    async def _maybe_await(value: Any) -> Any:
        if asyncio.iscoroutine(value):
            return await value
        return value

    async def _run() -> None:
        service = service_factory()
        try:
            await _maybe_await(service.init_grpc(address))
            if not await _maybe_await(service.aloha()):
                raise RuntimeError(f"OrcaLab edit service not reachable at {address}")
            paths = [path_type(f"/{n}") for n in names]
            transforms = []
            for n in names:
                entry = layout[n]
                pos = np.asarray(entry["position"], dtype=np.float64).reshape(3)
                rot = np.asarray(entry["rotation_wxyz"], dtype=np.float64).reshape(4)
                transforms.append(
                    transform_type(
                        position=pos.copy(),
                        rotation=rot.copy(),
                        scale=float(entry.get("scale", 1.0)),
                    )
                )
            await _maybe_await(service.set_actor_transform_batch(paths, transforms))
        finally:
            if hasattr(service, "destroy_grpc"):
                try:
                    await _maybe_await(service.destroy_grpc())
                except Exception:  # noqa: BLE001 -- best-effort cleanup only
                    pass

    try:
        asyncio.run_coroutine_threadsafe(_run(), loop).result(timeout=30.0)
    finally:
        try:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=2.0)
            loop.close()
        except Exception:  # noqa: BLE001 -- loop teardown is best-effort
            pass

    return names


# ---------------------------------------------------------------------------
# Articulated OrcaLab renderer -- real MJLab gait + complete qpos mirroring
# ---------------------------------------------------------------------------

def _neutralize_orca_gym_mainthread_signal() -> None:
    """Make ``orca_gym``'s file lock usable from a non-main thread.

    ``orca_gym.utils.dir_utils.file_lock`` bounds a blocking ``fcntl.flock``
    wait by installing a ``SIGALRM`` handler.  ``signal.signal`` /
    ``signal.alarm`` raise ``ValueError('signal only works in main thread of
    the main interpreter')`` off the main thread, and ``OrcaLabRenderBackend``
    now drives the renderer on a dedicated worker thread (see ``_run``).

    Swap that module's ``signal`` reference for a proxy that no-ops
    ``signal``/``alarm`` on non-main threads -- the flock wait then just blocks
    without a timeout, which is fine for the single-process demo where the
    per-file lock is uncontended and stale locks are already reaped by
    ``cleanup_zombie_locks``.  Real ``signal`` behaviour is untouched on the
    main thread.  Idempotent; a missing ``orca_gym`` is left for the renderer
    factory to report.
    """
    try:
        from orca_gym.utils import dir_utils  # type: ignore
    except Exception:  # noqa: BLE001 -- absence surfaced later, with context
        return

    import signal as _real_signal
    import threading

    if getattr(dir_utils.signal, "_navila_worker_safe", False):
        return

    class _WorkerSafeSignal:
        _navila_worker_safe = True

        def __getattr__(self, name: str) -> Any:
            return getattr(_real_signal, name)

        def signal(self, sig: Any, handler: Any) -> Any:
            if threading.current_thread() is threading.main_thread():
                return _real_signal.signal(sig, handler)
            return _real_signal.getsignal(sig)

        def alarm(self, seconds: Any) -> int:
            if threading.current_thread() is threading.main_thread():
                return _real_signal.alarm(seconds)
            return 0

    dir_utils.signal = _WorkerSafeSignal()


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
        # Every lifecycle call runs on this one dedicated thread.
        # ``OrcaLabBatchRenderer`` (built inside ``_ensure_renderer``) creates
        # its own asyncio loop and drives it with ``loop.run_until_complete``
        # on whatever thread constructs/steps it.  The MCP bridge calls this
        # backend from a sync ``@mcp.tool()`` that FastMCP runs inline on the
        # server's *running* event-loop thread, where ``run_until_complete``
        # raises ``RuntimeError('Cannot run the event loop while another loop
        # is running')``.  A loop-free worker thread sidesteps that and also
        # keeps all MJLab/MJWarp state on a single thread.  Created lazily so
        # constructing the backend without starting it (unit tests, dry runs)
        # spawns no thread.
        self._executor: Any | None = None

    def _run(self, fn: Any, *args: Any) -> Any:
        """Execute ``fn`` on the dedicated worker thread and block for it."""
        import concurrent.futures

        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="orcalab-render"
            )
        return self._executor.submit(fn, *args).result()

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
        self._run(self._start_impl)

    def _start_impl(self) -> None:
        self._inner.start()
        self._ensure_renderer()

    def reset(self, episode: Any | None = None) -> RobotState:
        return self._run(self._reset_impl, episode)

    def _reset_impl(self, episode: Any | None = None) -> RobotState:
        self._inner.start()
        self._ensure_renderer()
        state = self._inner.reset(episode)
        self._push_state(state)
        return state

    def set_velocity_command(self, command: VelocityCommand) -> None:
        self._run(self._inner.set_velocity_command, command)

    def step(self) -> "RobotState | PhysicsStep":
        return self._run(self._step_impl)

    def _step_impl(self) -> "RobotState | PhysicsStep":
        result = self._inner.step()
        state = getattr(result, "state", result)
        self._push_state(state)
        return result

    def emergency_stop(self) -> None:
        self._run(self._emergency_stop_impl)

    def _emergency_stop_impl(self) -> None:
        if hasattr(self._inner, "emergency_stop"):
            self._inner.emergency_stop()
        else:
            self._inner.set_velocity_command(
                VelocityCommand(0.0, 0.0, 0.0, 0.0, stop=True)
            )

    def close(self) -> None:
        try:
            self._run(self._close_impl)
        finally:
            executor, self._executor = self._executor, None
            if executor is not None:
                executor.shutdown(wait=False)

    def _close_impl(self) -> None:
        try:
            if self._renderer is not None:
                self._renderer.close()
        finally:
            self._renderer = None
            self._inner.close()

    def _ensure_renderer(self) -> None:
        if self._renderer is not None:
            return
        # This runs on the worker thread (see ``_run``); orca_gym's file lock
        # would otherwise crash trying to install a SIGALRM handler here.
        _neutralize_orca_gym_mainthread_signal()
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
        # Anchor the mock inner's spawn to the scene's authored transform for
        # the robot actor -- MockBackend otherwise always starts at world
        # origin facing yaw=0, which is NOT the street's actual heading (see
        # authored_robot_start_pose's docstring). MjlabGo2Backend (the
        # non-mock inner) has no start-pose kwarg to anchor -- its own spawn
        # anchoring is a separate, not-yet-built concern (docs/PLAN.md's C2
        # real-gait item), so this only fires when inner_kind == "mock".
        if (
            inner_kind == "mock"
            and _env_flag("NAVILA_BRIDGE_ORCA_ANCHOR", True)
            and "start_xy" not in kwargs
            and "start_yaw" not in kwargs
        ):
            anchor = authored_robot_start_pose(kwargs.get("robot_actor_name"))
            if anchor is not None:
                x, y, yaw = anchor
                kwargs = {**kwargs, "start_xy": (x, y), "start_yaw": yaw}
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
