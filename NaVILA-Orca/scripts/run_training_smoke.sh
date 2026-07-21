#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_ORCALAB_PYTHON="/home/user/anaconda3/envs/orcalab/bin/python"
DRIVER_PYTHON="${NAVILA_ORCA_DRIVER_PYTHON:-${DEFAULT_ORCALAB_PYTHON}}"

# --python may be supplied again by the caller; argparse uses the final value.
export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
cd -- "${PROJECT_ROOT}"
exec "${DRIVER_PYTHON}" -m navila_orca.training --python "${DRIVER_PYTHON}" "$@"
