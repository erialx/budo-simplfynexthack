<p align="right"><sub><a href="CONDA_SETUP_zh.md">中文</a> · <strong>English</strong></sub></p>

# Fresh Conda setup

This guide creates a clean host Conda installation that matches the two
project environments used by Orca_VLN. Follow it when `conda` is missing,
Miniconda and Anaconda conflict, or a new computer has not run the project
before.

## 1. The environment contract

Use one Conda distribution only:

- Keep an existing Miniconda **or** Anaconda installation if `conda info` works.
- On a new machine, Miniconda is the smaller recommended option for this project.
- Do not install Miniconda on top of Anaconda or combine both `bin` directories
  in `PATH`.
- Do not install OrcaLab or NaVILA into `base`.

The installer creates two path-based environments inside the checkout:

| Prefix | Python | Purpose |
| --- | --- | --- |
| `.conda/envs/orcalab` | 3.12 | OrcaLab, MJLab, Go2 control and project tests |
| `.conda/envs/navila` | 3.10 | NaVILA inference, PyTorch 2.3 and FlashAttention |

The launchers call these interpreters directly. You do not activate either
environment when running the example.

## 2. Check the host first

Use Ubuntu 22.04 or 24.04 on x86-64. Confirm the architecture and host NVIDIA
driver before installing Python packages:

```bash
uname -m
nvidia-smi
```

Continue only when the architecture is `x86_64` and `nvidia-smi` lists the GPU.
If NVML reports a driver/library mismatch, reboot once; do not delete the
project environments.

## 3. Use an existing Conda installation

Open a new Bash terminal:

```bash
type -a conda
conda --version
conda info --base
```

One intended base directory should be reported, normally `~/miniconda3` or
`~/anaconda3`. If this works, skip to section 5.

If Conda is installed but the command is missing, initialize the installation
you intend to keep:

```bash
~/miniconda3/bin/conda init bash
source ~/.bashrc
```

For an existing Anaconda installation, replace `miniconda3` with `anaconda3`.
If `type -a conda` shows both distributions, start a clean terminal and source
only the selected installation:

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda info --base
```

Do not delete either installation until you have identified which one contains
environments you need.

## 4. Install Miniconda on a new machine

Skip this section if section 3 already succeeded. Use the current Linux
installer from the
[official Miniconda guide](https://www.anaconda.com/docs/getting-started/miniconda/install/linux-install):

```bash
cd /tmp
curl -LO https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
sha256sum Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh
```

Compare the SHA-256 value with the value published in the
[official installer directory](https://repo.anaconda.com/miniconda/) before
running the installer. Accept the default `~/miniconda3` location and answer
`yes` when asked to initialize Conda. Then reopen the terminal, or run:

```bash
source ~/.bashrc
conda info --base
```

Anaconda Distribution is also supported. If you choose it instead, follow the
[official Anaconda Linux installer](https://www.anaconda.com/docs/getting-started/anaconda/install/linux-install)
and do not additionally install Miniconda.

## 5. Verify Conda can create Python environments

Check channel access before cloning the large model:

```bash
conda search python=3.12
```

Current Anaconda channels may display a Terms of Service prompt. Review it and
accept only if appropriate for your use. The official command for an explicit
acceptance is:

```bash
conda tos accept
```

For a company proxy or firewall, ask the administrator to allow the official
Anaconda repositories. Do not work around certificate failures with
`ssl_verify: false`.

## 6. Install Orca_VLN from zero

```bash
git clone https://github.com/openverse-orca/Orca_VLN.git
cd Orca_VLN
```

Create both isolated environments and download the reviewed model:

```bash
./NaVILA-Orca/scripts/setup_all.sh
```

The installer is resumable. If a network download stops, run the same command
again; do not create replacement named environments by hand.

Verify the result independently:

```bash
./NaVILA-Orca/scripts/doctor.sh
```

The final line must be:

```text
Orca_VLN installation is ready.
```

Confirm that the interpreters match the runtime contract:

```bash
.conda/envs/orcalab/bin/python --version
.conda/envs/navila/bin/python --version
```

The first command must report Python 3.12 and the second Python 3.10.

## 7. Run without activation

Do not run `conda activate orcalab` or `conda activate navila`. From the
repository root, use three terminals:

```bash
./NaVILA-Orca/scripts/start_orcalab_gui.sh
```

```bash
./NaVILA-Orca/scripts/start_navvlm_server.sh
```

```bash
./NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh
```

An active `(base)` prompt is harmless: each launcher resolves its project-local
interpreter from its own file path.

## 8. Failure map

| Symptom | Correct action |
| --- | --- |
| `conda: command not found` | Run the selected installation's `conda init bash`, then reopen Bash |
| `type -a conda` shows Miniconda and Anaconda | Select one installation and source only its `etc/profile.d/conda.sh` |
| Conda asks for channel ToS | Review the prompt; if accepted, run `conda tos accept` |
| `HTTP 000 CONNECTION FAILED` | Fix the proxy/firewall with the administrator; keep TLS verification enabled |
| Setup was interrupted | Rerun `setup_all.sh`; it repairs the same project prefixes |
| NVML driver/library mismatch | Reboot once, verify `nvidia-smi`, then rerun setup |
| Doctor reports the wrong Python/package version | Run the corresponding `setup_orcalab_env.sh` or `setup_navila_env.sh` repair command |

Reference: [official `conda init` documentation](https://docs.conda.io/projects/conda/en/stable/commands/init.html).
