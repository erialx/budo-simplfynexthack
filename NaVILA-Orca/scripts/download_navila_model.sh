#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_ROOT="$(cd "${PROJECT_ROOT}/.." && pwd)"
MODEL_DIR="${1:-${WORKSPACE_ROOT}/models/navila-llama3-8b-8f}"
MODEL_REPO="a8cheng/navila-llama3-8b-8f"

mkdir -p "${MODEL_DIR}"

if command -v hf >/dev/null 2>&1; then
  exec hf download "${MODEL_REPO}" --local-dir "${MODEL_DIR}"
fi

if command -v huggingface-cli >/dev/null 2>&1; then
  exec huggingface-cli download "${MODEL_REPO}" --local-dir "${MODEL_DIR}"
fi

echo "Hugging Face CLI is unavailable. Install huggingface_hub in the active orcalab environment." >&2
exit 2
