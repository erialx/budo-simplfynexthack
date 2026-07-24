# Orca_VLN developer kit

This directory is the distributable OrcaLab runtime. Its GitHub project homepage is one level up: [Orca_VLN](../README.md).

For a first run, open [the getting-started guide](docs/GETTING_STARTED.md). The three commands below cover the normal development loop:

```bash
./scripts/start_orcalab_gui.sh
./scripts/start_navvlm_server.sh
./scripts/run_orcalab_scene_locomotion.sh
```

Import `default_set.json` into an open industrial-warehouse 3DGS scene before running navigation. NaVILA itself remains a separate course-provided service; configure its server file with `NAVILA_SERVER_SCRIPT` and its model directory with `NAVVLM_MODEL_PATH`.

```bash
./scripts/build_kit.sh
```

The kit contains the OrcaLab adapter, local Go2 task/MJCF/mesh assets, a default Go2 locomotion checkpoint, the global setting, and one reproducible warehouse episode. It does not include NaVILA/LLaVA source, model weights, 3DGS assets, or IsaacLab.
