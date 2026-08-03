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
environments directly, so no shell activation or repository-root environment
variable is needed.

Before running navigation, subscribe to `VLN_Presentation`
(`333f1b37-518d-44ed-ba1c-89b80071074f.pak`) and `unitree_robots` in OrcaLab.
Then select the `VLN_Presentation` scene and choose
**File → Open Layout → `factory.json`**. The default launcher prompt passes the
red bin, turns right to the blue barrel, and stops at the white robotic arm.
NaVILA remains in a separate runtime, while the TCP server adapter is owned by
this project and the model is downloaded to the default project model directory.

```bash
./scripts/build_kit.sh
```

The kit contains the OrcaLab adapter, local Go2 task/MJCF/mesh assets, a default Go2 locomotion checkpoint, the `factory.json` layout, and one reproducible `VLN_Presentation` episode. It does not include NaVILA/LLaVA source, model weights, subscribed OrcaLab assets, or IsaacLab.
