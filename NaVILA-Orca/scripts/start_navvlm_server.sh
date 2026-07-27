#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_ROOT="$(cd "${PROJECT_ROOT}/.." && pwd)"
DEFAULT_NAVVLM_PYTHON="${WORKSPACE_ROOT}/.conda/envs/navila/bin/python"
if [[ -x "${DEFAULT_NAVVLM_PYTHON}" ]]; then
  NAVVLM_PYTHON="${NAVVLM_PYTHON:-${DEFAULT_NAVVLM_PYTHON}}"
else
  NAVVLM_PYTHON="${NAVVLM_PYTHON:-python}"
fi
NAVILA_SERVER_SCRIPT="${NAVILA_SERVER_SCRIPT:-${PROJECT_ROOT}/scripts/navila_vlm_server.py}"
MODEL_PATH="${NAVVLM_MODEL_PATH:-${WORKSPACE_ROOT}/models/navila-llama3-8b-8f}"

if [[ ! -f "${NAVILA_SERVER_SCRIPT}" ]]; then
  echo "NaVILA server file does not exist: ${NAVILA_SERVER_SCRIPT}" >&2
  exit 2
fi

if [[ ! -d "${MODEL_PATH}" ]]; then
  echo "NavVLM model directory does not exist: ${MODEL_PATH}" >&2
  exit 2
fi

if ! "${NAVVLM_PYTHON}" -c 'import torch, transformers, llava' >/dev/null 2>&1; then
  echo "NaVILA runtime is incomplete in: ${NAVVLM_PYTHON}" >&2
  echo "Expected torch, transformers, and the editable NaVILA llava package." >&2
  exit 2
fi

# NaVILA remains an explicit, separately installed dependency.  This OrcaLab
# teaching project owns the simulator/navigation adapter, not NaVILA's model.
exec "${NAVVLM_PYTHON}" "${NAVILA_SERVER_SCRIPT}" \
  --host "${NAVVLM_HOST:-127.0.0.1}" \
  --port "${NAVVLM_PORT:-54321}" \
  --model_path "${MODEL_PATH}" \
  "$@"
