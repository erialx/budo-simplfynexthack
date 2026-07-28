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
