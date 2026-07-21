# Dependency profiles

This workspace needs three separate Python environments because its simulator and
model stacks pin incompatible Python, PyTorch, CUDA, NumPy, and related packages.
Do not concatenate the lock files or install them into one environment.

| File | Python | Purpose |
| --- | --- | --- |
| `requirements.txt` | 3.10 | NaVILA 8B VLM server |
| `requirements-isaac.txt` | 3.10 | Isaac Sim 4.1 + IsaacLab + NaVILA-Bench |
| `requirements-orcalab.txt` | 3.12 | OrcaLab/MJWarp port |

All three files are full snapshots of the verified environments, including
transitive packages. Workspace projects use relative editable paths; external Git
packages are pinned to commits. No requirement contains a machine-specific
`/home/user/...` path.

## Recreate the environments

Run these commands from the uploaded workspace root, using three fresh
environments:

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-isaac.txt
python -m pip install -r requirements-orcalab.txt
```

In practice, create/activate the matching Python version before each command.
NVIDIA drivers, CUDA runtime compatibility, the Omniverse EULA, OrcaStudio, scene
assets, model checkpoints, and external simulator workspaces are not Python
packages and cannot be restored by pip.

The source environment also contains two ancillary editable packages that have no
portable Git origin: `mink-orca==0.1.0` and `orcalab-pyside==26.5.1`.
Neither is required by the NaVILA-Orca navigation entry points, so they are omitted
instead of recording broken absolute paths. OrcaStudio supplies its own PySide
integration when its GUI is installed.

`pip check` passes in the Isaac and OrcaLab source environments. The VLM source
environment reports only `decord 0.6.0 is not supported on this platform`; this is
the upstream wheel's stale CPython tag, and the verified Python 3.10 runtime can
still import and use Decord.
