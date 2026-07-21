"""Project-local paths for the source components used by NaVILA-Orca."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPONENTS_ROOT = Path(
    os.environ.get("NAVILA_ORCA_COMPONENTS_ROOT", PROJECT_ROOT / "components")
).expanduser()


def component_path(name: str, legacy_path: str, env_var: str) -> Path:
    """Prefer a component below this project, then fall back to the old path."""

    configured = os.environ.get(env_var)
    if configured:
        return Path(configured).expanduser()
    local = COMPONENTS_ROOT / name
    return local if local.exists() else Path(legacy_path)


NAVILA_ROOT = component_path("NaVILA", "/home/user/VLN/NaVILA", "NAVILA_ROOT")
NAVILA_BENCH_ROOT = component_path(
    "NaVILA-Bench", "/home/user/VLN/NaVILA-Bench", "NAVILA_BENCH_ROOT"
)
ORCALAB_RSLRL_ROOT = component_path(
    "OrcaLab-RSLRL", "/home/user/OrcaLab-RSLRL", "ORCALAB_RSLRL_ROOT"
)
UNITREE_RL_MJLAB_ROOT = component_path(
    "unitree_rl_mjlab", "/home/user/unitree_rl_mjlab", "UNITREE_RL_MJLAB_ROOT"
)
ORCA_RL_ROOT = component_path("orca_rl", "/home/user/orca_rl", "ORCA_RL_ROOT")
