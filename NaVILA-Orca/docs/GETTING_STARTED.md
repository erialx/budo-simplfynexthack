<p align="right"><sub><strong>English</strong> · <a href="GETTING_STARTED_zh.md">中文</a></sub></p>

# Getting started: run a navigation instruction in OrcaLab

The goal is not merely to launch a model. Observe the full robot decision chain: **what it sees, what the language model says, how the quadruped executes, and what changes in the scene**.

Run OrcaLab, the NaVILA server, and the navigation process in three terminals. Keeping the layers separate makes failures easy to isolate.

## 1. Goal and success criteria

The default instruction is: `Move forward toward the blue barrel, then stop before the yellow vehicle.`

A successful run is more than an error-free terminal. It should satisfy all of the following:

- OrcaLab contains the industrial warehouse, one complete Go2, a blue barrel, and a yellow vehicle.
- Images from `mujococamera1080` change as the Go2 moves.
- The NaVILA server receives eight images plus the task text and returns a parseable action.
- Go2 moves stably; `outputs/scene_locomotion_smoke/` contains result JSON and RGB frames after the run.

## 2. Four roles in the system

| Layer | Input | Output | Does not handle |
| --- | --- | --- | --- |
| NaVILA | 8 RGB frames + natural language | textual action | joint control or collision solving |
| Navigation loop | textual action + current state | velocity target and duration | visual-language inference |
| Go2 locomotion | velocity target | 12 joint actions | semantic meaning such as “blue barrel” |
| OrcaLab | Go2 pose | scene RGB and visualization | low-level gait training or solving |

For example, when NaVILA returns `turn left 15 degrees`, the navigation loop converts it to a fixed yaw velocity for 0.5 seconds. The Go2 policy executes continuously at 50 Hz, then the OrcaLab camera returns the next image. This is the high-level VLM / low-level control boundary.

## 3. Prerequisites

### 1. OrcaLab environment

You need Linux, an NVIDIA GPU, OrcaLab/OrcaGym `26.6.3`, MJLab `1.2.0`, `mujoco-warp 3.5.0`, and `rsl-rl-lib 5.x`.

```bash
cd /path/to/NaVILA-Orca
conda activate orcalab
python -m pip install -e '.[orca]'
python -m navila_orca.cli doctor
python -m navila_orca.training
```

In `doctor`, the default task, `default_set.json`, `go2_flat.pt`, and the Go2 XML must all report `exists: true`. Resolve version mismatches before doing scene work.

If OrcaLab is not installed at the default location, set:

```bash
export NAVILA_ORCA_PYTHON=/absolute/path/to/orcalab/bin/python
export NAVILA_ORCA_ORCALAB_BIN=/absolute/path/to/orcalab/bin/orcalab
```

### 2. NaVILA runtime in its compatible environment

NaVILA and its model are explicit external prerequisites. Keep it in its dedicated Python 3.10 / PyTorch 2.3 environment; OrcaLab currently uses Python 3.12 / PyTorch 2.12, and installing NaVILA there would replace incompatible core packages. Orca_VLN provides the small TCP server adapter, so NaVILA-Bench is not required.

The server script must accept:

```text
--host 127.0.0.1  --port 54321  --model_path /path/to/model
```

For the packaged `/home/user/VLN` workspace, use these absolute paths:

```bash
conda activate /home/user/VLN/.conda/envs/navila
export NAVVLM_PYTHON=/home/user/VLN/.conda/envs/navila/bin/python
export NAVILA_SERVER_SCRIPT=/home/user/VLN/NaVILA-Orca/scripts/navila_vlm_server.py
export NAVVLM_MODEL_PATH=/home/user/VLN/models/navila-llama3-8b-8f
```

## 4. First run

### Step A: open the default scene

In terminal A:

```bash
./scripts/start_orcalab_gui.sh
```

In the GUI:

1. Subscribe to/download and open `IndustrialWarehouse1_3dgs`.
2. Import `NaVILA-Orca/default_set.json` through the global-setting importer.
3. Confirm the scene tree contains exactly one complete Go2 actor.
4. Confirm that the blue barrel and yellow vehicle are visible ahead.

`default_set.json` stores only actor layout; it is not the industrial-warehouse 3DGS scene. Importing it without first loading the warehouse does not create a navigable visual environment.

The launcher includes a scene-profile watcher. Whenever a new scene produces MuJoCo XML, it injects the `orca-train` profile (`timestep=0.005`, air resistance disabled) without modifying the OrcaLab installation.

### Step B: start NaVILA

In terminal B:

```bash
conda activate /home/user/VLN/.conda/envs/navila
./scripts/start_navvlm_server.sh
```

Keep this terminal running after it reports listening on `127.0.0.1:54321`. For “server file does not exist”, check `NAVILA_SERVER_SCRIPT`. For model-load failures, ensure `NAVVLM_MODEL_PATH` points to the model root, not a single weight file.

### Step C: run navigation

In terminal C:

```bash
conda activate orcalab
./scripts/run_orcalab_scene_locomotion.sh
```

Important defaults:

| Option | Default behavior | Why it matters |
| --- | --- | --- |
| `--robot-actor-name auto` | requires exactly one complete Go2 in the scene | prevents controlling the wrong actor |
| `--camera-asset-path prefabs/mujococamera1080` | creates once and captures PNG continuously | uses robot ego view, not the viewport |
| `--camera-mount-position 0.35 0 0.48` | mounts the camera in front of and above the base | approximates head view and reduces body occlusion |
| `--warmup-steps 100` | executes 100 zero-velocity policy steps before motion | stabilizes policy state before VLM commands |
| `--scene-profile orca-train` | 200 Hz physics and 50 Hz control | makes action distance reproducible by tick |

## 5. Read the outputs

Results are written to `outputs/scene_locomotion_smoke/`. Every run saves at least:

- RGB frames for checking camera placement and image changes during motion.
- Run JSON containing the instruction, parsed action, timing, and trajectory.
- A scene-alignment file for investigating coordinate or actor issues in the OrcaLab combined XML.

Maintain an experiment table for each run: instruction, first model action, final position, whether it approached the blue barrel, unexpected turns, and screenshot filename. Do not record only “pass/fail”.

## 6. Three progressive tasks

### Task 1: reproduce the baseline

Keep all defaults and run the case twice. Compare action sequences and final trajectories. Discuss whether model inference is deterministic and whether simulation initialization is repeatable.

### Task 2: language ablation

```bash
./scripts/run_orcalab_scene_locomotion.sh \
  --instruction 'Move to the blue barrel and stop.'
```

Then try “turn left first, then approach the blue barrel.” Record whether wording changes the returned action. This is not a language-model trivia test; it asks whether language, images, and geometry jointly influence the decision.

### Task 3: camera ablation

Raise the camera slightly:

```bash
./scripts/run_orcalab_scene_locomotion.sh \
  --camera-mount-position 0.35 0 0.58
```

Compare RGB frames and NaVILA actions. Changing camera position changes the VLM observation, not the physical controller; explain any behavioral change as a change in visual information.

## 7. Integrate a custom Go2 policy (advanced)

The default checkpoint is sufficient to reproduce the VLN baseline. To use a custom low-level policy, choose [OrcaLocomotion](https://github.com/openverse-orca/OrcaLocomotion), IsaacLab, or another training platform; training platform choice is not restricted by this project.

MJLab in Orca_VLN only runs the baseline and writes an alignment report. Custom integration must align Go2 joint order/sign, root pose, action order, control frequency, and the `vx / vy / wz / duration` velocity-command interface. See [Low-level locomotion](LOW_LEVEL_LOCOMOTION.md) for direct loading and adapter paths, and [VLN fine-tuning](VLN_FINE_TUNING.md) for high-level NaVILA SFT/LoRA.

## 8. Common failures: locate the failing layer first

| Symptom | Check first | Common cause |
| --- | --- | --- |
| `Actor does not exist` | OrcaLab scene tree | setting not imported, Go2 deleted, or actor name mismatch |
| zero or multiple Go2 actors | current scene | no complete Go2 or setting imported more than once |
| missing camera properties | `orca-lab` / `orca-gym` versions | not on 26.6.3 or using old `agentcamera` |
| VLM cannot connect | terminal B and port 54321 | NaVILA server is not running or port differs |
| model cannot load | `NAVVLM_MODEL_PATH` | wrong directory or incomplete NaVILA environment |
| Go2 shakes or falls | checkpoint, warmup, scene start pose | incompatible checkpoint, penetration at start, or unstable policy state |

Always debug in this order: scene/actor → camera → NaVILA server → action text → Go2 policy. This avoids misdiagnosing a connectivity error as a navigation-model failure.
