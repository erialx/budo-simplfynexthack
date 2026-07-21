#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORCA_PYTHON="${NAVILA_ORCA_PYTHON:-/home/user/anaconda3/envs/orcalab/bin/python}"
export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

if [[ ! -x "${ORCA_PYTHON}" ]]; then
  echo "OrcaLab Python 不存在或不可执行: ${ORCA_PYTHON}" >&2
  exit 2
fi

# This command never launches or republishes OrcaLab. It reuses the current
# layout, requires exactly one complete Go2 in the downloaded combined MJCF,
# and fails before motion if actor/XML/scene-option alignment is incomplete.
exec "${ORCA_PYTHON}" -m navila_orca.cli run \
  --render-backend orcalab \
  --orcagym-address 127.0.0.1:50051 \
  --orcalab-edit-address 127.0.0.1:50151 \
  --camera-actor-name navila_ego \
  --camera-transport grpc-png \
  --camera-mount-position 0.30 0.0 0.16 \
  --stabilize-camera-horizon \
  --no-publish \
  --robot-actor-name auto \
  --anchor-existing-scene \
  --scene-profile mjlab-train \
  --strict-scene-alignment \
  --manual-xml-override \
  --instruction-file "${PROJECT_ROOT}/prompts/orcalab_scene_locomotion.txt" \
  --vlm-backend tcp \
  --vlm-host 127.0.0.1 \
  --vlm-port 54321 \
  --image-interval 0.5 \
  --state-stream-interval 0.04 \
  --live-monitor \
  --monitor-interval 0.1 \
  --max-decisions 0 \
  --max-control-steps 0 \
  --output "${PROJECT_ROOT}/outputs/scene_locomotion_smoke" \
  "$@"
