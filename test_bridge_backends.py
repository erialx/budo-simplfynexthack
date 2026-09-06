"""Headless tests for bridge_backends.OrcaLabMirrorBackend.capture_frame (C2's
camera-capture-only fallback, docs/PLAN.md "C" item 3) and
bridge_backends.trigger_scene_hazard (the in-scene hazard trigger, "C" item 5).

OrcaLabMirrorBackend's pose-mirror side (_connect/_mirror) has no unit tests
here on purpose -- it needs the real `orcalab.*` edit-service package and was
verified live against a running OrcaLab GUI instead (see docs/STAGE3_TESTING.md
and CLAUDE.md's C1 status). capture_frame() reuses that same _service/_call
plumbing, so these tests bypass _connect() entirely and inject a fake service
object directly -- exercising the actual PNG-read code path (via a real tiny
PNG written to disk) without needing OrcaLab, grpc, or a GPU.

trigger_scene_hazard() and reset_scene_layout() open their own connection per
call rather than reusing a persistent one, so their tests patch
bridge_backends._load_orca_edit_runtime to return a fake (service_factory,
Transform, Path) triple instead -- exercising the real connect/write/
disconnect coroutine without needing orcalab.* either. load_scene_layout()
just parses a street.json-shaped file, tested against real temp files.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

import numpy as np
from PIL import Image

import bridge_backends as bb


def _make_backend(*, camera=True):
    backend = bb.OrcaLabMirrorBackend(inner=bb.MockBackend(), camera=camera)
    # Bypass _connect(): no real orcalab.* package / gRPC channel in this env.
    backend._mirror_disabled = False
    backend._service = object()  # replaced per-test below where it matters
    backend._call = lambda coro: asyncio.run(coro)
    return backend


class _FakeService:
    """Stands in for EditServiceWrapper.get_camera_png: writes a real PNG of
    `pixel_value` to <output_dir>/<filename> and reports success."""

    def __init__(self, pixel_value=(9, 9, 9), size=(4, 4), succeed=True):
        self.pixel_value = pixel_value
        self.size = size
        self.succeed = succeed
        self.calls = []

    async def get_camera_png(self, camera_name, output_dir, filename):
        self.calls.append((camera_name, output_dir, filename))
        if not self.succeed:
            return False
        path = os.path.join(output_dir, filename)
        Image.new("RGB", self.size, self.pixel_value).save(path)
        return True


class _RaisingService:
    async def get_camera_png(self, camera_name, output_dir, filename):
        raise RuntimeError("simulated edit-service RPC failure")


def test_capture_frame_disabled_returns_none_without_touching_service():
    backend = _make_backend(camera=False)
    backend._service = _FakeService()
    assert backend.capture_frame() is None
    assert backend._service.calls == []


def test_capture_frame_none_when_mirror_never_connected():
    backend = _make_backend(camera=True)
    backend._service = None  # _connect() never ran / failed
    assert backend.capture_frame() is None


def test_capture_frame_reads_real_png_written_by_the_service():
    backend = _make_backend(camera=True)
    backend._service = _FakeService(pixel_value=(200, 20, 20), size=(8, 6))
    frame = backend.capture_frame()
    assert frame is not None
    assert frame.shape == (6, 8, 3)  # PIL size is (w, h); array shape is (h, w, c)
    assert np.all(frame == np.array([200, 20, 20], dtype=np.uint8))


def test_capture_frame_advances_request_index_and_reuses_camera_name():
    backend = _make_backend(camera=True)
    service = _FakeService()
    backend._service = service
    backend.capture_frame()
    backend.capture_frame()
    assert [c[0] for c in service.calls] == ["mujococamera1080", "mujococamera1080"]
    assert service.calls[0][2] != service.calls[1][2]  # distinct filenames


def test_capture_frame_none_when_service_reports_failure():
    backend = _make_backend(camera=True)
    backend._service = _FakeService(succeed=False)
    assert backend.capture_frame() is None
    assert backend._camera_failures == 1


def test_capture_frame_none_when_service_raises():
    backend = _make_backend(camera=True)
    backend._service = _RaisingService()
    assert backend.capture_frame() is None  # must not propagate
    assert backend._camera_failures == 1


def _with_env(name, value, fn):
    """Zero-dependency env-var override -- avoids depending on pytest's
    monkeypatch fixture so this file's __main__ runner keeps working too."""
    had = name in os.environ
    old = os.environ.get(name)
    os.environ[name] = value
    try:
        fn()
    finally:
        if had:
            os.environ[name] = old
        else:
            del os.environ[name]


def test_capture_frame_custom_camera_name_env():
    def _check():
        backend = bb.OrcaLabMirrorBackend(inner=bb.MockBackend(), camera=True)
        backend._service = _FakeService()
        backend._call = lambda coro: asyncio.run(coro)
        backend.capture_frame()
        assert backend._service.calls[0][0] == "custom_cam"

    _with_env("NAVILA_BRIDGE_ORCA_CAMERA_NAME", "custom_cam", _check)


def test_camera_env_flag_defaults_off():
    backend = bb.OrcaLabMirrorBackend(inner=bb.MockBackend())
    assert backend._camera_enabled is False


def test_camera_env_flag_on():
    def _check():
        backend = bb.OrcaLabMirrorBackend(inner=bb.MockBackend())
        assert backend._camera_enabled is True

    _with_env("NAVILA_BRIDGE_ORCA_CAMERA", "1", _check)


# ---------------------------------------------------------------------------
# trigger_scene_hazard (docs/PLAN.md "C" item 5: in-scene hazard trigger)
# ---------------------------------------------------------------------------

class _FakeTransform:
    def __init__(self, position, rotation, scale):
        self.position = position
        self.rotation = rotation
        self.scale = scale


class _FakePath:
    def __init__(self, path):
        self.path = path


class _FakeEditService:
    """Stands in for EditServiceWrapper for trigger_scene_hazard's tests."""

    def __init__(self, *, aloha_ok=True, fail_transform=False):
        self.aloha_ok = aloha_ok
        self.fail_transform = fail_transform
        self.init_calls = []
        self.transform_calls = []
        self.destroyed = False

    async def init_grpc(self, address):
        self.init_calls.append(address)

    async def aloha(self):
        return self.aloha_ok

    async def set_actor_transform_batch(self, paths, transforms):
        if self.fail_transform:
            raise RuntimeError("simulated write failure")
        self.transform_calls.append((paths, transforms))

    async def destroy_grpc(self):
        self.destroyed = True


def _with_patched_edit_runtime(service, fn):
    orig = bb._load_orca_edit_runtime
    bb._load_orca_edit_runtime = lambda: (lambda: service, _FakeTransform, _FakePath)
    try:
        fn()
    finally:
        bb._load_orca_edit_runtime = orig


def test_trigger_scene_hazard_moves_actor_and_disconnects():
    service = _FakeEditService()

    def _check():
        bb.trigger_scene_hazard(
            "blue_hatchback_car_1", (1.0, 2.0, 0.0), (1.0, 0.0, 0.0, 0.0)
        )
        assert service.init_calls == ["127.0.0.1:50151"]
        assert len(service.transform_calls) == 1
        paths, transforms = service.transform_calls[0]
        assert paths[0].path == "/blue_hatchback_car_1"
        assert np.allclose(transforms[0].position, [1.0, 2.0, 0.0])
        assert np.allclose(transforms[0].rotation, [1.0, 0.0, 0.0, 0.0])
        assert service.destroyed is True

    _with_patched_edit_runtime(service, _check)


def test_trigger_scene_hazard_uses_custom_edit_address():
    service = _FakeEditService()

    def _check():
        bb.trigger_scene_hazard("x", (0.0, 0.0, 0.0), edit_address="10.0.0.5:9999")
        assert service.init_calls == ["10.0.0.5:9999"]

    _with_patched_edit_runtime(service, _check)


def test_trigger_scene_hazard_raises_when_edit_service_unreachable():
    service = _FakeEditService(aloha_ok=False)

    def _check():
        try:
            bb.trigger_scene_hazard("blue_hatchback_car_1", (0.0, 0.0, 0.0))
        except RuntimeError as exc:
            assert "not reachable" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")
        assert service.transform_calls == []  # never got to the write
        assert service.destroyed is True  # cleanup still ran

    _with_patched_edit_runtime(service, _check)


def test_trigger_scene_hazard_raises_on_write_failure_and_still_disconnects():
    service = _FakeEditService(fail_transform=True)

    def _check():
        try:
            bb.trigger_scene_hazard("blue_hatchback_car_1", (0.0, 0.0, 0.0))
        except RuntimeError as exc:
            assert "simulated write failure" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")
        assert service.destroyed is True  # finally-block cleanup still ran

    _with_patched_edit_runtime(service, _check)


# ---------------------------------------------------------------------------
# load_scene_layout / reset_scene_layout (docs/PLAN.md "C" item 6: scene
# reset reliability)
# ---------------------------------------------------------------------------

def _write_scene_json(actors):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump({"actors": actors}, f)
    return path


def test_load_scene_layout_parses_position_rotation_scale():
    path = _write_scene_json([
        {
            "name": "a1",
            "transform": {"position": [1, 2, 3], "rotation": [1, 0, 0, 0], "scale": 2.0},
        },
        {"name": "no_transform"},
        {"transform": {"position": [0, 0, 0], "rotation": [1, 0, 0, 0]}},  # no name
    ])
    try:
        layout = bb.load_scene_layout(path)
        assert set(layout) == {"a1"}
        assert layout["a1"]["position"] == (1.0, 2.0, 3.0)
        assert layout["a1"]["rotation_wxyz"] == (1.0, 0.0, 0.0, 0.0)
        assert layout["a1"]["scale"] == 2.0
    finally:
        os.remove(path)


def test_load_scene_layout_defaults_scale_to_one():
    path = _write_scene_json([
        {"name": "a1", "transform": {"position": [0, 0, 0], "rotation": [1, 0, 0, 0]}},
    ])
    try:
        layout = bb.load_scene_layout(path)
        assert layout["a1"]["scale"] == 1.0
    finally:
        os.remove(path)


def test_load_scene_layout_env_var_default():
    path = _write_scene_json([
        {"name": "env_actor", "transform": {"position": [9, 9, 9], "rotation": [1, 0, 0, 0]}},
    ])

    def _check():
        layout = bb.load_scene_layout()  # path=None -> env var
        assert set(layout) == {"env_actor"}

    try:
        _with_env("NAVILA_BRIDGE_SCENE_LAYOUT", path, _check)
    finally:
        os.remove(path)


def test_reset_scene_layout_restores_all_except_robot_by_default():
    layout = {
        "quadruped_robot_1": {
            "position": (0.0, 0.0, 0.0), "rotation_wxyz": (1.0, 0.0, 0.0, 0.0), "scale": 1.0
        },
        "blue_hatchback_car_1": {
            "position": (5.0, 6.0, 0.0), "rotation_wxyz": (1.0, 0.0, 0.0, 0.0), "scale": 1.0
        },
        "traffic_light_1": {
            "position": (1.0, 1.0, 0.0), "rotation_wxyz": (1.0, 0.0, 0.0, 0.0), "scale": 1.0
        },
    }
    service = _FakeEditService()

    def _check():
        restored = bb.reset_scene_layout(layout)
        assert set(restored) == {"blue_hatchback_car_1", "traffic_light_1"}
        assert len(service.transform_calls) == 1
        paths, transforms = service.transform_calls[0]
        assert {p.path for p in paths} == {"/blue_hatchback_car_1", "/traffic_light_1"}

    _with_patched_edit_runtime(service, _check)


def test_reset_scene_layout_explicit_actor_names_overrides_robot_exclusion():
    layout = {
        "quadruped_robot_1": {
            "position": (0.0, 0.0, 0.0), "rotation_wxyz": (1.0, 0.0, 0.0, 0.0), "scale": 1.0
        },
        "blue_hatchback_car_1": {
            "position": (5.0, 6.0, 0.0), "rotation_wxyz": (1.0, 0.0, 0.0, 0.0), "scale": 1.0
        },
    }
    service = _FakeEditService()

    def _check():
        restored = bb.reset_scene_layout(layout, actor_names=["quadruped_robot_1"])
        assert restored == ["quadruped_robot_1"]
        paths, _transforms = service.transform_calls[0]
        assert paths[0].path == "/quadruped_robot_1"

    _with_patched_edit_runtime(service, _check)


def test_reset_scene_layout_raises_on_unknown_actor_name():
    layout = {
        "blue_hatchback_car_1": {
            "position": (0.0, 0.0, 0.0), "rotation_wxyz": (1.0, 0.0, 0.0, 0.0), "scale": 1.0
        },
    }
    try:
        bb.reset_scene_layout(layout, actor_names=["nonexistent_actor"])
    except KeyError as exc:
        assert "nonexistent_actor" in str(exc)
    else:
        raise AssertionError("expected KeyError")


def test_reset_scene_layout_empty_result_never_contacts_edit_service():
    layout = {
        "quadruped_robot_1": {
            "position": (0.0, 0.0, 0.0), "rotation_wxyz": (1.0, 0.0, 0.0, 0.0), "scale": 1.0
        },
    }

    def _boom():
        raise AssertionError("edit service should not be contacted for an empty restore set")

    orig = bb._load_orca_edit_runtime
    bb._load_orca_edit_runtime = _boom
    try:
        restored = bb.reset_scene_layout(layout)  # only actor is the excluded robot
        assert restored == []
    finally:
        bb._load_orca_edit_runtime = orig


def test_reset_scene_layout_custom_exclude_actors():
    layout = {
        "quadruped_robot_1": {
            "position": (0.0, 0.0, 0.0), "rotation_wxyz": (1.0, 0.0, 0.0, 0.0), "scale": 1.0
        },
        "blue_hatchback_car_1": {
            "position": (0.0, 0.0, 0.0), "rotation_wxyz": (1.0, 0.0, 0.0, 0.0), "scale": 1.0
        },
    }
    service = _FakeEditService()

    def _check():
        restored = bb.reset_scene_layout(layout, exclude_actors=["blue_hatchback_car_1"])
        assert set(restored) == {"quadruped_robot_1"}

    _with_patched_edit_runtime(service, _check)


def test_reset_scene_layout_uses_custom_robot_actor_env():
    layout = {
        "custom_robot": {
            "position": (0.0, 0.0, 0.0), "rotation_wxyz": (1.0, 0.0, 0.0, 0.0), "scale": 1.0
        },
        "blue_hatchback_car_1": {
            "position": (0.0, 0.0, 0.0), "rotation_wxyz": (1.0, 0.0, 0.0, 0.0), "scale": 1.0
        },
    }
    service = _FakeEditService()

    def _inner():
        restored = bb.reset_scene_layout(layout)
        assert set(restored) == {"blue_hatchback_car_1"}

    def _check():
        _with_patched_edit_runtime(service, _inner)

    _with_env("NAVILA_BRIDGE_ORCA_ROBOT_ACTOR", "custom_robot", _check)


def test_reset_scene_layout_raises_when_edit_service_unreachable():
    layout = {
        "blue_hatchback_car_1": {
            "position": (0.0, 0.0, 0.0), "rotation_wxyz": (1.0, 0.0, 0.0, 0.0), "scale": 1.0
        },
    }
    service = _FakeEditService(aloha_ok=False)

    def _check():
        try:
            bb.reset_scene_layout(layout)
        except RuntimeError as exc:
            assert "not reachable" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")

    _with_patched_edit_runtime(service, _check)


# ---------------------------------------------------------------------------
# spawn_camera_actor (add the persistent mujococamera1080 ego camera)
# ---------------------------------------------------------------------------

class _FakeAssetActor:
    def __init__(self, name, asset_path):
        self.name = name
        self.asset_path = asset_path
        self.transform = None


class _FakeAddActorRequest:
    def __init__(self, actor, parent):
        self.actor = actor
        self.parent = parent


class _FakePathWithRoot(_FakePath):
    @classmethod
    def root_path(cls):
        return cls("/")


class _FakeSpawnService(_FakeEditService):
    def __init__(self, *, aloha_ok=True, exists=False, add_ok=True):
        super().__init__(aloha_ok=aloha_ok)
        self.exists = exists
        self.add_ok = add_ok
        self.add_calls = []
        self.delete_calls = []

    async def get_actor_property_groups_batch(self, paths):
        if not self.exists:
            raise RuntimeError("actor not found")
        return [{}]

    async def delete_actor_batch(self, paths):
        self.delete_calls.append(paths)
        self.exists = False

    async def add_actor_batch(self, requests, *args):
        self.add_calls.append((requests, args))
        if not self.add_ok:
            if args:  # (requests, True) two-arg form -> (added, errors)
                return False, ["simulated add failure"]
            raise RuntimeError("simulated add failure")
        return (True, []) if args else None


def _with_patched_spawn_runtime(service, fn):
    orig = bb._load_orca_spawn_runtime
    bb._load_orca_spawn_runtime = lambda: (
        lambda: service,
        _FakeTransform,
        _FakePathWithRoot,
        _FakeAssetActor,
        _FakeAddActorRequest,
    )
    try:
        fn()
    finally:
        bb._load_orca_spawn_runtime = orig


def test_spawn_camera_actor_adds_when_absent_and_disconnects():
    service = _FakeSpawnService(exists=False)

    def _check():
        info = bb.spawn_camera_actor()
        assert info["created"] is True
        assert info["actor_name"] == "mujococamera1080"
        assert len(service.add_calls) == 1
        assert service.destroyed is True
        actor = service.add_calls[0][0][0].actor
        assert actor.asset_path == "prefabs/mujococamera1080"
        assert actor.transform is not None

    _with_patched_spawn_runtime(service, _check)


def test_spawn_camera_actor_is_noop_when_already_present():
    service = _FakeSpawnService(exists=True)

    def _check():
        info = bb.spawn_camera_actor()
        assert info["created"] is False
        assert not service.add_calls
        assert not service.delete_calls

    _with_patched_spawn_runtime(service, _check)


def test_spawn_camera_actor_replace_deletes_then_adds():
    service = _FakeSpawnService(exists=True)

    def _check():
        info = bb.spawn_camera_actor(replace=True)
        assert info["created"] is True
        assert len(service.delete_calls) == 1
        assert len(service.add_calls) == 1

    _with_patched_spawn_runtime(service, _check)


def test_spawn_camera_actor_raises_when_edit_service_unreachable():
    service = _FakeSpawnService(aloha_ok=False)

    def _check():
        try:
            bb.spawn_camera_actor()
        except RuntimeError as exc:
            assert "not reachable" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")

    _with_patched_spawn_runtime(service, _check)


def test_spawn_camera_actor_raises_on_add_failure_and_still_disconnects():
    service = _FakeSpawnService(exists=False, add_ok=False)

    def _check():
        try:
            bb.spawn_camera_actor()
        except RuntimeError as exc:
            assert "add" in str(exc).lower()
        else:
            raise AssertionError("expected RuntimeError")
        assert service.destroyed is True

    _with_patched_spawn_runtime(service, _check)


if __name__ == "__main__":
    import sys
    import traceback

    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
        except Exception:  # noqa: BLE001
            failed.append(name)
            print(f"FAIL {name}")
            traceback.print_exc()
        else:
            passed += 1
            print(f"PASS {name}")
    print(f"\n{passed}/{len(tests)} passed")
    if failed:
        print("Failed:", ", ".join(failed))
        sys.exit(1)
