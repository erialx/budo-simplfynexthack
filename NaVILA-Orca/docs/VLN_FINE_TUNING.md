<p align="right"><sub><strong>English</strong> · <a href="VLN_FINE_TUNING_zh.md">中文</a></sub></p>

# High-level VLN adaptation: SFT and LoRA

This track changes the decision model, not the walking controller. Keep the Go2 locomotion checkpoint fixed while collecting and evaluating high-level navigation data. The stable interface is the canonical action vocabulary described in [Architecture](ARCHITECTURE.md).

## Start with a baseline, not training

Run the default warehouse case first. Save the result directory, inspect the RGB frames, and review the action sequence. Baseline reproduction gives every team a known camera convention, scene layout, command vocabulary, and output format before any model changes are made.

```bash
./scripts/run_orcalab_scene_locomotion.sh \
  --output outputs/warehouse_baseline
```

## Collect reviewable examples

The package includes a small exporter for high-level records:

```bash
python scripts/export_vln_sft_records.py \
  outputs/warehouse_baseline \
  --output data/vln_review_queue.jsonl
```

This produces a model-agnostic record containing the instruction, saved image paths, baseline actions, scene metadata, and an `unreviewed` marker. It is a review queue, **not ground truth**. Inspect image/action alignment and attach a human label before using it for training:

```bash
python scripts/export_vln_sft_records.py \
  outputs/warehouse_baseline \
  --output data/vln_reviewed.jsonl \
  --label "turn left 15 degrees"
```

Each record has this shape:

```json
{
  "instruction": "Move to the blue barrel and stop.",
  "image_paths": ["/abs/path/000_step_000000.jpg"],
  "baseline_actions": ["move forward 25 cm", "stop"],
  "target_action": "turn left 15 degrees",
  "review_status": "reviewed"
}
```

Adapt this record to the exact dataset template required by the NaVILA training release. Do not assume the JSONL above is a drop-in replacement for any specific trainer.

## SFT direction

Use SFT when the desired improvement is mostly semantic or task-specific:

- warehouse terminology: pallets, cones, exit signs, inspection points;
- a clearer stop policy near a target or hazard;
- staged routes such as “reach shelf A, then inspect the aisle”;
- action wording that remains inside the supported command vocabulary.

A practical first dataset is small and curated: collect a fixed set of scene states, write one unambiguous instruction for each, review the expected next action, then hold out different camera positions and object placements for evaluation. Keep the assistant target to one action; the runtime already turns that action into a fixed motion chunk.

## LoRA direction

Use LoRA when a full NaVILA fine-tune is unnecessary. In the NaVILA training environment:

1. Start from the provided NaVILA checkpoint.
2. Freeze the base model and train adapters on the reviewed navigation records.
3. Keep the image-history length and prompt format aligned with deployment.
4. Merge or load the adapter through the NaVILA server used by `NAVILA_SERVER_SCRIPT`.
5. Re-run the same fixed Orca_VLN episodes before changing scene assets.

The exact target modules, image processor, and launch command are determined by the NaVILA release used by the organizer. The competition baseline intentionally does not hard-code them.

## Evaluation checklist

Report more than whether the robot eventually moves:

- valid-action rate: output parses into exactly one permitted action;
- instruction adherence: correct turn/forward/stop decision for a reviewed state;
- visual grounding: behavior changes when the target object or camera view changes;
- closed-loop outcome: final distance to goal, path trace, and saved RGB evidence;
- regression: baseline warehouse runs still work with the adapted model.

If the model proposes an invalid action, fix the high-level data, prompt, or model output—not the Go2 policy.
