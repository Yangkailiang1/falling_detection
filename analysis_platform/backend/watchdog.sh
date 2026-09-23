#!/bin/bash
set -u
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -n "${FORMAL_PYTHON_BIN:-}" ] && [ -x "$FORMAL_PYTHON_BIN" ]; then PYTHON_BIN="$FORMAL_PYTHON_BIN"; elif [ -x "$PROJECT_DIR/.venv/bin/python" ]; then PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"; else PYTHON_BIN="$(command -v python3 || command -v python || true)"; fi
if [ -z "${PYTHON_BIN:-}" ]; then echo "未找到 Python 3，请先执行 tools/setup_formal.ps1。" >&2; exit 1; fi
cd "$PROJECT_DIR"
while true; do "$PYTHON_BIN" run.py >> /tmp/analysis_backend.log 2>&1; ec=$?; echo "[$(date +%H:%M:%S)] backend exited code=$ec, restarting in 3s" >> /tmp/analysis_backend.log; sleep 3; done
