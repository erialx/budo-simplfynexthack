#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAVVLM_PYTHON="${NAVVLM_PYTHON:-python}"
MODEL_PATH="${NAVVLM_MODEL_PATH:?Set NAVVLM_MODEL_PATH to the NavVLM model directory.}"
export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

exec "${NAVVLM_PYTHON}" -m navila_orca.navvlm_server \
  --host "${NAVVLM_HOST:-127.0.0.1}" \
  --port "${NAVVLM_PORT:-54321}" \
  --model_path "${MODEL_PATH}" \
  "$@"
