<p align="center">
  <h1 align="center">NaVILA–OrcaLab</h1>
  <p align="center">
    A compact navigation stack for running NaVILA on a Go2 inside OrcaLab.
    <br />
    <a href="#quickstart">Quickstart</a> ·
    <a href="NaVILA-Orca/docs/GETTING_STARTED.md">Getting started</a> ·
    <a href="#layout">Repository layout</a>
  </p>
</p>

NaVILA–OrcaLab is a practical starting point for developers who want to connect a visual-language navigation model to a quadruped in OrcaLab. NaVILA sees a rolling window of first-person images and emits a short navigation action; the local Go2 policy turns that action into stable quadruped motion; OrcaLab supplies the industrial-warehouse scene and camera view.

The model stays separate. This repository does not vendor NaVILA/LLaVA source or weights: it connects to a course-provided NaVILA server over TCP. The Go2 task, MJCF, meshes, locomotion checkpoint, camera bridge, default global setting, and reproducible scene case live here.

```text
instruction + 8 RGB frames
            │
            ▼
     NaVILA server                         separate environment
            │  move / turn / stop
            ▼
   navigation adapter                       this repository
            │  vx, vy, wz, duration
            ▼
   Go2 locomotion policy ─────► OrcaLab warehouse + persistent RGB camera
```

## Quickstart

The default case asks Go2 to approach the blue barrel and stop before the yellow vehicle.

```bash
git clone https://github.com/openverse-orca/Orca_VLN.git
cd Orca_VLN/NaVILA-Orca

# terminal A — OrcaLab GUI
conda activate orcalab
python -m pip install -e '.[orca]'
./scripts/start_orcalab_gui.sh

# terminal B — a separately installed NaVILA server
conda activate navila
export NAVILA_SERVER_SCRIPT=/path/to/NaVILA-Bench/scripts/vlm_server.py
export NAVVLM_MODEL_PATH=/path/to/navvlm-llama3-8b-8f
./scripts/start_navvlm_server.sh

# terminal C — navigation
conda activate orcalab
./scripts/run_orcalab_scene_locomotion.sh
```

Before terminal C, load `IndustrialWarehouse1_3dgs` in OrcaLab and import [`default_set.json`](NaVILA-Orca/default_set.json) through the global-setting import UI. The file creates the Go2 and the objects used by the lesson; it is not the warehouse asset itself.

## What is in the box

| Component | Role |
| --- | --- |
| [`default_set.json`](NaVILA-Orca/default_set.json) | Default Go2 + warehouse-object layout for OrcaLab |
| [`demo_episode.json`](NaVILA-Orca/scenes/default_warehouse/demo_episode.json) | Instruction, start pose, goal, and reference path |
| [`navila_orca`](NaVILA-Orca/src/navila_orca) | Navigation loop, camera bridge, scene-profile injection, result writing |
| [`go2_task`](NaVILA-Orca/src/navila_orca/go2_task) | Local MJLab task definition, Go2 MJCF, and meshes |
| [`go2_flat.pt`](NaVILA-Orca/src/navila_orca/assets/checkpoints/go2_flat.pt) | Default locomotion policy checkpoint |

## Layout

```text
NaVILA-Orca/
├── default_set.json                 # import into OrcaLab
├── scenes/default_warehouse/        # the teaching episode
├── src/navila_orca/                 # OrcaLab ↔ Go2 ↔ NaVILA adapter
├── scripts/                         # start, run, train, package
└── docs/                            # camera notes and getting-started guide
```

Useful entry points:

```bash
# Check the local OrcaLab/MJLab stack and packaged assets.
python -m navila_orca.cli doctor

# Train the local Go2 flat-ground policy.
./scripts/train_go2.sh --agent.max-iterations 15001

# Build a clean distribution archive.
./scripts/build_kit.sh
```

## Classroom use

The [getting-started guide](NaVILA-Orca/docs/GETTING_STARTED.md) walks through scene setup, the three-terminal run, what to inspect after each step, language/camera ablations, low-level policy training, and a layer-by-layer troubleshooting table.

For the OrcaLab/MJLab programming model, see [mjlab](https://github.com/mujocolab/mjlab) and [OrcaLocomotion](https://github.com/openverse-orca/OrcaLocomotion/tree/orca_warp).
