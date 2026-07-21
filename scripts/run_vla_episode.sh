#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIM_GPU="${NAVILA_SIM_GPU:-0}"

cd "${ROOT_DIR}/NaVILA-Bench"
export CUDA_VISIBLE_DEVICES="${SIM_GPU}"
exec "${ROOT_DIR}/.conda/envs/vlnce-isaac/bin/python" scripts/navila_eval.py \
    --task=go2_matterport_vision \
    --num_envs=1 \
    --history_length=9 \
    --load_run=2024-09-25_23-22-02 \
    --headless \
    --enable_cameras \
    --device_id=0 \
    --episode_idx=0 \
    --vlm_host=127.0.0.1 \
    --vlm_port=54321
