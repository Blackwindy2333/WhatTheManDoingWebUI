#!/usr/bin/env bash
# WhatTheManDoing WebUI - config helper (Unix / Git Bash / macOS / Linux)
# Usage: ./config.sh [command...] | ./config.sh  (interactive menu)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="${SCRIPT_DIR}/config_cli.py"

if [[ ! -f "${PY_SCRIPT}" ]]; then
  echo "error: config_cli.py not found next to this script" >&2
  exit 1
fi

# Pick a Python 3 interpreter
PY="${PYTHON:-}"
if [[ -z "${PY}" ]]; then
  for cand in python3 python py; do
    if command -v "${cand}" >/dev/null 2>&1; then
      PY="${cand}"
      break
    fi
  done
fi
if [[ -z "${PY}" ]]; then
  echo "error: Python 3 not found in PATH. Install Python 3.10+ first." >&2
  exit 1
fi

# No args → interactive menu; otherwise pass through to config_cli.py
if [[ $# -eq 0 ]]; then
  exec "${PY}" "${PY_SCRIPT}"
else
  exec "${PY}" "${PY_SCRIPT}" "$@"
fi
