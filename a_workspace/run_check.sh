#!/usr/bin/env bash
# Runs a_workspace/check_real_backend.py with the right interpreter and the
# right PYTHONPATH, so you don't have to get a long multi-line command right in
# Git Bash (which, per CLAUDE.md, likes to eat trailing backslashes).
#
# Usage, from anywhere:
#     bash a_workspace/run_check.sh              # CPU, slow but needs no GPU
#     bash a_workspace/run_check.sh --device cuda   # on the GPU box
#
# Any arguments you pass are forwarded to check_real_backend.py.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "Repo root : $REPO_ROOT"

# --- find the orcalab interpreter ------------------------------------------
# The env does NOT live in this repo. It's in the Orca_VLN checkout.
# Git Bash on Windows sets USERNAME, not USER, so default both rather than
# letting `set -u` kill the script.
WHOAMI="${USERNAME:-${USER:-}}"

CANDIDATES=(
  "${PY:-}"
  "$REPO_ROOT/.conda/envs/orcalab/python.exe"
  "$HOME/Orca_VLN/.conda/envs/orcalab/python.exe"
  "/c/Users/${WHOAMI}/Orca_VLN/.conda/envs/orcalab/python.exe"
  "/c/Users/aadha/Orca_VLN/.conda/envs/orcalab/python.exe"
)

FOUND=""
for candidate in "${CANDIDATES[@]}"; do
  [ -z "$candidate" ] && continue
  if [ -x "$candidate" ] || [ -f "$candidate" ]; then
    FOUND="$candidate"
    break
  fi
done

if [ -z "$FOUND" ]; then
  echo ""
  echo "ERROR: could not find the orcalab env's python.exe."
  echo "Looked in:"
  for candidate in "${CANDIDATES[@]}"; do echo "  $candidate"; done
  echo ""
  echo "Find it yourself with:  ls ~/Orca_VLN/.conda/envs/orcalab/python.exe"
  echo "then re-run with:       PY=/path/to/python.exe bash a_workspace/run_check.sh"
  exit 1
fi

PY="$FOUND"
echo "Interpreter: $PY"
echo ""

# PYTHONIOENCODING: without it the CLI dies instantly on a Unicode arrow that
# the console codepage can't encode (CLAUDE.md, "Windows-specific").
# PYTHONPATH: forces THIS repo's source ahead of the editable install, which
# points at the Orca_VLN checkout and would otherwise be what actually loads.
# PYTHONUNBUFFERED / -u: Git Bash's terminal is a pipe, not a real Windows
# console, so Python block-buffers stdout and the script looks hung for
# minutes while MJLab loads. Force line-by-line output instead.
PYTHONIOENCODING=utf-8 \
PYTHONUNBUFFERED=1 \
PYTHONPATH="$REPO_ROOT/NaVILA-Orca/src" \
"$PY" -u "$REPO_ROOT/a_workspace/check_real_backend.py" "$@"
