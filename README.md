<p align="center">
  <h1 align="center">Orca_VLN</h1>
  <p align="center">
    Closed-loop visual-language navigation for Go2 in OrcaLab.
    <br />
    <a href="#quickstart">Quickstart</a> ·
    <a href="#competition-baseline">Competition baseline</a> ·
    <a href="NaVILA-Orca/docs/GETTING_STARTED.md">Docs</a>
  </p>
</p>

> **Orca_VLN is a baseline solution, you can choose to finetune it based on the specific task requirement.**
> NaVILA decides the next navigation action from language and ego RGB; a Go2 policy executes that action in OrcaLab; the next camera frame closes the loop.

```text
instruction + ego RGB  →  NaVILA  →  velocity command  →  Go2  →  next ego RGB
```

The repository provides the OrcaLab side of the system: a persistent Go2 camera, scene lifecycle, a default warehouse episode, a runnable low-level policy, and traceable run artifacts. NaVILA stays in its own environment and connects over TCP.

## Quickstart

```bash
git clone https://github.com/openverse-orca/Orca_VLN.git
cd Orca_VLN/NaVILA-Orca

# A — OrcaLab
conda activate orcalab
python -m pip install -e '.[orca]'
./scripts/start_orcalab_gui.sh

# B — NaVILA service
conda activate navila
export NAVILA_SERVER_SCRIPT=/path/to/NaVILA-Bench/scripts/vlm_server.py
export NAVVLM_MODEL_PATH=/path/to/navvlm-llama3-8b-8f
./scripts/start_navvlm_server.sh

# C — Orca_VLN
conda activate orcalab
./scripts/run_orcalab_scene_locomotion.sh
```

Open `IndustrialWarehouse1_3dgs` in OrcaLab first, then import [`default_set.json`](NaVILA-Orca/default_set.json). It instantiates the Go2 and the reference objects used by the default episode.

## Competition baseline

The default episode approaches the blue barrel and stops before the yellow vehicle. It is designed to make the full loop visible: instruction, NaVILA response, Go2 motion, ego camera frames, and saved measurements.

| Evaluation dimension | What teams improve | Baseline status |
| --- | --- | --- |
| **High-level VLN** | prompts, mission logic, inspection behavior, SFT/LoRA | NaVILA action loop is ready to run |
| **Low-level locomotion** | command tracking, turning, stopping, stability, recovery | supplied Go2 policy is intentionally general, not navigation-tuned |
| **System evidence** | scene setup, camera capture, action trace, reproducibility | run artifacts are saved automatically |

The supplied `go2_flat.pt` is a conservative flat-ground locomotion baseline. It has not been tuned around this warehouse, NaVILA’s discrete motion chunks, or task-specific stopping accuracy. That gap is intentional: low-level execution quality is a competition metric, not a hidden implementation detail.

## Extend the baseline

- [Getting started](NaVILA-Orca/docs/GETTING_STARTED.md) — scene setup, processes, camera, and first run.
- [Hackathon baseline](NaVILA-Orca/docs/HACKATHON_BASELINE.md) — checkpoints, tracks, evidence, and submission scope.
- [High-level VLN](NaVILA-Orca/docs/VLN_FINE_TUNING.md) — reviewed rollout export plus SFT/LoRA direction.
- [Low-level integration](NaVILA-Orca/docs/LOW_LEVEL_LOCOMOTION.md) — train in OrcaLocomotion, IsaacLab, or another platform; align the model through a stable adapter.
- [Architecture](NaVILA-Orca/docs/ARCHITECTURE.md) — the high-level VLN ↔ low-level locomotion contract.

```bash
# Verify the packaged Go2 model, XML, and version alignment.
./scripts/check_mjlab_alignment.sh

# Export a baseline rollout for high-level data review.
python scripts/export_vln_sft_records.py outputs/warehouse_baseline \
  --output data/vln_review_queue.jsonl
```

## Package

`NaVILA-Orca/` contains the runtime, default global setting, warehouse episode, Go2 MJCF/mesh assets, and baseline checkpoint. Build a clean archive with:

```bash
./scripts/build_kit.sh
```

## Acknowledgements

Orca_VLN uses [NaVILA](https://github.com/AnjieCheng/NaVILA) as its high-level vision-language navigation model. If you use NaVILA in your work, please cite:

```bibtex
@inproceedings{cheng2025navila,
  title     = {Navila: Legged robot vision-language-action model for navigation},
  author    = {Cheng, An-Chieh and Ji, Yandong and Yang, Zhaojing and Gongye, Zaitian and Zou, Xueyan and Kautz, Jan and B{\i}y{\i}k, Erdem and Yin, Hongxu and Liu, Sifei and Wang, Xiaolong},
  booktitle = {RSS},
  year      = {2025}
}
```
