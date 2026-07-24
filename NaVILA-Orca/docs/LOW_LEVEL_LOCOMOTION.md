# Low-level locomotion: optional advanced track

The default Go2 checkpoint is part of the baseline. Participants do not need to train locomotion to build a navigation, inspection, or hazard-reporting demo. The recommended default is to keep the provided low-level policy fixed and innovate at the VLN layer.

For teams working on gait quality, terrain response, recovery, or a new robot, [OrcaLocomotion](https://github.com/openverse-orca/OrcaLocomotion) is the default reference project for low-level training in the Orca ecosystem.

## What must remain stable

The high-level runtime talks to locomotion only through `VelocityCommand`:

```text
vx, vy, wz, duration_s  →  low-level policy  →  robot state and pose
```

Your custom policy may have a different observation vector, network, terrain representation, reward, or action space. It must still accept a body-frame velocity target and expose synchronized state at a known `control_dt`. Do not feed language tokens or NaVILA RGB history into the locomotion policy unless the team is explicitly building a new cross-layer research system.

## Recommended workflow

1. Reproduce the packaged flat-ground baseline unchanged.
2. Use OrcaLocomotion as the source of truth for your custom low-level training environment and configuration.
3. Train and validate the new policy in its own low-level environment.
4. Verify stand, forward, turn, stop, and recovery behavior with fixed velocity commands.
5. Export the checkpoint and run it through Orca_VLN with `--checkpoint`.
6. Repeat the unchanged warehouse episode and compare motion chunks, drift, and final trace.

```bash
./scripts/run_orcalab_scene_locomotion.sh \
  --checkpoint /absolute/path/to/custom_go2_policy.pt
```

## What to submit for this track

- training configuration and checkpoint provenance;
- a short description of the velocity-command interface;
- baseline-versus-custom comparison on the same episode;
- a run directory containing RGB frames, `measurements.json`, and the final trace.

This keeps a locomotion innovation comparable to a VLN innovation: both use the same scene and report the same navigation evidence.
