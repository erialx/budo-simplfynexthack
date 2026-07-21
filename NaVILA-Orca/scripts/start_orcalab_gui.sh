#!/usr/bin/env bash
set -euo pipefail

ORCALAB_BIN="${NAVILA_ORCA_ORCALAB_BIN:-/home/user/anaconda3/envs/orcalab/bin/orcalab}"
WORKSPACE="${NAVILA_ORCA_WORKSPACE:-/home/user/Orca/OrcaLab/DefaultProject}"

exec "${ORCALAB_BIN}" "${WORKSPACE}" \
  --scene orcalab_day \
  --layout blank \
  --full-screen \
  --sim-config external \
  --verbose \
  "$@"
