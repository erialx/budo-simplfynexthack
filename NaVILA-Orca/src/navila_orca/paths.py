"""Paths owned by this distributable NaVLM-Orca project.

There are deliberately no fallbacks to sibling repositories or absolute local
checkouts.  Everything that the runtime needs beyond installed OrcaLab/MJLab
distributions lives below this project root.
"""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parent
ASSETS_ROOT = PACKAGE_ROOT / "assets"
CHECKPOINT_ROOT = ASSETS_ROOT / "checkpoints"
DEFAULT_GO2_CHECKPOINT = CHECKPOINT_ROOT / "go2_flat.pt"
BUNDLED_GO2_XML = PACKAGE_ROOT / "go2_task/assets/robots/unitree_go2/xmls/go2.xml"
SCENES_ROOT = PROJECT_ROOT / "scenes"
DEFAULT_WAREHOUSE_SCENE = SCENES_ROOT / "default_warehouse"
DEFAULT_GLOBAL_SETTINGS = PROJECT_ROOT / "default_set.json"
DEFAULT_DEMO_EPISODE = DEFAULT_WAREHOUSE_SCENE / "demo_episode.json"

# This is project source, not an editable dependency or an external checkout.
GO2_TASK_PACKAGE = "navila_orca.go2_task.tasks"
