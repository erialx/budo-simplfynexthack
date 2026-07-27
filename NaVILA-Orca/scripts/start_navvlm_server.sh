#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_ROOT="$(cd "${PROJECT_ROOT}/.." && pwd)"
NAVVLM_PYTHON="${NAVVLM_PYTHON:-python}"
NAVILA_SERVER_SCRIPT="${NAVILA_SERVER_SCRIPT:-${WORKSPACE_ROOT}/NaVILA-Bench/scripts/vlm_server.py}"
MODEL_PATH="${NAVVLM_MODEL_PATH:-${WORKSPACE_ROOT}/models/navila-llama3-8b-8f}"

if [[ ! -f "${NAVILA_SERVER_SCRIPT}" ]]; then
  echo "NaVILA server file does not exist: ${NAVILA_SERVER_SCRIPT}" >&2
  exit 2
fi

if [[ ! -d "${MODEL_PATH}" ]]; then
  echo "NavVLM model directory does not exist: ${MODEL_PATH}" >&2
  exit 2
fi

# NaVILA remains an explicit, separately installed dependency.  This OrcaLab
# teaching project owns the simulator/navigation adapter, not NaVILA's model.
exec "${NAVVLM_PYTHON}" "${NAVILA_SERVER_SCRIPT}" \
  --host "${NAVVLM_HOST:-127.0.0.1}" \
  --port "${NAVVLM_PORT:-54321}" \
  --model_path "${MODEL_PATH}" \
  "$@"
