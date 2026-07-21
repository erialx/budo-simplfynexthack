# NaVILA local installation

This workspace follows the Isaac simulation path linked by the NaVILA README.

## Installed layout

- `NaVILA/`: `76b98f233dd0fff05dfcd69435eec6740febff9d`
- `NaVILA-Bench/`: `e9d2db12ce5788c0f987d734c0094100b6bc0d3a`
- `IsaacLab/`: fork commit `4d558ec83878c4892a46591c85ba91ac9d3c1834` (`VERSION=1.1.0`)
- `.conda/envs/vlnce-isaac`: Python 3.10, Isaac Sim 4.1.0.0, Torch 2.2.2+cu121
- `.conda/envs/navila`: Python 3.10, Torch 2.3.0+cu121, FlashAttention 2.5.8, NaVILA dependencies
- `models/navila-llama3-8b-8f`: official NaVILA checkpoint
- `NaVILA-Bench/isaaclab_exts/omni.isaac.vlnce/assets`: 90 Matterport USD scenes and 1,077 episodes

NaVILA and Isaac Sim intentionally use separate environments because their pinned
PyTorch versions conflict. GPU 0 is used for simulation and GPU 1 for the VLM by
default; override them with `NAVILA_SIM_GPU` and `NAVILA_VLM_GPU`.

The simulation environment also pins the AWS SDK compatibility set used by Isaac
Sim 4.1's Replicator extensions:

```text
boto3==1.34.68
botocore==1.34.68
s3transfer==0.10.1
```

Do not upgrade these three packages independently. Isaac Sim 4.1 bundles
`botocore==1.34.68`; a newer unpinned `s3transfer` fails while cameras initialize.

## First launch

The NVIDIA Omniverse EULA was accepted interactively on this machine on 2026-07-20.
The acceptance record is stored by Kit at
`.conda/envs/vlnce-isaac/lib/python3.10/site-packages/omni/EULA_ACCEPTED`.

Re-run the finite smoke test with:

```bash
cd /home/user/VLN
CUDA_VISIBLE_DEVICES=0 .conda/envs/vlnce-isaac/bin/python tools/isaac_smoke.py
```

Success prints:

```text
ISAAC_SMOKE_OK steps=20 device=cuda:0
```

This exact test has passed on GPU 0 and exited with status 0.

## Run the benchmark

The PD-planner demo exercises Isaac Sim, Isaac Lab, Matterport rendering, the Go2
robot, cameras, and the bundled low-level policy without the 8B VLM:

```bash
/home/user/VLN/scripts/run_pd_demo.sh
```

Verified episode 0 result (`zsNo4HB9uLZ`):

```text
path_length: 7.463170035760413
distance_to_goal: 0.5613518514153688
success: 1.0
spl: 1.0
oracle_success: 1.0
```

For a full NaVILA episode, start the model server first:

```bash
/home/user/VLN/scripts/start_vlm_server.sh
```

Then, in a second terminal:

```bash
/home/user/VLN/scripts/run_vla_episode.sh
```

To open the live Isaac Sim window instead of running headless, keep the same VLM
server running and use the GUI launcher in the second terminal:

```bash
/home/user/VLN/scripts/run_vla_gui.sh
```

The same GUI entry point can explicitly select the simulator backend:

```bash
# Reuse the running OrcaLab scene (does not launch another OrcaLab instance).
./scripts/run_vla_gui.sh -orca

# Use the original Isaac Sim path. This remains the default without a flag.
./scripts/run_vla_gui.sh -sim
```

The OrcaLab launcher already includes the TCP VLM address
`127.0.0.1:54321`, so its dedicated short command is:

```bash
./scripts/run_orcalab_scene_locomotion.sh
```

As in the original NaVILA evaluator, the launcher sends one continuous route
instruction on every decision: pass the red-orange bin, pass the blue barrel,
then stop in front of the yellow truck. The eight sampled frames cover the full
episode history. Intermediate landmarks do not require `stop` and do not reset
the history; only the final `stop` at the truck ends the episode.

This scene launcher places the 512×512 ego camera just above the Go2 front
head (`base_link` offset `0.30 0.00 0.16`) and follows yaw while rejecting
body roll/pitch, so gait sway does not tilt the VLM image.  After every NaVILA
motion chunk, the terminal and live monitor show `ideal` versus `actual`
distance/yaw plus forward progress and lateral drift.  The same records are
written to `motion_chunks` in the output measurement JSON.

The GUI launcher intentionally omits `--headless` while retaining
`--enable_cameras`. It still writes the measurement JSON and MP4 after the episode.

The model server listens only on `127.0.0.1:54321`. Stop it with `Ctrl+C` after the
episode exits. The verified full episode used GPU 0 for Isaac Sim and GPU 1 for the
8B VLM. It completed after the VLM emitted a stop action and wrote:

- `NaVILA-Bench/eval_results/go2_matterport_vision_loco_2024-09-25_23-22-02/measurements/0.json`
- `NaVILA-Bench/eval_results/go2_matterport_vision_loco_2024-09-25_23-22-02/videos/output_0.mp4`

Verified VLA result:

```text
path_length: 9.45187018195793
distance_to_goal: 1.4154536916873215
success: 1.0
spl: 0.8618019891285728
oracle_navigation_error: 0.1430521335449053
oracle_success: 1.0
```

The generated MP4 was decoded successfully: H.264 container input, 1024x512,
292 frames at 10 FPS (about 2.74 MB).

## OrcaLab / MuJoCo Warp port

The first-stage simulator-independent port lives at `/home/user/VLN/NaVILA-Orca`.
It has passed a finite real Go2 MJWarp + NaVILA VLM + gRPC RGB pipeline and a
real OrcaLab `UpdateLocalEnv` pose-stream smoke. Start with:

```bash
cd /home/user/VLN/NaVILA-Orca
./scripts/run_orca_smoke.sh
./scripts/run_orca_vla.sh        # requires the VLM server on 127.0.0.1:54321
./scripts/start_orcalab_gui.sh   # visible external-simulator GUI
./scripts/run_orcalab_pose_smoke.sh
```

See `NaVILA-Orca/README.md` for the pinned Orca/MJLab versions, results, and
the exact boundary of the current validation. Matterport 3DGS, corresponding
MuJoCo collision geometry, coordinate calibration, and a Go2 ego-camera
WebSocket are not yet available, so Orca results remain `scene_fidelity=false`.

The VLM server has already passed a synthetic eight-frame inference probe. Re-run it
while the server is listening with:

```bash
.conda/envs/navila/bin/python tools/probe_vlm.py
```

## Known packaging metadata warning

`pip check` in the simulation environment passes. In the VLM environment it prints
`decord 0.6.0 is not supported on this platform` because the published Decord 0.6.0
wheel contains a stale CPython 3.6 tag in its internal `WHEEL` metadata. Its Python
interface loads on Python 3.10, and `decord.VideoReader` was verified against the
generated 292-frame episode video. This warning did not affect NaVILA inference.
