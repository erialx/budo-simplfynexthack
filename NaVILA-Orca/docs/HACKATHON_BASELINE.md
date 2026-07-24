# Orca_VLN hackathon baseline

Orca_VLN is the organizer-provided simulation baseline. Teams begin with a working visual-language navigation loop in OrcaLab, then improve the mission behavior without rebuilding the robot stack.

## Baseline package

The organizer provides:

- an OrcaLab industrial-warehouse scene and [`default_set.json`](../default_set.json);
- a Go2, persistent ego RGB camera, and live navigation monitor;
- a NaVILA server integration point and default navigation episode;
- a packaged Go2 locomotion checkpoint and run scripts;
- result artifacts: RGB frames, action trace, trajectory state, and measurements;
- this guide plus the high-level and low-level extension tracks.

The baseline is simulation-first. A real EDU Go2 can be used for selected demonstrations, but hardware access is not required to reproduce or submit the core workflow.

## Four checkpoints

| Checkpoint | Team outcome | Evidence |
| --- | --- | --- |
| 1. Environment | Open scene, import setting, verify Go2 camera | screenshot of warehouse and robot view |
| 2. Autonomous loop | Run instruction → NaVILA → action → motion | `measurements.json` and terminal action trace |
| 3. Inspection logic | Add patrol, hazard, or image-capture behavior | saved images plus structured inspection record |
| 4. Integrated demo | Package one repeatable scenario | short video, source, setup notes, run directory |

## Competition tracks

### Baseline reproduction

Required for every team. Run the default warehouse episode with the supplied scene, Go2 checkpoint, and NaVILA server. This verifies that the simulator, camera, networking, and action interface are correct.

### Mission intelligence

The primary innovation track. Improve route logic, prompt design, staged waypoints, hazard detection, camera capture, inspection reports, or task-specific stop conditions. No model training is required.

### High-level VLN adaptation

Optional. Collect reviewed rollouts and apply SFT or LoRA to NaVILA without changing locomotion. See [VLN fine-tuning](VLN_FINE_TUNING.md).

### Low-level locomotion

Optional advanced track. Replace or improve the Go2 policy while preserving the velocity-command interface. Use [OrcaLocomotion](https://github.com/openverse-orca/OrcaLocomotion) as the default low-level training reference. See [Low-level locomotion](LOW_LEVEL_LOCOMOTION.md).

## Submission checklist

1. Repository or archive with a one-command reproduction path.
2. Scene and prompt/configuration changes clearly documented.
3. A recorded run or screenshots from the robot camera.
4. `measurements.json` and relevant logs from a successful run.
5. A brief statement of which track was changed and what remained baseline.

## Support boundary

Organizers support scene setup, the baseline run path, camera visibility, and the high/low-level interface. Teams own their custom prompts, task logic, data curation, fine-tuning, and custom locomotion policies.
