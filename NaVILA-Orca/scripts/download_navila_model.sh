#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_ROOT="$(cd "${PROJECT_ROOT}/.." && pwd)"
MODEL_DIR="${1:-${WORKSPACE_ROOT}/models/navila-llama3-8b-8f}"
MODEL_REPO="a8cheng/navila-llama3-8b-8f"
DEFAULT_NAVVLM_PYTHON="${WORKSPACE_ROOT}/.conda/envs/navila/bin/python"
NAVVLM_PYTHON="${NAVVLM_PYTHON:-${DEFAULT_NAVVLM_PYTHON}}"

mkdir -p "${MODEL_DIR}"

if [[ -x "${NAVVLM_PYTHON}" ]]; then
  exec "${NAVVLM_PYTHON}" -c \
    'from huggingface_hub import snapshot_download; import sys; snapshot_download(repo_id=sys.argv[1], local_dir=sys.argv[2])' \
    "${MODEL_REPO}" "${MODEL_DIR}"
fi

echo "NaVILA Python does not exist: ${NAVVLM_PYTHON}" >&2
echo "Run ./scripts/setup_navila_env.sh before downloading the checkpoint." >&2
exit 2
