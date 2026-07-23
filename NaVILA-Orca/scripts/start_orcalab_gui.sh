#!/usr/bin/env bash
set -euo pipefail

ORCALAB_BIN="${NAVILA_ORCA_ORCALAB_BIN:-/home/user/anaconda3/envs/orcalab/bin/orcalab}"
WORKSPACE="${NAVILA_ORCA_WORKSPACE:-/home/user/Orca/OrcaLab/DefaultProject}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_WATCHER="${SCRIPT_DIR}/watch_orcalab_scene_profile.sh"

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
