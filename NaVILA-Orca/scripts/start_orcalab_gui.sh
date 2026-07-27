#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/orcalab_env.sh"
navila_orca_require_gui

ORCALAB_BIN="${NAVILA_ORCA_ORCALAB_BIN}"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_ROOT="$(cd "${PROJECT_ROOT}/.." && pwd)"
WORKSPACE="${NAVILA_ORCA_WORKSPACE:-${WORKSPACE_ROOT}/orcalab_workspace}"
PROFILE_WATCHER="${SCRIPT_DIR}/watch_orcalab_scene_profile.sh"

if [[ ! -d "${WORKSPACE}" ]]; then
  mkdir -p "${WORKSPACE}"
fi
if [[ ! -f "${WORKSPACE}/.orcalab/config.toml" ]]; then
  "${ORCALAB_BIN}" "${WORKSPACE}" --init-config
fi

"${ORCALAB_BIN}" "${WORKSPACE}" \
  --scene orcalab_day \
  --layout blank \
  --full-screen \
  --sim-config external \
  --verbose \
  "$@" &
GUI_PID=$!

# A scene switch creates a fresh MuJoCo runtime with OrcaLab's XML defaults.
# Keep the project profile attached to this GUI lifetime, without touching the
# authored 3DGS package or OrcaLab installation.
"${PROFILE_WATCHER}" --parent-pid "${GUI_PID}" &
WATCHER_PID=$!

cleanup() {
  kill "${WATCHER_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

wait "${GUI_PID}"
