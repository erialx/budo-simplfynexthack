#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ORCALAB_PYTHON="${NAVILA_ORCA_PYTHON:-/home/user/anaconda3/envs/orcalab/bin/python}"

exec "${ORCALAB_PYTHON}" "${SCRIPT_DIR}/run_orcalab_pose_smoke.py" "$@"
