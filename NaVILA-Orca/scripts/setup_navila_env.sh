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
CONSTRAINTS="${PROJECT_ROOT}/constraints/navila-rss2025.txt"
FLASH_ATTN_WHEEL="https://github.com/Dao-AILab/flash-attention/releases/download/v2.5.8/flash_attn-2.5.8+cu122torch2.3cxx11abiFALSE-cp310-cp310-linux_x86_64.whl#sha256=13454dd3d37cf173649bd389b84614b8072fb283f8f2fd23a65ab66caafc304b"
TRANSFORMERS_REVISION="345b9b1a6a308a1fa6559251eb33ead2211240ac"

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
from importlib import metadata
from pathlib import Path

def require_equal(name, actual, expected):
    if actual != expected:
        raise SystemExit(
            f"{name} version mismatch: found {actual}, expected {expected}. "
            "Run ./NaVILA-Orca/scripts/setup_navila_env.sh to repair it."
        )

source = Path(sys.argv[1]).resolve()
require_equal("Python", sys.version_info[:2], (3, 10))

import flash_attn
import llava
import torch
import torchvision
import transformers
from llava.model.builder import load_pretrained_model

require_equal("torch", torch.__version__.split('+', 1)[0], "2.3.0")
require_equal("torchvision", torchvision.__version__.split('+', 1)[0], "0.18.0")
require_equal("transformers", transformers.__version__, "4.37.2")
require_equal("flash-attn", flash_attn.__version__, "2.5.8")
require_equal("deepspeed", metadata.version("deepspeed"), "0.9.5")
require_equal("accelerate", metadata.version("accelerate"), "0.27.2")
require_equal("numpy", metadata.version("numpy"), "1.26.0")
require_equal("opencv-python", metadata.version("opencv-python"), "4.8.0.74")
require_equal("setuptools", metadata.version("setuptools"), "81.0.0")
if not Path(llava.__file__).resolve().is_relative_to(source):
    raise SystemExit(f"llava resolves outside the reviewed checkout: {llava.__file__}")

replacement = source / "llava/train/transformers_replace/modeling_utils.py"
installed = Path(transformers.__file__).resolve().parent / "modeling_utils.py"
if replacement.read_bytes() != installed.read_bytes():
    raise SystemExit(
        "NaVILA Transformers patch is missing. "
        "Run ./NaVILA-Orca/scripts/setup_navila_env.sh to repair it."
    )
print("NaVILA runtime verified")
print(f"python={sys.executable}")
print(f"torch={torch.__version__}; torchvision={torchvision.__version__}; transformers={transformers.__version__}")
print(f"flash-attn={flash_attn.__version__}; deepspeed={metadata.version('deepspeed')}; llava={Path(llava.__file__).resolve()}")
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

"${PYTHON}" -m pip install --upgrade \
  "pip==26.1.2" "setuptools==81.0.0" "wheel==0.47.0"
"${PYTHON}" -m pip install --index-url https://download.pytorch.org/whl/cu121 \
  'torch==2.3.0' 'torchvision==0.18.0'
"${PYTHON}" -m pip install "${FLASH_ATTN_WHEEL}"
"${PYTHON}" -m pip install \
  --requirement "${CONSTRAINTS}"
"${PYTHON}" -m pip install --no-deps --editable "${NAVILA_SOURCE}"
"${PYTHON}" -m pip install --force-reinstall --no-deps \
  "git+https://github.com/huggingface/transformers@${TRANSFORMERS_REVISION}"

SITE_PACKAGES="$("${PYTHON}" -c 'import site; print(site.getsitepackages()[0])')"
cp -a "${NAVILA_SOURCE}/llava/train/transformers_replace/." "${SITE_PACKAGES}/transformers/"

verify
