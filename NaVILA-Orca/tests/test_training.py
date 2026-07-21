from __future__ import annotations

import pytest

from navila_orca.training import (
    CompatibilityError,
    SMOKE_TASK_FACTORY,
    build_training_smoke_argv,
    compatibility_errors,
    ensure_compatible_versions,
)


COMPATIBLE = {
    "mjlab": "1.2.0",
    "mujoco-warp": "3.5.0",
    "rsl-rl-lib": "5.0.1",
    "orca-gym": "26.5.1",
    "orca-lab": "26.5.1",
}


def test_training_smoke_argv_is_explicit_and_one_iteration() -> None:
    argv = build_training_smoke_argv(
        python="/opt/orca/bin/python",
        device="cuda:2",
        num_envs=16,
        output="/tmp/navila-training-smoke",
    )

    assert argv == [
        "/opt/orca/bin/python",
        "-m",
        "orcalab_rslrl.tools.train",
        "--task-factory",
        SMOKE_TASK_FACTORY,
        "--iterations",
        "1",
        "--num-envs",
        "16",
        "--device",
        "cuda:2",
        "--log-dir",
        "/tmp/navila-training-smoke",
        "--wandb-mode",
        "disabled",
    ]


@pytest.mark.parametrize("num_envs", [0, 1, -4])
def test_training_smoke_argv_rejects_too_few_envs(num_envs: int) -> None:
    with pytest.raises(ValueError, match="num_envs >= 2"):
        build_training_smoke_argv(num_envs=num_envs)


def test_compatible_versions_are_accepted() -> None:
    assert compatibility_errors(COMPATIBLE) == ()
    ensure_compatible_versions(COMPATIBLE)


def test_compatible_release_families_are_accepted() -> None:
    versions = {
        **COMPATIBLE,
        "rsl-rl-lib": "5.3.2",
        "orca-gym": "26.5.9",
        "orca-lab": "26.5.12",
    }
    ensure_compatible_versions(versions)


def test_version_error_reports_all_missing_and_mismatched_packages() -> None:
    versions = {
        "mjlab": "1.5.2",
        "mujoco-warp": "3.10.0",
        "rsl-rl-lib": "6.0.0",
        "orca-gym": "26.6.0",
        "orca-lab": None,
    }

    with pytest.raises(CompatibilityError) as caught:
        ensure_compatible_versions(versions)

    message = str(caught.value)
    assert "mjlab: found 1.5.2, expected 1.2.0" in message
    assert "mujoco-warp: found 3.10.0, expected 3.5.0" in message
    assert "rsl-rl-lib: found 6.0.0, expected 5.x" in message
    assert "orca-gym: found 26.6.0, expected 26.5.x" in message
    assert "orca-lab: not installed (expected 26.5.x)" in message
