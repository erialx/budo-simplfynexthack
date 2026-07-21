# VLN workspace

Private reproducible workspace for NaVILA vision-language navigation across the
Isaac Sim/IsaacLab and OrcaLab/MJWarp simulator paths.

## Repository layout

- `NaVILA/`, `NaVILA-Bench/`, `IsaacLab/`: pinned upstream submodules.
- `NaVILA-Orca/`: simulator-independent navigation and OrcaLab integration.
- `requirements*.txt`: verified dependency locks for the three incompatible
  Python environments.
- `patches/`: local changes layered on pinned upstream source.
- `scripts/`, `tools/`: launchers and finite smoke tests.

## Start here

1. Clone with `git clone --recurse-submodules`.
2. Follow [REPOSITORY.md](REPOSITORY.md) to restore the local upstream patch and
   separately stored model/scene assets.
3. Follow [REQUIREMENTS.md](REQUIREMENTS.md) to create the three environments.
4. Use [README_LOCAL.md](README_LOCAL.md) for the verified launch commands and
   known runtime constraints.

Model weights, Conda environments, Matterport assets, caches, logs, and generated
outputs are intentionally excluded from Git.
