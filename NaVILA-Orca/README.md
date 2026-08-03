<p align="right"><sub><strong>English</strong> · <a href="README_zh.md">中文</a></sub></p>

# Orca_VLN developer kit

This directory is the distributable OrcaLab runtime. Its GitHub project homepage is one level up: [Orca_VLN](../README.md).

For a first run, open [the getting-started guide](docs/GETTING_STARTED.md). The three commands below cover the normal development loop:

```bash
./scripts/start_orcalab_gui.sh
./scripts/start_navvlm_server.sh
./scripts/run_orcalab_scene_locomotion.sh
```

From a fresh clone, run `./scripts/setup_all.sh` once and require
`./scripts/doctor.sh` to pass. The launchers use the two project-local
environments directly, so no shell activation or repository-root variable is
needed. Keep low-level training tools such as OrcaLocomotion in a separate
environment and exchange only a compatible policy checkpoint.

Before navigation, subscribe to `VLN_Presentation` and `unitree_robots`, select
`VLN_Presentation`, then choose **File → Open Layout → `factory.json`**. Wait
for both subscriptions to finish before loading the layout. The default route
is red bin → right turn → blue barrel → white robotic arm.

```bash
./scripts/build_kit.sh
```

The kit contains the OrcaLab adapter, local Go2 task/MJCF/mesh assets, a default Go2 locomotion checkpoint, the `factory.json` layout, and one reproducible `VLN_Presentation` episode. It does not include NaVILA/LLaVA source, model weights, subscribed OrcaLab assets, or IsaacLab.
