#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORCA_PYTHON="${NAVILA_ORCA_PYTHON:-/home/user/anaconda3/envs/orcalab/bin/python}"
export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

exec "${ORCA_PYTHON}" -m navila_orca.go2_train Unitree-Go2-Flat "$@"
