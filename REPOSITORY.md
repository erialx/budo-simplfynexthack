# Private repository handoff

This repository stores source code and reproducible Python dependency locks. It
does not store disposable environments, download caches, model weights, simulator
assets, logs, or evaluation outputs.

## Clone

```bash
git clone --recurse-submodules <private-repository-url> VLN
cd VLN
git -C NaVILA-Bench apply ../patches/navila-bench-vlm-server.patch
```

The submodules are pinned by the parent repository to the verified revisions
listed in `README_LOCAL.md`. The patch restores the local VLM server changes that
are not present in the upstream NaVILA-Bench revision.

## Python environments

Follow `REQUIREMENTS.md`. The VLM, Isaac Sim, and OrcaLab profiles must remain
separate.

## Files restored outside Git

These large files are intentionally excluded and must be downloaded or copied to
the same paths before running the full benchmark:

- `models/navila-llama3-8b-8f/`: official NaVILA model checkpoint.
- `NaVILA-Bench/isaaclab_exts/omni.isaac.vlnce/assets/`: Matterport scenes and
  episodes.
- NVIDIA/Omniverse runtime data created inside the Python environment.
- OrcaLab workspace and external simulator assets.

The local `NaVILA-Orca/components/` entries were machine-specific symlinks.
Their source repositories and verified revisions are documented in
`NaVILA-Orca/README.md` and locked as Git requirements where they are Python
dependencies. Recreate those component checkouts locally when using the OrcaLab
backend.

## Upload policy

Do not force-add ignored model or environment files. GitHub rejects ordinary Git
objects larger than 100 MB, while this workspace contains individual files close
to 5 GB. Use a model/artifact registry or separately configured Git LFS storage if
those binaries must be shared.

