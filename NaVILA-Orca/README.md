<p align="right"><sub><strong>English</strong> · <a href="README_zh.md">中文</a></sub></p>

# Orca_VLN developer kit

This directory is the distributable OrcaLab runtime. Its GitHub project homepage is one level up: [Orca_VLN](../README.md).

Choose exactly one deployment option:

| Deployment option | Layout | Instructions |
| --- | --- | --- |
| **Option A (default) — single-host deployment** | OrcaLab, NaVILA, and navigation run on one machine | [Getting started](docs/GETTING_STARTED.md#option-a-single-host) |
| **Option B — remote inference** | OrcaLab and navigation run on the client; NaVILA runs on a separate GPU server | [Remote inference deployment](docs/REMOTE_INFERENCE.md) |

## Option A (default) — single-host development loop

From a fresh clone, run `./scripts/setup_all.sh` once and require
`./scripts/doctor.sh` to pass. The launchers use the two project-local
environments directly, so no shell activation or repository-root environment
variable is needed.

The following three commands cover the normal single-host development loop:

```bash
./scripts/start_orcalab_gui.sh
./scripts/start_navvlm_server.sh
./scripts/run_orcalab_scene_locomotion.sh
```

For the complete first-run procedure, follow
[Option A in the getting-started guide](docs/GETTING_STARTED.md#option-a-single-host).

## Option B — remote inference

Option B keeps the OrcaLab GUI and navigation process on the client and runs
only the NaVILA service on a separate GPU server. Do not treat the three
single-host commands above as one client-side sequence. Follow the dedicated
[remote inference guide](docs/REMOTE_INFERENCE.md) for per-machine installation,
service startup, the SSH tunnel, and the end-to-end NaVILA protocol check.

Open the default `orcalab_day` map and choose
**File → Open Layout → `default_set.json`** before running navigation. NaVILA
remains in a separate runtime under both options, while the TCP server adapter
is owned by this project and the model is downloaded to the default project
model directory on the machine that runs inference.

```bash
./scripts/build_kit.sh
```

The kit contains the OrcaLab adapter, local Go2 task/MJCF/mesh assets, a default Go2 locomotion checkpoint, the global setting, and one reproducible `orcalab_day` episode. It does not include NaVILA/LLaVA source, model weights, 3DGS assets, or IsaacLab.
