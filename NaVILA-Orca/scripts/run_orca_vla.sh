#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ORCALAB_PYTHON="${NAVILA_ORCA_PYTHON:-/home/user/anaconda3/envs/orcalab/bin/python}"
OUTPUT_ARGS=()
if [[ -n "${NAVILA_ORCA_OUTPUT:-}" ]]; then
  OUTPUT_ARGS=(--output "${NAVILA_ORCA_OUTPUT}")
fi

export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
cd -- "${PROJECT_ROOT}"
exec "${ORCALAB_PYTHON}" -m navila_orca.cli run \
  --render-backend grpc-loopback \
  --vlm-backend tcp \
  --vlm-host 127.0.0.1 \
  --vlm-port 54321 \
  --max-decisions 3 \
  --max-control-steps 300 \
  --device cuda:0 \
  "${OUTPUT_ARGS[@]}" \
  "$@"
