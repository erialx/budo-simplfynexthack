<p align="right"><sub><strong>English</strong> · <a href="README_zh.md">中文</a></sub></p>

# Orca_VLN developer kit

This directory is the distributable OrcaLab runtime. Its GitHub project homepage is one level up: [Orca_VLN](../README.md).

For a first run, open [the getting-started guide](docs/GETTING_STARTED.md).
Sampling rates and exact model interfaces are defined in the
[model and runtime I/O specification](docs/MODEL_IO_SPEC.md). The three
commands below cover the normal development loop:

```bash
./scripts/start_orcalab_gui.sh
./scripts/start_navvlm_server.sh
./scripts/run_orcalab_scene_locomotion.sh
```

From a fresh clone, run `./scripts/setup_all.sh` once and require
`./scripts/doctor.sh` to pass. The launchers use the two project-local
environments directly, so no shell activation or repository-root environment
variable is needed.

Open the default `orcalab_day` map and choose
**File → Open Layout → `default_set.json`** before running navigation. NaVILA
remains in a separate runtime, while the TCP server adapter is owned by this
project and the model is downloaded to the default project model directory.

```bash
./scripts/build_kit.sh
```

The kit contains the OrcaLab adapter, local Go2 task/MJCF/mesh assets, a default Go2 locomotion checkpoint, the global setting, and one reproducible `orcalab_day` episode. It does not include NaVILA/LLaVA source, model weights, 3DGS assets, or IsaacLab.
