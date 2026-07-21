from __future__ import annotations

import gzip
import json

import numpy as np
import pytest

from navila_orca.episodes import load_episode


def _write_dataset(path):
    episode = {
        "episode_id": 7,
        "scene_id": "mp3d/example/example.glb",
        "start_position": [1, 2, 3],
        "start_rotation": [1, 0, 0, 0],
        "goals": [{"position": [4, 5, 6], "radius": 0.5}],
        "instruction": {"instruction_text": " Go forward. "},
        "reference_path": [[1, 2, 3], [4, 5, 6]],
        "gt_locations": [[1, 2, 3], [2, 3, 4], [4, 5, 6]],
    }
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump({"episodes": [episode]}, stream)


def test_load_episode(tmp_path):
    dataset = tmp_path / "episodes.json.gz"
    _write_dataset(dataset)
    episode = load_episode(dataset, 0)

    assert episode.episode_id == "7"
    assert episode.instruction == "Go forward."
    assert episode.goal_radius == 0.5
    np.testing.assert_allclose(episode.goal_position, [4, 5, 6])


def test_load_episode_checks_index(tmp_path):
    dataset = tmp_path / "episodes.json.gz"
    _write_dataset(dataset)
    with pytest.raises(IndexError):
        load_episode(dataset, 1)
