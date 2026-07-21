#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VLM_GPU="${NAVILA_VLM_GPU:-1}"

cd "${ROOT_DIR}/NaVILA-Bench"
export CUDA_VISIBLE_DEVICES="${VLM_GPU}"
exec "${ROOT_DIR}/.conda/envs/navila/bin/python" scripts/vlm_server.py \
    --model_path "${ROOT_DIR}/models/navila-llama3-8b-8f" \
    --host 127.0.0.1 \
    --port 54321 \
    --device cuda
