"""Training smoke launcher and compatibility guard for NaVILA-Orca.

The native OrcaLab-RSLRL registry currently contains G1 only; it does not
contain a Go2 training task.  The one-iteration command in this module uses
``orcalab_rslrl.tasks.smoke:make_train_env`` solely to validate the MJWarp to
RSL-RL training plumbing.  Go2 policy and physics are reused from the local
``unitree_rl_mjlab`` project until a real Go2 task is ported and registered.
"""

from __future__ import annotations

import argparse
from importlib import metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Mapping, Sequence

from navila_orca.paths import ORCALAB_RSLRL_ROOT, UNITREE_RL_MJLAB_ROOT

SMOKE_TASK_FACTORY = "orcalab_rslrl.tasks.smoke:make_train_env"
DEFAULT_ORCALAB_RSLRL_ROOT = ORCALAB_RSLRL_ROOT

GO2_TRAINING_STATUS = (
    "OrcaLab-RSLRL's native task registry currently has no Go2 task. "
    "This smoke validates generic MJWarp/RSL-RL plumbing only; the current "
    f"Go2 policy and physics are reused from {UNITREE_RL_MJLAB_ROOT}."
)

# Distribution names are deliberately kept identical to importlib.metadata
# names so diagnostic output can be pasted directly into `pip show`.
COMPATIBLE_VERSION_SPECS = {
    "mjlab": "1.2.0",
    "mujoco-warp": "3.5.0",
    "rsl-rl-lib": "5.x",
    "orca-gym": "26.5.x",
    "orca-lab": "26.5.x",
}


class CompatibilityError(RuntimeError):
    """Raised when the selected Python environment has an incompatible stack."""


def build_training_smoke_argv(
    *,
    python: str | os.PathLike[str] = sys.executable,
    device: str = "cuda:0",
    num_envs: int = 2,
    output: str | os.PathLike[str] = "outputs/training-smoke",
) -> list[str]:
    """Build the explicit, shell-free argv for a one-iteration training smoke."""

    python_arg = os.fspath(python)
    output_arg = os.fspath(output)
    if not python_arg:
        raise ValueError("python must not be empty")
    if not device:
        raise ValueError("device must not be empty")
    if num_envs < 2:
        raise ValueError("training smoke requires num_envs >= 2")
    if not output_arg:
        raise ValueError("output must not be empty")

    return [
        python_arg,
        "-m",
        "orcalab_rslrl.tools.train",
        "--task-factory",
        SMOKE_TASK_FACTORY,
        "--iterations",
        "1",
        "--num-envs",
        str(num_envs),
        "--device",
        device,
        "--log-dir",
        output_arg,
        "--wandb-mode",
        "disabled",
    ]


def _matches_version(distribution: str, installed: str) -> bool:
    expected = COMPATIBLE_VERSION_SPECS[distribution]
    if expected in {"1.2.0", "3.5.0"}:
        # A local build tag does not change the compatible release.  Pre/dev
        # releases are rejected because they may not preserve the tested ABI.
        return (
            re.fullmatch(re.escape(expected) + r"(?:\+[A-Za-z0-9._-]+)?", installed)
            is not None
        )
    if expected == "5.x":
        return re.fullmatch(r"5(?:\.\d+)+(?:\+[A-Za-z0-9._-]+)?", installed) is not None
    if expected == "26.5.x":
        return (
            re.fullmatch(r"26\.5(?:\.\d+)+(?:\+[A-Za-z0-9._-]+)?", installed)
            is not None
        )
    raise AssertionError(f"Unhandled compatibility spec: {distribution} {expected}")


def compatibility_errors(
    installed_versions: Mapping[str, str | None],
) -> tuple[str, ...]:
    """Return all stack mismatches without importing GPU-backed packages."""

    errors: list[str] = []
    for distribution, expected in COMPATIBLE_VERSION_SPECS.items():
        installed = installed_versions.get(distribution)
        if installed is None:
            errors.append(f"{distribution}: not installed (expected {expected})")
        elif not _matches_version(distribution, installed):
            errors.append(f"{distribution}: found {installed}, expected {expected}")
    return tuple(errors)


def ensure_compatible_versions(installed_versions: Mapping[str, str | None]) -> None:
    """Raise one readable error containing every incompatible distribution."""

    errors = compatibility_errors(installed_versions)
    if errors:
        details = "\n".join(f"  - {error}" for error in errors)
        raise CompatibilityError(
            "Incompatible OrcaLab training environment:\n"
            f"{details}\n"
            "Use the tested OrcaLab 26.5 environment; do not upgrade mjlab or "
            "mujoco-warp in place."
        )


def current_installed_versions() -> dict[str, str | None]:
    """Read versions from the Python process running this module."""

    versions: dict[str, str | None] = {}
    for distribution in COMPATIBLE_VERSION_SPECS:
        try:
            versions[distribution] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            versions[distribution] = None
    return versions


def probe_installed_versions(python: str | os.PathLike[str]) -> dict[str, str | None]:
    """Inspect the selected interpreter without importing simulator libraries."""

    python_arg = os.fspath(python)
    if os.path.realpath(python_arg) == os.path.realpath(sys.executable):
        return current_installed_versions()

    distributions = tuple(COMPATIBLE_VERSION_SPECS)
    probe = (
        "import json\n"
        "from importlib import metadata\n"
        f"names = {distributions!r}\n"
        "result = {}\n"
        "for name in names:\n"
        "    try:\n"
        "        result[name] = metadata.version(name)\n"
        "    except metadata.PackageNotFoundError:\n"
        "        result[name] = None\n"
        "print('NAVILA_ORCA_VERSIONS=' + json.dumps(result, sort_keys=True))\n"
    )
    try:
        completed = subprocess.run(
            [python_arg, "-c", probe],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise CompatibilityError(
            f"Could not run Python interpreter {python_arg!r}: {exc}"
        ) from exc
    if completed.returncode != 0:
        detail = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or "no diagnostic output"
        )
        raise CompatibilityError(
            f"Could not inspect packages with {python_arg!r} (exit {completed.returncode}): {detail}"
        )

    marker = "NAVILA_ORCA_VERSIONS="
    payload = next(
        (
            line[len(marker) :]
            for line in reversed(completed.stdout.splitlines())
            if line.startswith(marker)
        ),
        None,
    )
    if payload is None:
        raise CompatibilityError(
            f"Version probe from {python_arg!r} returned no parseable result"
        )
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CompatibilityError(
            f"Version probe from {python_arg!r} returned invalid JSON"
        ) from exc
    return {name: parsed.get(name) for name in distributions}


def run_training_smoke(
    *,
    python: str | os.PathLike[str] = sys.executable,
    device: str = "cuda:0",
    num_envs: int = 2,
    output: str | os.PathLike[str] = "outputs/training-smoke",
    orcalab_rslrl_root: str | os.PathLike[str] = DEFAULT_ORCALAB_RSLRL_ROOT,
) -> subprocess.CompletedProcess[bytes]:
    """Validate versions and run the local one-iteration smoke subprocess."""

    root = Path(orcalab_rslrl_root).expanduser().resolve()
    smoke_source = root / "orcalab_rslrl" / "tasks" / "smoke.py"
    if not smoke_source.is_file():
        raise FileNotFoundError(
            f"Local OrcaLab-RSLRL smoke factory was not found at {smoke_source}"
        )

    versions = probe_installed_versions(python)
    ensure_compatible_versions(versions)
    argv = build_training_smoke_argv(
        python=python,
        device=device,
        num_envs=num_envs,
        output=Path(output).expanduser().resolve(),
    )
    return subprocess.run(argv, cwd=root, check=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the local OrcaLab-RSLRL one-iteration MJWarp/RSL-RL smoke."
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter in the OrcaLab environment",
    )
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--num-envs", type=int, default=2)
    parser.add_argument("--output", default="outputs/training-smoke")
    parser.add_argument(
        "--orcalab-rslrl-root",
        default=os.environ.get(
            "ORCALAB_RSLRL_ROOT", os.fspath(DEFAULT_ORCALAB_RSLRL_ROOT)
        ),
        help="Path to the local OrcaLab-RSLRL checkout",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Check target-environment versions without starting CUDA training",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        versions = probe_installed_versions(args.python)
        ensure_compatible_versions(versions)
        print(
            "Compatible stack: "
            + ", ".join(f"{name}={version}" for name, version in versions.items())
        )
        print(GO2_TRAINING_STATUS)
        if args.check_only:
            return 0
        run_training_smoke(
            python=args.python,
            device=args.device,
            num_envs=args.num_envs,
            output=args.output,
            orcalab_rslrl_root=args.orcalab_rslrl_root,
        )
    except (CompatibilityError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
