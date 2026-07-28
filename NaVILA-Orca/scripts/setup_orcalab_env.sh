#!/usr/bin/env bash
set -euo pipefail

# Create the OrcaLab runtime used by this project. It is intentionally a
# prefix environment so it can coexist with the dedicated NaVILA environment.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_ROOT="$(cd "${PROJECT_ROOT}/.." && pwd)"
ENV_PREFIX="${NAVILA_ORCALAB_ENV_PREFIX:-${WORKSPACE_ROOT}/.conda/envs/orcalab}"
PYTHON="${ENV_PREFIX}/bin/python"
CONSTRAINTS="${PROJECT_ROOT}/constraints/orcalab-26.6.3.txt"

usage() {
  cat <<'EOF'
Usage: ./scripts/setup_orcalab_env.sh [--verify]

Creates the tested OrcaLab 26.6.3 / MJLab 1.2.0 environment. Override the
environment location with NAVILA_ORCALAB_ENV_PREFIX if required.
EOF
}

verify() {
  if [[ ! -x "${PYTHON}" ]]; then
    echo "OrcaLab Python does not exist: ${PYTHON}" >&2
    return 2
  fi

  "${PYTHON}" - "${PROJECT_ROOT}" <<'PY'
import sys
from importlib import metadata
from pathlib import Path

def require_equal(name, actual, expected):
    if actual != expected:
        raise SystemExit(
            f"{name} version mismatch: found {actual}, expected {expected}. "
            "Run ./NaVILA-Orca/scripts/setup_orcalab_env.sh to repair it."
        )

project = Path(sys.argv[1]).resolve()
require_equal("Python", sys.version_info[:2], (3, 12))

expected = {
    "setuptools": "81.0.0",
    "orca-lab": "26.6.3",
    "orca-gym": "26.6.3",
    "mjlab": "1.2.0",
    "mujoco-warp": "3.5.0",
    "rsl-rl-lib": "5.0.1",
    "torch": "2.12.0",
    "torchvision": "0.27.0",
}
installed = {name: metadata.version(name) for name in expected}
for name, version in expected.items():
    require_equal(name, installed[name].split("+", 1)[0], version)

import navila_orca
if not Path(navila_orca.__file__).resolve().is_relative_to(project / "src"):
    raise SystemExit(
        f"navila_orca resolves outside this checkout: {navila_orca.__file__}. "
        "Run ./NaVILA-Orca/scripts/setup_orcalab_env.sh to repair it."
    )
print("OrcaLab runtime verified")
print(f"python={sys.executable}")
print("; ".join(f"{name}={installed[name]}" for name in expected))
PY
  "${PYTHON}" -m pip check
}

case "${1:-}" in
  "") ;;
  --verify) verify; exit $? ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

if ! command -v conda >/dev/null 2>&1; then
  echo "Conda is required. Install Miniconda or Anaconda, reopen the terminal, then rerun this command." >&2
  exit 2
fi
if [[ ! -x "${PYTHON}" ]]; then
  conda create --yes --prefix "${ENV_PREFIX}" python=3.12 pip
fi

"${PYTHON}" -m pip install --upgrade \
  "pip==26.0.1" "setuptools==81.0.0" "wheel==0.46.3"
"${PYTHON}" -m pip install \
  --constraint "${CONSTRAINTS}" \
  --editable "${PROJECT_ROOT}[orca,test]"
verify
