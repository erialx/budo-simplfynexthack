#!/usr/bin/env bash

# Shared OrcaLab runtime resolution. Source this file from launchers; it must
# never select a different Conda distribution behind the user's back.

navila_orca_resolve_runtime() {
  local active_python=""
  local resolved_python="${NAVILA_ORCA_PYTHON:-}"
  local resolved_orcalab="${NAVILA_ORCA_ORCALAB_BIN:-}"

  if [[ -z "${resolved_python}" && -n "${CONDA_PREFIX:-}" ]]; then
    active_python="${CONDA_PREFIX}/bin/python"
    if [[ -x "${active_python}" ]]; then
      resolved_python="${active_python}"
    fi
  fi

  if [[ -z "${resolved_python}" ]]; then
    echo "No OrcaLab Python selected." >&2
    echo "Run 'conda activate orcalab' in this terminal, or set NAVILA_ORCA_PYTHON=/absolute/path/to/orcalab/bin/python." >&2
    return 2
  fi
  if [[ ! -x "${resolved_python}" ]]; then
    echo "OrcaLab Python does not exist or is not executable: ${resolved_python}" >&2
    return 2
  fi

  if [[ -z "${resolved_orcalab}" ]]; then
    resolved_orcalab="$(dirname "${resolved_python}")/orcalab"
  fi

  export NAVILA_ORCA_PYTHON="${resolved_python}"
  export NAVILA_ORCA_ORCALAB_BIN="${resolved_orcalab}"
}

navila_orca_require_gui() {
  navila_orca_resolve_runtime
  if [[ ! -x "${NAVILA_ORCA_ORCALAB_BIN}" ]]; then
    echo "OrcaLab executable does not exist or is not executable: ${NAVILA_ORCA_ORCALAB_BIN}" >&2
    echo "Set NAVILA_ORCA_ORCALAB_BIN to the executable in the same OrcaLab environment." >&2
    return 2
  fi
}
