from __future__ import annotations

import os
from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"


def _make_executable(path: Path, body: str = "#!/usr/bin/env bash\nexit 0\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def test_runtime_resolver_prefers_project_prefix_over_active_conda(
    tmp_path: Path,
) -> None:
    project_prefix = tmp_path / "project-orcalab"
    active_prefix = tmp_path / "active-navila"
    _make_executable(project_prefix / "bin/python")
    _make_executable(project_prefix / "bin/orcalab")
    _make_executable(active_prefix / "bin/python", "#!/usr/bin/env bash\nexit 1\n")

    command = (
        'source "$1"; navila_orca_resolve_runtime; '
        'printf "%s\\n%s\\n" "$NAVILA_ORCA_PYTHON" "$NAVILA_ORCA_ORCALAB_BIN"'
    )
    env = {
        **os.environ,
        "NAVILA_ORCALAB_ENV_PREFIX": str(project_prefix),
        "CONDA_PREFIX": str(active_prefix),
    }
    env.pop("NAVILA_ORCA_PYTHON", None)
    env.pop("NAVILA_ORCA_ORCALAB_BIN", None)
    result = subprocess.run(
        ["bash", "-c", command, "bash", str(SCRIPTS / "orcalab_env.sh")],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.stdout.splitlines() == [
        str(project_prefix / "bin/python"),
        str(project_prefix / "bin/orcalab"),
    ]


def test_runtime_resolver_explains_missing_project_environment(
    tmp_path: Path,
) -> None:
    command = 'source "$1"; navila_orca_resolve_runtime'
    env = {
        **os.environ,
        "NAVILA_ORCALAB_ENV_PREFIX": str(tmp_path / "missing"),
        "CONDA_PREFIX": str(tmp_path / "also-missing"),
    }
    env.pop("NAVILA_ORCA_PYTHON", None)
    env.pop("NAVILA_ORCA_ORCALAB_BIN", None)
    result = subprocess.run(
        ["bash", "-c", command, "bash", str(SCRIPTS / "orcalab_env.sh")],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 2
    assert "setup_orcalab_env.sh" in result.stderr


def test_navila_server_rejects_partial_model_directory(tmp_path: Path) -> None:
    model = tmp_path / "model"
    model.mkdir()
    env = {
        **os.environ,
        "NAVVLM_MODEL_PATH": str(model),
        "NAVVLM_PYTHON": "/bin/true",
    }
    result = subprocess.run(
        [str(SCRIPTS / "start_navvlm_server.sh")],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 2
    assert "missing or incomplete" in result.stderr


def test_transformers_reinstall_cannot_reresolve_locked_dependencies() -> None:
    script = (SCRIPTS / "setup_navila_env.sh").read_text()

    assert 'pip install --force-reinstall --no-deps \\' in script


def test_nvidia_preflight_explains_driver_library_mismatch(tmp_path: Path) -> None:
    fake_smi = tmp_path / "nvidia-smi"
    _make_executable(
        fake_smi,
        "#!/usr/bin/env bash\n"
        "echo 'Failed to initialize NVML: Driver/library version mismatch' >&2\n"
        "echo 'NVML library version: 580.173' >&2\n"
        "exit 1\n",
    )
    result = subprocess.run(
        [str(SCRIPTS / "check_nvidia_driver.sh")],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "NVIDIA_SMI_BIN": str(fake_smi), "LD_LIBRARY_PATH": ""},
    )

    assert result.returncode == 2
    assert "Driver/library version mismatch" in result.stderr
    assert "Reboot the computer once" in result.stderr
    assert "Do not delete or reinstall" in result.stderr


def test_nvidia_preflight_detects_library_path_pollution(tmp_path: Path) -> None:
    fake_smi = tmp_path / "nvidia-smi"
    _make_executable(
        fake_smi,
        "#!/usr/bin/env bash\n"
        'if [[ -n "${LD_LIBRARY_PATH:-}" ]]; then exit 1; fi\n'
        "echo 'GPU 0: test'\n",
    )
    result = subprocess.run(
        [str(SCRIPTS / "check_nvidia_driver.sh")],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "NVIDIA_SMI_BIN": str(fake_smi),
            "LD_LIBRARY_PATH": "/fake/cuda/stubs",
        },
    )

    assert result.returncode == 2
    assert "unset LD_LIBRARY_PATH" in result.stderr


def test_system_dependency_check_reports_missing_qt_xcb_package(
    tmp_path: Path,
) -> None:
    fake_dpkg = tmp_path / "dpkg-query"
    _make_executable(fake_dpkg, "#!/usr/bin/env bash\nexit 1\n")
    result = subprocess.run(
        [str(SCRIPTS / "setup_system_deps.sh"), "--verify"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "DPKG_QUERY_BIN": str(fake_dpkg)},
    )

    assert result.returncode == 2
    assert "libxcb-cursor0" in result.stderr
    assert "setup_system_deps.sh" in result.stderr


def test_navila_install_and_server_verify_the_real_builder_import() -> None:
    constraints = (PROJECT_ROOT / "constraints/navila-rss2025.txt").read_text()
    setup = (SCRIPTS / "setup_navila_env.sh").read_text()
    server = (SCRIPTS / "start_navvlm_server.sh").read_text()

    assert "deepspeed==0.9.5" in constraints
    assert "from llava.model.builder import load_pretrained_model" in setup
    assert "from llava.model.builder import load_pretrained_model" in server
