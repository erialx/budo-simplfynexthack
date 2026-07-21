"""Load NaVILA-Bench episodes without importing Isaac Lab."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import numpy as np

from .contracts import EpisodeSpec
from .paths import NAVILA_BENCH_ROOT


DEFAULT_DATASET = (
    NAVILA_BENCH_ROOT
    / "isaaclab_exts/omni.isaac.vlnce/assets/vln_ce_isaac_v1.json.gz"
)


def load_episode(path: str | Path = DEFAULT_DATASET, index: int = 0) -> EpisodeSpec:
    """Load one episode from the Isaac-converted R2R dataset.

    The arrays remain in the dataset/Matterport world frame.  A flat MJWarp
    smoke therefore reports navigation metrics as non-authoritative unless a
    matching Orca scene and coordinate transform are supplied.
    """

    dataset_path = Path(path).expanduser().resolve()
    with gzip.open(dataset_path, "rt", encoding="utf-8") as stream:
        payload: dict[str, Any] = json.load(stream)
    episodes = payload.get("episodes")
    if not isinstance(episodes, list):
        raise ValueError(f"Dataset {dataset_path} has no 'episodes' list")
    if not 0 <= index < len(episodes):
        raise IndexError(f"Episode index {index} is outside [0, {len(episodes)})")

    raw = episodes[index]
    goals = raw.get("goals") or []
    if not goals:
        raise ValueError(f"Episode {index} has no goals")
    instruction = raw.get("instruction", {})
    instruction_text = (
        instruction.get("instruction_text", "")
        if isinstance(instruction, dict)
        else str(instruction)
    ).strip()
    reference_path = _xyz_array(raw.get("reference_path"), "reference_path")
    gt_locations = _xyz_array(
        raw.get("gt_locations", raw.get("reference_path")), "gt_locations"
    )
    start_position = _vector(raw.get("start_position"), 3, "start_position")
    start_rotation = _vector(raw.get("start_rotation"), 4, "start_rotation")
    goal_position = _vector(
        goals[0].get("position", reference_path[-1]), 3, "goal.position"
    )

    return EpisodeSpec(
        episode_id=str(raw.get("episode_id", index)),
        scene_id=str(raw.get("scene_id", "")),
        instruction=instruction_text,
        start_position=start_position,
        start_quat_wxyz=start_rotation,
        goal_position=goal_position,
        goal_radius=float(goals[0].get("radius", 3.0)),
        reference_path=reference_path,
        gt_locations=gt_locations,
    )


def _vector(value: Any, size: int, name: str) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.shape != (size,) or not np.isfinite(array).all():
        raise ValueError(f"{name} must be a finite vector of length {size}")
    return array


def _xyz_array(value: Any, name: str) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim != 2 or array.shape[1:] != (3,) or len(array) == 0:
        raise ValueError(f"{name} must be a non-empty [N, 3] array")
    if not np.isfinite(array).all():
        raise ValueError(f"{name} contains non-finite values")
    return array
