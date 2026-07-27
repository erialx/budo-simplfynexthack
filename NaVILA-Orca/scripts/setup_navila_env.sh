#!/usr/bin/env bash
set -euo pipefail

# Create the inference environment used by the local NaVILA TCP service.
# This is deliberately separate from OrcaLab: NaVILA requires CPython 3.10
# and PyTorch 2.3, while the current OrcaLab distribution uses newer core
# packages that must not be replaced.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_ROOT="$(cd "${PROJECT_ROOT}/.." && pwd)"
ENV_PREFIX="${NAVILA_ENV_PREFIX:-${WORKSPACE_ROOT}/.conda/envs/navila}"
NAVILA_SOURCE="${NAVILA_SOURCE:-${WORKSPACE_ROOT}/NaVILA}"
NAVILA_REVISION="${NAVILA_REVISION:-76b98f233dd0fff05dfcd69435eec6740febff9d}"
PYTHON="${ENV_PREFIX}/bin/python"
FLASH_ATTN_WHEEL="https://github.com/Dao-AILab/flash-attention/releases/download/v2.5.8/flash_attn-2.5.8+cu122torch2.3cxx11abiFALSE-cp310-cp310-linux_x86_64.whl"

usage() {
  cat <<'EOF'
Usage: ./scripts/setup_navila_env.sh [--verify]

Creates or repairs the dedicated NaVILA inference environment. Override paths
with NAVILA_ENV_PREFIX and NAVILA_SOURCE, or select a reviewed source revision
with NAVILA_REVISION, when the workspace layout differs.
EOF
}

verify() {
  if [[ ! -x "${PYTHON}" ]]; then
    echo "NaVILA Python does not exist: ${PYTHON}" >&2
    return 2
  fi
  if [[ ! -d "${NAVILA_SOURCE}/llava" ]]; then
    echo "NaVILA source does not exist: ${NAVILA_SOURCE}" >&2
    return 2
  fi
  if [[ "$(git -C "${NAVILA_SOURCE}" rev-parse HEAD)" != "${NAVILA_REVISION}" ]]; then
    echo "NaVILA source is not the reviewed revision: ${NAVILA_REVISION}" >&2
    return 2
  fi

  "${PYTHON}" - "${NAVILA_SOURCE}" <<'PY'
import sys
from pathlib import Path

source = Path(sys.argv[1]).resolve()
assert sys.version_info[:2] == (3, 10), sys.version

import flash_attn
import llava
import torch
import torchvision
import transformers

assert torch.__version__.split('+', 1)[0] == "2.3.0", torch.__version__
assert torchvision.__version__.split('+', 1)[0] == "0.18.0", torchvision.__version__
assert transformers.__version__ == "4.37.2", transformers.__version__
assert flash_attn.__version__ == "2.5.8", flash_attn.__version__
assert Path(llava.__file__).resolve().is_relative_to(source), llava.__file__

replacement = source / "llava/train/transformers_replace/modeling_utils.py"
installed = Path(transformers.__file__).resolve().parent / "modeling_utils.py"
assert replacement.read_bytes() == installed.read_bytes(), "NaVILA Transformers patch is missing"
print("NaVILA runtime verified")
print(f"python={sys.executable}")
print(f"torch={torch.__version__}; torchvision={torchvision.__version__}; transformers={transformers.__version__}")
print(f"flash-attn={flash_attn.__version__}; llava={Path(llava.__file__).resolve()}")
PY
}

case "${1:-}" in
  "") ;;
  --verify) verify; exit $? ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

if ! command -v conda >/dev/null 2>&1; then
  echo "conda is required to create ${ENV_PREFIX}." >&2
  exit 2
fi

if [[ ! -d "${NAVILA_SOURCE}/.git" ]]; then
  if [[ -e "${NAVILA_SOURCE}" ]]; then
    echo "NaVILA source is not a Git checkout: ${NAVILA_SOURCE}" >&2
    exit 2
  fi
  git clone https://github.com/AnjieCheng/NaVILA.git "${NAVILA_SOURCE}"
fi

CURRENT_REVISION="$(git -C "${NAVILA_SOURCE}" rev-parse HEAD)"
if [[ "${CURRENT_REVISION}" != "${NAVILA_REVISION}" ]]; then
  if [[ -n "$(git -C "${NAVILA_SOURCE}" status --porcelain)" ]]; then
    echo "NaVILA source has local changes and is not the reviewed revision ${NAVILA_REVISION}." >&2
    echo "Use a clean checkout, or explicitly set NAVILA_REVISION to the source revision you reviewed." >&2
    exit 2
  fi
  git -C "${NAVILA_SOURCE}" checkout --detach "${NAVILA_REVISION}"
fi

if [[ ! -x "${PYTHON}" ]]; then
  conda create --yes --prefix "${ENV_PREFIX}" python=3.10 pip
fi

"${PYTHON}" -m pip install --upgrade pip
"${PYTHON}" -m pip install --index-url https://download.pytorch.org/whl/cu121 \
  'torch==2.3.0' 'torchvision==0.18.0'
"${PYTHON}" -m pip install "${FLASH_ATTN_WHEEL}"
"${PYTHON}" -m pip install --editable "${NAVILA_SOURCE}"
"${PYTHON}" -m pip install --force-reinstall \
  'git+https://github.com/huggingface/transformers@v4.37.2'

SITE_PACKAGES="$("${PYTHON}" -c 'import site; print(site.getsitepackages()[0])')"
cp -a "${NAVILA_SOURCE}/llava/train/transformers_replace/." "${SITE_PACKAGES}/transformers/"

verify
