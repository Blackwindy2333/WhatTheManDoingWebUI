#!/usr/bin/env bash
# WhatTheManDoing WebUI - 配置管理脚本（Unix / Git Bash / macOS / Linux）
# 用法: ./config.sh [子命令...] | ./config.sh  （不带参数进入交互菜单）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="${SCRIPT_DIR}/config_cli.py"

if [[ ! -f "${PY_SCRIPT}" ]]; then
  echo "错误：未找到与本脚本同目录的 config_cli.py" >&2
  exit 1
fi

# 选择 Python 3 解释器
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
  echo "错误：PATH 中未找到 Python，请先安装 Python 3.10 及以上版本。" >&2
  exit 1
fi

# 无参数 → 交互菜单；有参数 → 透传给 config_cli.py
if [[ $# -eq 0 ]]; then
  exec "${PY}" "${PY_SCRIPT}"
else
  exec "${PY}" "${PY_SCRIPT}" "$@"
fi
