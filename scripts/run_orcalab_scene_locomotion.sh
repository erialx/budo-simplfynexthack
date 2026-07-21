#!/usr/bin/env bash
set -euo pipefail

VLN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_SCRIPT="${VLN_ROOT}/NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh"

if [[ ! -x "${PROJECT_SCRIPT}" ]]; then
  echo "OrcaLab scene locomotion launcher not found or not executable: ${PROJECT_SCRIPT}" >&2
  exit 1
fi

exec "${PROJECT_SCRIPT}" "$@"
