#!/bin/bash
set -e
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${FORMAL_PYTHON_BIN:-$PROJECT_DIR/backend/.venv/bin/python}"
NODE_BIN="${FORMAL_NODE_BIN:-$(command -v node || true)}"
if [ ! -x "$PYTHON_BIN" ]; then PYTHON_BIN="$(command -v python3 || command -v python || true)"; fi
if [ -z "${PYTHON_BIN:-}" ]; then echo "未找到 Python，请先执行 tools/setup_formal.ps1。"; exit 1; fi
echo "[1/3] 启动正式 Flask 后端 5001..."
cd "$PROJECT_DIR/backend"; nohup "$PYTHON_BIN" run.py > /tmp/analysis_backend.log 2>&1 &
for i in $(seq 1 40); do curl -sf http://127.0.0.1:5001/api/health >/dev/null 2>&1 && break; sleep 2; done
echo "[2/3] 启动正式管理端 5173..."
cd "$PROJECT_DIR/frontend"; npm ci; nohup npm run dev -- --host 127.0.0.1 > /tmp/formal_frontend.log 2>&1 &
for i in $(seq 1 30); do curl -sf http://127.0.0.1:5173/ >/dev/null 2>&1 && break; sleep 1; done
echo "[3/3] 前端已就绪，启动无头语音服务..."
if [ -z "${NODE_BIN:-}" ]; then echo "未找到 Node.js，请先执行 tools/setup_formal.ps1。"; exit 1; fi
cd "$PROJECT_DIR/headless_voice"; npm ci; nohup "$NODE_BIN" headless_voice.js > /tmp/headless_voice.log 2>&1 &
echo "正式平台已启动：5001、5173，语音服务最后启动。"
