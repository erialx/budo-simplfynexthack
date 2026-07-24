<p align="center">
  <h1 align="center">NaVILA–OrcaLab</h1>
  <p align="center">
    Visual-language navigation for legged robots in OrcaLab.
    <br />
    <a href="#quickstart">Quickstart</a> ·
    <a href="#vln-runtime">VLN runtime</a> ·
    <a href="NaVILA-Orca/docs/GETTING_STARTED.md">Developer guide</a> ·
    <a href="#contributing">Contributing</a>
  </p>
</p>

NaVILA–OrcaLab is a closed-loop VLN runtime for deploying NaVILA on a Go2 in OrcaLab. It turns language-grounded visual decisions into stable robot motion, maintains a persistent ego camera, and records the full navigation trace in a photorealistic industrial environment.

The repository ships the OrcaLab-facing part of the stack: the Go2 locomotion runtime, camera bridge, scene lifecycle, default warehouse layout, navigation episode, and output artifacts. Connect a NaVILA server and run a complete visual-language navigation loop without wiring the simulator and robot-control layers yourself.

```text
Language instruction + temporal ego RGB
                  │
                  ▼
           NaVILA VLN policy
                  │  navigation action
                  ▼
      NaVILA–OrcaLab runtime
                  │  velocity command
                  ▼
      Go2 locomotion + OrcaLab scene
                  │
                  ▼
          next ego observation
```

## VLN runtime

| Capability | Included behavior |
| --- | --- |
| Temporal visual input | Sends a rolling 8-frame Go2 RGB history to NaVILA |
| Natural-language control | Converts `move`, `turn`, and `stop` responses into bounded velocity commands |
| Persistent ego camera | Uses `prefabs/mujococamera1080`; one camera actor follows the robot throughout the run |
| Go2 execution | Runs the packaged flat-ground locomotion policy at 50 Hz |
| Scene continuity | Reuses the authored OrcaLab layout and reapplies the MuJoCo scene profile after scene switches |
| Run evidence | Saves actions, trajectory state, scene alignment data, and RGB observations |

The default warehouse episode asks Go2 to approach the blue barrel and stop before the yellow vehicle. It is intentionally small: a dependable baseline for bringing up a new model, changing prompts, moving the camera, or replacing the locomotion policy.

## Quickstart

```bash
git clone https://github.com/openverse-orca/Orca_VLN.git
cd Orca_VLN/NaVILA-Orca

# terminal A — OrcaLab
conda activate orcalab
python -m pip install -e '.[orca]'
./scripts/start_orcalab_gui.sh

# terminal B — NaVILA, installed in its own environment
conda activate navila
export NAVILA_SERVER_SCRIPT=/path/to/NaVILA-Bench/scripts/vlm_server.py
export NAVVLM_MODEL_PATH=/path/to/navvlm-llama3-8b-8f
./scripts/start_navvlm_server.sh

# terminal C — VLN runtime
conda activate orcalab
./scripts/run_orcalab_scene_locomotion.sh
```

Before starting the runtime, open `IndustrialWarehouse1_3dgs` in OrcaLab and import [`default_set.json`](NaVILA-Orca/default_set.json) through the global-setting UI. The setting instantiates the Go2 and reference objects; the 3DGS warehouse remains an OrcaLab asset.

## Integration contract

NaVILA runs as a separate service and owns model loading and inference. The only contract required by this repository is a local server that accepts an image sequence and navigation instruction, then replies with a canonical action string. Configure the service explicitly:

```bash
export NAVILA_SERVER_SCRIPT=/path/to/vlm_server.py
export NAVVLM_MODEL_PATH=/path/to/navvlm-model
export NAVVLM_PYTHON=/path/to/navila/bin/python
```

`start_navvlm_server.sh` forwards host, port, and model path to that script. The runtime uses `127.0.0.1:54321` by default; override `NAVVLM_PORT` when needed.

## Default deployment package

| Path | Purpose |
| --- | --- |
| [`default_set.json`](NaVILA-Orca/default_set.json) | Go2 and object layout imported into OrcaLab |
| [`demo_episode.json`](NaVILA-Orca/scenes/default_warehouse/demo_episode.json) | Default instruction, start pose, goal, and reference path |
| [`src/navila_orca`](NaVILA-Orca/src/navila_orca) | VLN runtime, camera adapter, scene profile, and result writer |
| [`go2_task`](NaVILA-Orca/src/navila_orca/go2_task) | Local Go2 task definition, MJCF, and meshes |
| [`go2_flat.pt`](NaVILA-Orca/src/navila_orca/assets/checkpoints/go2_flat.pt) | Default Go2 locomotion checkpoint |

```bash
# Inspect the OrcaLab/MJLab runtime and packaged assets.
python -m navila_orca.cli doctor

# Train or replace the local Go2 locomotion policy.
./scripts/train_go2.sh --agent.max-iterations 15001

# Build a clean distributable archive.
./scripts/build_kit.sh
```

## Developer guide

The [developer guide](NaVILA-Orca/docs/GETTING_STARTED.md) covers the default scene, process startup, camera placement, action tracing, model/scene failure modes, and the first set of prompt and camera ablations.

## Contributing

Issues and pull requests are welcome. Keep changes scoped to the VLN runtime, OrcaLab scene integration, Go2 execution path, or reproducible examples. Include the command you ran and the resulting artifact or error when reporting a runtime issue.

## Citation

If this project supports your work, please cite it as:

```bibtex
@software{navila_orcalab,
  title  = {NaVILA--OrcaLab},
  author = {Openverse Orca},
  url    = {https://github.com/openverse-orca/Orca_VLN},
  year   = {2026}
}
```
