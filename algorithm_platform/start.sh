#!/bin/bash
# ============================================
# 算法迭代平台 - 一键启动脚本
# 功能: 启动 Flask 后端(:5003) + Vue3 前端(:5174)
# ============================================

set -e

# nvm Node.js（Vite 6.x 需要 Node 18+）
export NVM_DIR="$HOME/.nvm"
NODE_BIN=$(ls -d "$NVM_DIR/versions/node/v2"*"/bin" 2>/dev/null | sort -V | tail -1)
if [ -n "$NODE_BIN" ]; then
    export PATH="$NODE_BIN:$PATH"
fi

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=========================================="
echo "  算法迭代平台 - 启动中..."
echo "=========================================="

# 依赖安装检查
if [ ! -d "$PROJECT_DIR/backend/data" ]; then
    mkdir -p "$PROJECT_DIR/backend/data"
fi

# --- 后端启动 ---
echo ""
echo "[1/2] 启动 Flask 后端 (:5003)..."
PYTHON_BIN="python3"
# 用绝对路径启动（Python 会把脚本目录加入 sys.path，from app import 可用），便于 stop.sh 匹配
$PYTHON_BIN "$PROJECT_DIR/backend/run.py" > "$PROJECT_DIR/backend/server.log" 2>&1 &
BACKEND_PID=$!
echo "  后端 PID: $BACKEND_PID"

# 等待后端就绪
for i in $(seq 1 20); do
    if curl -sf http://localhost:5003/api/health > /dev/null 2>&1; then
        echo "  后端已就绪 ✓"
        break
    fi
    sleep 0.5
done

# --- 前端启动 ---
echo ""
echo "[2/2] 启动 Vue3 前端 (:5174)..."
cd "$PROJECT_DIR/frontend"
if [ ! -d node_modules ]; then
    echo "  首次运行，安装依赖..."
    npm install
fi
npm run dev > "$PROJECT_DIR/frontend/vite.log" 2>&1 &
FRONTEND_PID=$!
echo "  前端 PID: $FRONTEND_PID"

sleep 3
echo ""
echo "=========================================="
echo "  算法迭代平台已启动:"
echo "  前端:  http://localhost:5174"
echo "  后端:  http://localhost:5003/api/health"
echo "  API Key: $(grep -q ALGO_API_KEY "$PROJECT_DIR/.env" 2>/dev/null && echo '(见 .env)' || echo '未配置（请设置本地 API Key）')"
echo "  停止:  bash $PROJECT_DIR/stop.sh"
echo "=========================================="
