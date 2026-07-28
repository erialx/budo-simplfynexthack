<p align="right"><sub><strong>English</strong> · <a href="README_zh.md">中文</a></sub></p>

<p align="center">
  <img src="NaVILA-Orca/assets/brand/orca-vln-navigation-logo.png" alt="Orca_VLN quadruped robot and navigation path" width="150" align="middle" />
  &nbsp;&nbsp;
  <img src="NaVILA-Orca/assets/brand/orca-platform-logo-blue.png" alt="ORCA Lab by Songying Technology" width="125" align="middle" />
</p>

<h1 align="center">
  <img src="NaVILA-Orca/assets/brand/orca-vln-wordmark.svg" alt="ORCA_VLN" width="340" />
</h1>

<p align="center">
  A visual-language navigation example in OrcaLab.
  <br />
  <a href="#quickstart">🚀 Quickstart</a> ·
  <a href="#competition-baseline">🏁 Competition baseline</a> ·
  <a href="NaVILA-Orca/docs/GETTING_STARTED.md">📚 Docs</a>
</p>

<p align="center">
  <img src="NaVILA-Orca/assets/presentation/warehouse-overview.png" alt="OrcaLab warehouse navigation scene" width="48%" />
  <img src="NaVILA-Orca/assets/presentation/live-monitor.png" alt="Orca_VLN live monitor" width="48%" />
</p>

> **Orca_VLN is a baseline VLN example. Fine-tune it for task-specific requirements.**
> NaVILA maps language and ego RGB to the next navigation action; OrcaLab updates the scene and returns the next visual observation.

```text
instruction + ego RGB  →  NaVILA  →  navigation action  →  OrcaLab  →  next ego RGB
```

The repository provides the OrcaLab side of the example: persistent ego observation, scene lifecycle, a default `orcalab_day` episode, a runnable control baseline, and traceable run artifacts. NaVILA stays in its own environment and connects over TCP.

## 🧭 Ego Camera ↔ Simulator Views

Left: the observation received by the VLN policy. Right: the corresponding scene-level simulator view.

<p align="center"><sub><strong>Kitchen navigation</strong></sub></p>
<p align="center">
  <img src="NaVILA-Orca/assets/presentation/kitchen-overview.png" alt="Kitchen scene overview" width="48%" />
  <img src="NaVILA-Orca/assets/presentation/kitchen-robot-view.png" alt="Robot in the kitchen scene" width="48%" />
</p>

<p align="center"><sub><strong>Warehouse navigation</strong></sub></p>
<p align="center">
  <img src="NaVILA-Orca/assets/presentation/warehouse-corridor.png" alt="Warehouse corridor scene" width="48%" />
  <img src="NaVILA-Orca/assets/presentation/warehouse-robot-view.png" alt="Robot in the warehouse scene" width="48%" />
</p>

<p align="center"><sub><strong>Storage navigation</strong></sub></p>
<p align="center">
  <img src="NaVILA-Orca/assets/presentation/storage-aisle.png" alt="Storage aisle scene" width="48%" />
  <img src="NaVILA-Orca/assets/presentation/storage-robot-view.png" alt="Robot in the storage scene" width="48%" />
</p>

<a id="quickstart"></a>

## 🚀 Quickstart

**Before starting:** use Ubuntu 22.04/24.04, Git, and an NVIDIA GPU of at
least RTX 4090 class whose driver passes `nvidia-smi`.

### Install once

If `conda --version` does not work, install one clean Miniconda:

```bash
curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh \
  -o /tmp/miniconda.sh
bash /tmp/miniconda.sh -b -p "$HOME/miniconda3"
source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda init bash
conda --version
```

Clone the project:

```bash
git clone https://github.com/openverse-orca/Orca_VLN.git
cd Orca_VLN
```

Create both pinned environments and download the reviewed NaVILA model:

```bash
./NaVILA-Orca/scripts/setup_all.sh
```

On a fresh Ubuntu installation, setup may request `sudo` once to install the
Qt/XCB libraries required by the OrcaLab GUI.
It also prepares OrcaLab's verified native viewport and scene pak before the
first GUI launch, so OrcaLab does not install components and request a restart.

Verify the completed installation. The final line must be
`Orca_VLN installation is ready.`:

```bash
./NaVILA-Orca/scripts/doctor.sh
```

The environments live under this checkout in `.conda/envs/`: OrcaLab uses
Python 3.12 and NaVILA uses Python 3.10. The launchers resolve them from their
own file paths; no `ORCA_VLN_ROOT`, `conda activate`, or manual `deactivate`
step is required.

### Run in order in three terminals

#### A — Open OrcaLab and assemble the preset scene

```bash
./NaVILA-Orca/scripts/start_orcalab_gui.sh
```

Do not start navigation yet. In OrcaLab:

1. Open the default map `orcalab_day`.
2. Choose **File → Open Layout** and select
   [`NaVILA-Orca/default_set.json`](NaVILA-Orca/default_set.json).
3. Wait until the Go2, blue barrel, yellow vehicle, and other preset objects
   appear in the scene.

> **Asset subscription — required for the default case.** In OrcaLab, subscribe
> to `SimpleMovement_Conveybelt`, `SimpleMovement_Slope`, `unitree_robots`, and
> `OrcaPlaygroundAssets` before loading `default_set.json`. These supply the
> layout's boxes, bins/barrel, Go2, and vehicle. Subscribe to
> `IndustrialWarehouse1_3dgs`, `IndustrialWarehouse2_3dgs`, `kitchen_3dgs`, or
> `AutoProductionLine_Warehouse` only when you choose to build or evaluate in
> those additional scenes.

The map alone does not contain the preset task. `default_set.json` is the
layout that instantiates it, and terminal A must remain open.

**Already using OrcaLab?** You may skip terminal A and use your own open
OrcaLab GUI, provided it is a compatible installation (the baseline is
validated against OrcaLab 26.6.3). Open `orcalab_day` and the same layout in
that GUI instead.

#### B — Start the NaVILA service

```bash
./NaVILA-Orca/scripts/start_navvlm_server.sh
```

Wait until terminal B reports that it is listening on `127.0.0.1:54321`.

#### C — Start closed-loop navigation

Run C only after the preset scene is visible in OrcaLab and the service is
listening in B. In the OrcaLab GUI, first choose **Run → Start Simulation → No
Simulation Program → Start** and wait for the external simulation to run.
Terminal C connects to that existing session; it does not start OrcaLab or the
simulation itself:

```bash
./NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh
```

<a id="competition-baseline"></a>

## 🏁 Competition baseline

The default episode approaches the blue barrel and stops before the yellow vehicle. It is designed to make the full loop visible: instruction, NaVILA response, executed action, ego camera frames, and saved measurements.

| Evaluation dimension | What teams improve | Baseline status |
| --- | --- | --- |
| **High-level VLN** | prompts, mission logic, inspection behavior, SFT/LoRA | NaVILA action loop is ready to run |
| **Low-level control** | command tracking, turning, stopping, stability, recovery | supplied control model is intentionally general, not navigation-tuned |
| **System evidence** | scene setup, camera capture, action trace, reproducibility | run artifacts are saved automatically |

The supplied control model is a conservative flat-ground baseline. It has not been tuned around this warehouse, NaVILA’s discrete motion chunks, or task-specific stopping accuracy. That gap is intentional: low-level execution quality is a competition metric, not a hidden implementation detail.

## 🧩 Extend the baseline

- [Getting started](NaVILA-Orca/docs/GETTING_STARTED.md) — scene setup, processes, camera, and first run.
- [Hackathon baseline](NaVILA-Orca/docs/HACKATHON_BASELINE.md) — checkpoints, tracks, evidence, and submission scope.
- [High-level VLN](NaVILA-Orca/docs/VLN_FINE_TUNING.md) — reviewed-data requirements and SFT/LoRA direction.
- [Low-level integration](NaVILA-Orca/docs/LOW_LEVEL_LOCOMOTION.md) — train in OrcaLocomotion, IsaacLab, or another platform; align the model through a stable adapter.
- [Architecture](NaVILA-Orca/docs/ARCHITECTURE.md) — the high-level VLN ↔ low-level locomotion contract.

## 📦 Package

`NaVILA-Orca/` contains the runtime, default global setting, `orcalab_day` episode, robot assets, and baseline checkpoint. Build a clean archive with:

```bash
./scripts/build_kit.sh
```

## 🙌 Acknowledgements

Orca_VLN uses [NaVILA](https://github.com/AnjieCheng/NaVILA) as its high-level vision-language navigation model. If you use NaVILA in your work, please cite:

```bibtex
@inproceedings{cheng2025navila,
  title     = {Navila: Legged robot vision-language-action model for navigation},
  author    = {Cheng, An-Chieh and Ji, Yandong and Yang, Zhaojing and Gongye, Zaitian and Zou, Xueyan and Kautz, Jan and B{\i}y{\i}k, Erdem and Yin, Hongxu and Liu, Sifei and Wang, Xiaolong},
  booktitle = {RSS},
  year      = {2025}
}
```

## 📄 License

Released under the [MIT License](LICENSE).
