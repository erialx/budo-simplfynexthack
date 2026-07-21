#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORCA_PYTHON="${NAVILA_ORCA_PYTHON:-/home/user/anaconda3/envs/orcalab/bin/python}"
export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

# Reuse the one OrcaLab GUI instance that is already running. This script does
# not start another GUI and never republishes/clears the authored layout.
exec "${ORCA_PYTHON}" -m navila_orca.cli run \
  --render-backend orcalab \
  --orcagym-address 127.0.0.1:50051 \
  --orcalab-edit-address 127.0.0.1:50151 \
  --camera-actor-name navila_ego \
  --camera-transport grpc-png \
  --no-publish \
  --robot-actor-name auto \
  --anchor-existing-scene \
  --scene-profile mjlab-train \
  --strict-scene-alignment \
  --manual-xml-override \
  --vlm-backend scripted \
  --scripted-action stop \
  --max-decisions 1 \
  --max-control-steps 2 \
  --output "${PROJECT_ROOT}/outputs/camera_bind_smoke" \
  "$@"
