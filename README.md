<p align="right"><sub><strong>English</strong> · <a href="README_zh.md">中文</a></sub></p>

<p align="center">
  <img src="NaVILA-Orca/assets/brand/orca-vln-navigation-logo.png" alt="Orca_VLN quadruped robot and navigation path" width="150" align="middle" />
  &nbsp;&nbsp;
  <img src="NaVILA-Orca/assets/brand/orca-platform-logo-blue.png" alt="ORCA Lab by Songying Technology" width="125" align="middle" />
</p>

<h1 align="center">
  <img src="NaVILA-Orca/assets/brand/orca-vln-wordmark.svg" alt="ORCA_VLN" width="340" />
</h1>

<p align="center">
  A visual-language navigation example in OrcaLab.
  <br />
  <a href="#quickstart">🚀 Quickstart</a> ·
  <a href="#remote-inference">🖥️ Remote inference</a> ·
  <a href="#managed-access">☁️ Managed access</a> ·
  <a href="#competition-baseline">🏁 Competition baseline</a> ·
  <a href="NaVILA-Orca/docs/GETTING_STARTED.md">📚 Docs</a>
</p>

<p align="center">
  <img src="NaVILA-Orca/assets/presentation/factory-overview-two-column.png" alt="OrcaLab factory navigation scene with the Go2 robot" width="48%" />
  <img src="NaVILA-Orca/assets/presentation/factory-live-monitor.png" alt="Orca_VLN factory live monitor" width="48%" />
</p>

> **Orca_VLN is a baseline VLN example. Fine-tune it for task-specific requirements.**
> NaVILA maps language and ego RGB to the next navigation action; OrcaLab updates the scene and returns the next visual observation.

```text
instruction + ego RGB  →  NaVILA  →  navigation action  →  OrcaLab  →  next ego RGB
```

The repository provides the OrcaLab side of the example: persistent ego observation, scene lifecycle, a default `VLN_Presentation` factory episode, a runnable control baseline, and traceable run artifacts. NaVILA stays in its own environment and connects over TCP.

## 🧭 Ego Camera ↔ Simulator Views

Left: the observation received by the VLN policy. Right: the corresponding scene-level simulator view.

<p align="center"><sub><strong>Kitchen navigation</strong></sub></p>
<p align="center">
  <img src="NaVILA-Orca/assets/presentation/kitchen-overview.png" alt="Kitchen scene overview" width="48%" />
  <img src="NaVILA-Orca/assets/presentation/kitchen-robot-view.png" alt="Robot in the kitchen scene" width="48%" />
</p>

<p align="center"><sub><strong>Warehouse navigation</strong></sub></p>
<p align="center">
  <img src="NaVILA-Orca/assets/presentation/warehouse-corridor.png" alt="Warehouse corridor scene" width="48%" />
  <img src="NaVILA-Orca/assets/presentation/warehouse-robot-view.png" alt="Robot in the warehouse scene" width="48%" />
</p>

<p align="center"><sub><strong>Storage navigation</strong></sub></p>
<p align="center">
  <img src="NaVILA-Orca/assets/presentation/storage-aisle.png" alt="Storage aisle scene" width="48%" />
  <img src="NaVILA-Orca/assets/presentation/storage-robot-view.png" alt="Robot in the storage scene" width="48%" />
</p>

<a id="quickstart"></a>

## 🚀 Quickstart

**Before starting:** use Ubuntu 22.04/24.04, Git, and an NVIDIA GPU of at
least RTX 4090 class whose driver passes `nvidia-smi`.

### Install once

If `conda --version` does not work, install a clean Miniconda, then clone the
project. Option B additionally needs both blocks run on a second machine —
Git, Conda, an NVIDIA driver, and a checkout at the same revision on both
hosts:

```bash
curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh \
  -o /tmp/miniconda.sh
bash /tmp/miniconda.sh -b -p "$HOME/miniconda3"
source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda init bash
conda --version
```

```bash
git clone https://github.com/openverse-orca/Orca_VLN.git
cd Orca_VLN
```

Then choose exactly one deployment option:

| Option | Deployment layout | Choose this when |
| --- | --- | --- |
| **💻 Option A (default) — Single-host deployment** | OrcaLab and NaVILA run on the same machine | One GPU host has sufficient resources and the shortest setup is preferred |
| **🖥️ Option B — Remote-inference deployment (split hosts)** | OrcaLab runs on the client and NaVILA on a dedicated GPU server | Inference should run independently or GPU memory and compute must be separated |
| **☁️ Option C — Managed remote inference (AWS SSM)** | OrcaLab runs on the participant's machine; NaVILA on a hosted AWS instance managed by the organizers | Hackathon or lab setups where participants use a shared GPU server without owning it |

#### 💻 Option A (default) — Single-host deployment

Create both pinned environments, download the reviewed NaVILA model, and
verify the installation (the final line must be
`Orca_VLN installation is ready.`):

```bash
./NaVILA-Orca/scripts/setup_all.sh
```

On a fresh Ubuntu, setup may request `sudo` once for the Qt/XCB libraries
required by the OrcaLab GUI.

```bash
./NaVILA-Orca/scripts/doctor.sh
```

Both environments live under this checkout in `.conda/envs/` (OrcaLab uses
Python 3.12, NaVILA Python 3.10); the launchers resolve them automatically,
so no `ORCA_VLN_ROOT` or `conda activate` is needed.

#### 🖥️ Option B — Remote-inference deployment (split hosts)

Install only OrcaLab on the client and only NaVILA on the inference server;
do not run `setup_all.sh` on both machines. Follow the independent
[remote-inference deployment chapter](#remote-inference) for per-host setup,
the SSH tunnel, and end-to-end validation. Blackwell RTX 5090 Laptop GPU is
supported.

#### ☁️ Option C — Managed remote inference (AWS SSM)

The NaVILA server is hosted and operated by the organizers; you never install
or touch it. Install only the OrcaLab side on your machine and connect as
described in the [managed access chapter](#managed-access): no SSH, no keys —
an AWS SSM port-forward makes NaVILA look like a local service on
`127.0.0.1:54321`.

### 💻 Option A: run in steps 1 → 2 → 3

A single-host deployment uses three local terminals in this order.

<a id="scene-setup"></a>

#### Step 1 — Open OrcaLab and assemble the preset scene

```bash
./NaVILA-Orca/scripts/start_orcalab_gui.sh
```

Before navigation, in OrcaLab:

1. Subscribe to `VLN_Presentation` and `unitree_robots`.
2. Select `VLN_Presentation`, wait for both subscriptions to finish, then open
   [`NaVILA-Orca/factory.json`](NaVILA-Orca/factory.json) with
   **File → Open Layout**.
3. Confirm that the Go2, red waste bin, blue barrels, red fire extinguisher,
   and white industrial robotic arm are visible.

`VLN_Presentation` provides the factory; `factory.json` adds the authored route.
Keep terminal 1 and OrcaLab running after the layout is visible.

**Already using OrcaLab?** You may skip terminal 1 and use your own open
OrcaLab GUI, provided it is a compatible installation (the baseline is
validated against OrcaLab 26.7.1). Select `VLN_Presentation` and load the same
`factory.json` layout in that GUI instead.

#### Step 2 — Start the NaVILA service locally

```bash
./NaVILA-Orca/scripts/start_navvlm_server.sh
```

Wait until terminal 2 reports that it is listening on `127.0.0.1:54321`.

<a id="run-navigation"></a>

#### Step 3 — Start closed-loop navigation

Run step 3 only after the preset scene is visible in OrcaLab and the service is
listening in step 2. In the OrcaLab GUI, first choose
**Run → Start Simulation → No
Simulation Program → Start** and wait for the external simulation to run.
Terminal 3 connects to that existing session; it does not start OrcaLab or the
simulation itself:

```bash
./NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh
```

Write your navigation input in
[`NaVILA-Orca/prompts/orcalab_scene_locomotion.txt`](NaVILA-Orca/prompts/orcalab_scene_locomotion.txt),
or send it directly with `--instruction`, for example:

```bash
./NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh \
  --instruction "Walk toward the red waste bin and pass close by it without stopping. Continue toward the blue barrels and pass them. Then turn right and follow the open aisle beside the white safety fence toward the red fire extinguisher. Keep outside the fenced work cell and avoid the boxes. When the white industrial robotic arm mounted on a gray pedestal is visible, approach the open floor directly in front of the pedestal. Stop about 1.5 meters away from the arm."
```

<a id="remote-inference"></a>

## 🖥️ Option B — Remote-inference deployment

Option B moves only NaVILA inference to a dedicated GPU server. The OrcaLab
GUI, scene, camera, low-level control, and navigation loop stay on the OrcaLab
client. An SSH local port forward connects the two hosts without exposing the
NaVILA TCP port directly:

```text
OrcaLab client navigation → 127.0.0.1:54321 → SSH tunnel
                         → inference server 127.0.0.1:54321 → NaVILA
```

The complete order is: **install each host → start the remote service → create
the tunnel → run the end-to-end check → prepare the scene and navigate → clean
up**. The developer kit also includes a standalone
[remote-inference deployment guide](NaVILA-Orca/docs/REMOTE_INFERENCE.md).

### Install the client and inference server separately

Both hosts need Git, Conda, an NVIDIA driver that passes `nvidia-smi`, and an
Orca_VLN checkout at the same revision. Do not run `setup_all.sh` on both
machines.

In the **OrcaLab client** checkout, install only the OrcaLab-side dependencies:

```bash
./NaVILA-Orca/scripts/check_nvidia_driver.sh
./NaVILA-Orca/scripts/setup_system_deps.sh
./NaVILA-Orca/scripts/setup_orcalab_env.sh
```

In the **inference server** checkout, install only the NaVILA-side dependencies
and model:

```bash
./NaVILA-Orca/scripts/check_nvidia_driver.sh
./NaVILA-Orca/scripts/setup_navila_env.sh
./NaVILA-Orca/scripts/download_navila_model.sh
```

`doctor.sh` checks for both environments on one machine and therefore applies
only to Option A. The per-component installers above verify their respective
environment and model.

### Inference server: start NaVILA

Run this on the inference server and keep the terminal open:

```bash
ORCA_VLN_DIR="/path/to/Orca_VLN"  # actual checkout path on the inference server
REMOTE_VLM_PORT="54321"

cd "$ORCA_VLN_DIR"
NAVVLM_HOST="127.0.0.1" \
NAVVLM_PORT="$REMOTE_VLM_PORT" \
./NaVILA-Orca/scripts/start_navvlm_server.sh
```

Keep `NAVVLM_HOST="127.0.0.1"`. Do not expose port `54321` in a security group
or firewall. The NaVILA TCP service has no TLS or authentication of its own;
only the SSH port needs to be externally reachable.

### OrcaLab client: create the SSH tunnel

Set the connection values on the client. The IP, account, and ports below are
placeholders:

```bash
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

SSH_HOST="xx.xx.xx.xx"             # actual inference-server IP
SSH_USER="your-ssh-user"          # remote account
SSH_PORT="22"
LOCAL_VLM_PORT="54321"
REMOTE_VLM_PORT="54321"
SSH_CONTROL_SOCKET="${HOME}/.ssh/orca-vln-%C"
```

Use the main command below. It does not force password or key authentication:
OpenSSH negotiates automatically from the client configuration, SSH agent,
and server policy. If earlier methods do not succeed and both client and
server policy permit interactive password authentication, SSH prompts for the
account password. It moves to the background only after authentication and
forwarding succeed.

```bash
ssh -p "$SSH_PORT" \
  -M -S "$SSH_CONTROL_SOCKET" \
  -f -N -T \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -L "127.0.0.1:${LOCAL_VLM_PORT}:127.0.0.1:${REMOTE_VLM_PORT}" \
  "${SSH_USER}@${SSH_HOST}"
```

> **Use the following section only when you have a PEM key that must be
> specified explicitly. If you do not have a PEM key, skip it and use the main
> command above unchanged.**

First set the PEM key path and permissions:

```bash
SSH_KEY_PATH="/path/to/private-key.pem"
chmod 600 "$SSH_KEY_PATH"
```

Only when using this PEM key, insert the two `+` lines between `ssh -p` and
`-M` in the main command; keep the remaining tunnel options unchanged:

```diff
ssh -p "$SSH_PORT" \
+  -i "$SSH_KEY_PATH" \
+  -o IdentitiesOnly=yes \
  -M -S "$SSH_CONTROL_SOCKET" \
```

### OrcaLab client: run the end-to-end tunnel and NaVILA protocol check

Before navigation, send a NaVILA protocol health request through the local
forwarded port:

```bash
./NaVILA-Orca/scripts/check_navvlm_endpoint.py \
  --host 127.0.0.1 \
  --port "$LOCAL_VLM_PORT"
```

The check succeeds only after receiving the matching protocol response from
the remote NaVILA service. It covers the **local port → SSH tunnel → remote
port → NaVILA application layer** without running model inference.
`ssh -O check` and `ss` inspect only the SSH master or local listener and do
not replace this end-to-end check. The server currently handles requests
serially; run the check before navigation, because a timeout during navigation
can also mean that the server is busy with inference.

### OrcaLab client: navigate and clean up

Complete Option A's [Step 1: scene setup](#scene-setup), start the external
simulation in OrcaLab, and then run:

```bash
./NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh \
  --vlm-host 127.0.0.1 \
  --vlm-port "$LOCAL_VLM_PORT"
```

After navigation, close only the SSH master created for this project:

```bash
ssh -p "$SSH_PORT" \
  -S "$SSH_CONTROL_SOCKET" \
  -O exit \
  "${SSH_USER}@${SSH_HOST}"
```

Finally, stop NaVILA with `Ctrl+C` in the inference-server terminal. If the
tunnel reports `Address already in use`, choose a free `LOCAL_VLM_PORT`. If the
end-to-end check fails, confirm that the remote service is still running, both
sides use the same `REMOTE_VLM_PORT`, and the SSH tunnel remains connected.

<a id="managed-access"></a>

## ☁️ Option C — Managed remote inference (AWS SSM)

Option C is for a server hosted and operated by the organizers — a hackathon
or lab deployment where participants reach NaVILA without owning the inference
host. There is no SSH, no public endpoint, and no key distribution. Each
participant authenticates as an IAM Identity Center (SSO) user whose
permissions allow exactly one action: an AWS SSM port-forward to the NaVILA
instance. The inference server then appears as a local service on
`127.0.0.1:54321`:

```text
OrcaLab client navigation → 127.0.0.1:54321 → AWS SSM port-forward
                         → inference server 127.0.0.1:54321 → NaVILA
```

The tunnel carries only the NaVILA protocol and cannot open a shell on the
instance. Follow the dedicated
[managed access guide](NaVILA-Orca/docs/ACCESS_GUIDE.md): it covers the two
required installs (AWS CLI v2 and the Session Manager plugin), grabbing
temporary credentials from the access portal, opening the tunnel, a
standard-library-only health check, and an optional mock inference round trip.
Organizers provision one SSO account per team; the fixed values in the guide
are the current test setup.

After establishing the SSM connection and passing the NaVILA service health
check, complete the following steps.

#### Step 1 — Open OrcaLab and assemble the preset scene

```bash
./NaVILA-Orca/scripts/start_orcalab_gui.sh
```

Do not start navigation yet. In OrcaLab:

1. Subscribe to `VLN_Presentation` and `unitree_robots`.
2. Select `VLN_Presentation`. After both subscriptions finish, use
   **File → Open Layout** to load
   [`NaVILA-Orca/factory.json`](NaVILA-Orca/factory.json).
3. Confirm that Go2, the red waste bin, blue barrels, red fire extinguisher,
   and white industrial robotic arm are all present.

`VLN_Presentation` provides the factory, while `factory.json` loads the
prearranged route. Keep terminal 1 and OrcaLab running after setup.

**Already using OrcaLab?** You may skip terminal 1 and use an existing
compatible OrcaLab GUI (the validated baseline version is OrcaLab 26.7.1).
Select `VLN_Presentation` in that GUI and load the same `factory.json` layout.

#### Step 2 — Start closed-loop navigation

Run step 2 only after OrcaLab displays the complete preset scene, the SSM port
forward is established, and the NaVILA service health check passes. In the
OrcaLab GUI, select **Run → Start Simulation → No Simulation Program → Start**
and wait for the external simulation to begin. Terminal 2 only connects to
this running session; it does not open OrcaLab or start the simulation:

```bash
./NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh
```

Write your navigation instruction to
[`NaVILA-Orca/prompts/orcalab_scene_locomotion.txt`](NaVILA-Orca/prompts/orcalab_scene_locomotion.txt),
or pass it directly with `--instruction`, for example:

```bash
./NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh \
  --instruction "Walk toward the red waste bin and pass close by it without stopping. Continue toward the blue barrels and pass them. Then turn right and follow the open aisle beside the white safety fence toward the red fire extinguisher. Keep outside the fenced work cell and avoid the boxes. When the white industrial robotic arm mounted on a gray pedestal is visible, approach the open floor directly in front of the pedestal. Stop about 1.5 meters away from the arm."
```

<a id="competition-baseline"></a>

## 🏁 Competition baseline

The default episode passes the red waste bin and blue barrels, turns right along the white safety fence toward the red fire extinguisher, and stops in front of the white industrial robotic arm. It is designed to make the full loop visible: instruction, NaVILA response, executed action, ego camera frames, and saved measurements.

| Evaluation dimension | What teams improve | Baseline status |
| --- | --- | --- |
| **High-level VLN** | prompts, mission logic, inspection behavior, SFT/LoRA | NaVILA action loop is ready to run |
| **Low-level control** | command tracking, turning, stopping, stability, recovery | supplied control model is intentionally general, not navigation-tuned |
| **System evidence** | scene setup, camera capture, action trace, reproducibility | run artifacts are saved automatically |

The supplied control model is a conservative flat-ground baseline. It has not been tuned around this factory scene, NaVILA’s discrete motion chunks, or task-specific stopping accuracy. That gap is intentional: low-level execution quality is a competition metric, not a hidden implementation detail.

## 🧩 Extend the baseline

- [Getting started](NaVILA-Orca/docs/GETTING_STARTED.md) — scene setup, processes, camera, and first run.
- [Remote inference](NaVILA-Orca/docs/REMOTE_INFERENCE.md) — split-host deployment over an SSH tunnel.
- [Managed inference access](NaVILA-Orca/docs/ACCESS_GUIDE.md) — reach a hosted NaVILA server through an AWS SSM port-forward, no SSH or keys.
- [Hackathon baseline](NaVILA-Orca/docs/HACKATHON_BASELINE.md) — checkpoints, tracks, evidence, and submission scope.
- [High-level VLN](NaVILA-Orca/docs/VLN_FINE_TUNING.md) — reviewed-data requirements and SFT/LoRA direction.
- [Low-level integration](NaVILA-Orca/docs/LOW_LEVEL_LOCOMOTION.md) — train in OrcaLocomotion, IsaacLab, or another platform; align the model through a stable adapter.
- [Architecture](NaVILA-Orca/docs/ARCHITECTURE.md) — the high-level VLN ↔ low-level locomotion contract.

## 📦 Package

`NaVILA-Orca/` contains the runtime, `factory.json` layout, `VLN_Presentation` episode, robot assets, and baseline checkpoint. Build a clean archive with:

```bash
./scripts/build_kit.sh
```

## 🙌 Acknowledgements

Orca_VLN uses [NaVILA](https://github.com/AnjieCheng/NaVILA) as its high-level vision-language navigation model. If you use NaVILA in your work, please cite:

```bibtex
@inproceedings{cheng2025navila,
  title     = {Navila: Legged robot vision-language-action model for navigation},
  author    = {Cheng, An-Chieh and Ji, Yandong and Yang, Zhaojing and Gongye, Zaitian and Zou, Xueyan and Kautz, Jan and B{\i}y{\i}k, Erdem and Yin, Hongxu and Liu, Sifei and Wang, Xiaolong},
  booktitle = {RSS},
  year      = {2025}
}
```

## 📄 License

Released under the [MIT License](LICENSE).
