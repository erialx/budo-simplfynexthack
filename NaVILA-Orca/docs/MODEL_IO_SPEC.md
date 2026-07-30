<p align="right"><sub><strong>English</strong> · <a href="MODEL_IO_SPEC_zh.md">中文</a></sub></p>

# Orca_VLN model I/O and sampling specification

## Model

Orca_VLN uses **NaVILA** as its high-level visual-language navigation model.

NaVILA observes the navigation instruction and robot ego images, then predicts
the next navigation action. It does not directly control joints: the textual
action becomes a velocity command executed by the Go2 locomotion policy.

```text
instruction + 8 ego RGB frames
        → NaVILA
        → navigation action
        → VelocityCommand(vx, vy, wz, duration)
        → Go2 locomotion policy
```

## Input

| Input | Specification |
| --- | --- |
| Images | exactly eight time-ordered RGB frames |
| Instruction | one non-empty UTF-8 navigation task |
| Camera | OrcaLab `prefabs/mujococamera1080` |
| Camera mount | `(0.1, 0.0, 0.5) m` in the Go2 base frame |
| Model image size | `384×384` SigLIP input for the current checkpoint |

TCP request:

```json
{
  "images": ["<base64 JPEG 1>", "...", "<base64 JPEG 8>"],
  "query": "<navigation instruction>"
}
```

### Eight-frame selection

- History frames are captured every `0.5` simulated seconds (`2 Hz`).
- Short histories are left-padded with black frames.
- Seven frames are uniformly selected from a longer full history; the eighth
  input is always the newest frame.
- This is full-history uniform sampling, not a latest-eight rolling window.
- History restarts when advancing to a new waypoint.

## Output

| Model output | Executed command |
| --- | --- |
| `move forward 25/50/75 cm` | `vx=0.5 m/s` for `0.5/1.0/1.5 s` |
| `turn left 15/30/45 degrees` | `wz=+π/6 rad/s` for `0.5/1.0/1.5 s` |
| `turn right 15/30/45 degrees` | `wz=-π/6 rad/s` for `0.5/1.0/1.5 s` |
| `stop` | zero velocity and duration; terminate |

One response must contain exactly one action. Empty, unknown, unsupported, or
multiple actions are rejected. The baseline has no lateral action, so `vy=0`.

Generation is deterministic:

```text
do_sample=False
temperature=0
num_beams=1
max_new_tokens=512
```

## Sampling and control rates

| Stage | Period | Rate |
| --- | ---: | ---: |
| MuJoCo physics | `0.005 s` | `200 Hz` |
| Go2 policy | `0.02 s` | `50 Hz` |
| OrcaLab pose and camera-follow update | `0.04 s` | `25 Hz` |
| NaVILA history capture | `0.5 s` | `2 Hz` |
| Live monitor refresh | `0.1 s` | `10 Hz` |

NaVILA is not invoked at a fixed rate. The next inference starts only after the
previous `0.5/1.0/1.5 s` motion chunk finishes. Wall-clock spacing also
includes inference, image transport, and rendering latency.

Go2 performs 100 zero-command policy steps, or two simulated seconds, before
navigation time starts.

## Low-level interface

The high/low-level boundary is:

```text
VelocityCommand(vx, vy, wz, duration_s)
```

The 50 Hz Go2 policy consumes a 47-value observation that includes
`[vx, vy, wz]` and emits twelve joint-position actions in this order:

```text
FL_hip, FL_thigh, FL_calf,
FR_hip, FR_thigh, FR_calf,
RL_hip, RL_thigh, RL_calf,
RR_hip, RR_thigh, RR_calf
```

A replacement policy must preserve the command frame, units, control period,
joint order, and action duration.

## Run artifacts

```text
NaVILA-Orca/outputs/scene_locomotion_smoke/
├── measurements.json
├── scene_alignment.json
└── frames/<run-id>/*.jpg
```

`measurements.json` records raw model output, motion chunks, target-versus-
measured errors, timing, camera configuration, final state, and termination.

At minimum, verify:

1. every request contains eight frames with the newest last;
2. normal history-frame `step_id` increments are 25 at `control_dt=0.02 s`;
3. one new inference follows each completed action chunk;
4. policy, physics, and OrcaLab synchronization run at 50, 200, and 25 Hz;
5. measurements, alignment, and frame artifacts are produced.

## Source

- Model service: [`navila_vlm_server.py`](../scripts/navila_vlm_server.py)
- Frame sampling: [`frames.py`](../src/navila_orca/frames.py)
- Action parser: [`actions.py`](../src/navila_orca/actions.py)
- Closed-loop timing: [`runner.py`](../src/navila_orca/runner.py)
- Runtime defaults: [`run_orcalab_scene_locomotion.sh`](../scripts/run_orcalab_scene_locomotion.sh)
