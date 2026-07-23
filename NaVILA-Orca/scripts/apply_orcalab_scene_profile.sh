#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORCA_PYTHON="${NAVILA_ORCA_PYTHON:-/home/user/anaconda3/envs/orcalab/bin/python}"
export PYTHONPATH="${PROJECT_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

if [[ ! -x "${ORCA_PYTHON}" ]]; then
  echo "OrcaLab Python 不存在或不可执行: ${ORCA_PYTHON}" >&2
  exit 2
fi

EXPECTED_ORCA_VERSION="26.6.3"
for ORCA_DISTRIBUTION in orca-lab orca-gym; do
  if ! INSTALLED_ORCA_VERSION="$("${ORCA_PYTHON}" -c "from importlib.metadata import version; print(version('${ORCA_DISTRIBUTION}'))" 2>/dev/null)"; then
    echo "未在 ${ORCA_PYTHON} 中找到 ${ORCA_DISTRIBUTION}；需要 ${EXPECTED_ORCA_VERSION}。" >&2
    exit 2
  fi
  if [[ "${INSTALLED_ORCA_VERSION}" != "${EXPECTED_ORCA_VERSION}" ]]; then
    echo "不支持的 ${ORCA_DISTRIBUTION} ${INSTALLED_ORCA_VERSION}；需要 ${EXPECTED_ORCA_VERSION}。" >&2
    exit 2
  fi
done

exec "${ORCA_PYTHON}" -m navila_orca.apply_scene_profile "$@"
