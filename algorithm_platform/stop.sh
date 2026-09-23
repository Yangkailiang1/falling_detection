#!/bin/bash
# ============================================
# 算法迭代平台 - 停止脚本
# ============================================

echo "停止算法迭代平台..."

# 停后端（[r] 技巧避免匹配到本脚本自身命令行）
BACKEND_PID=$(pgrep -f "[a]lgorithm_platform/backend/run.py" || true)
if [ -n "$BACKEND_PID" ]; then
    kill $BACKEND_PID 2>/dev/null
    echo "  后端已停止 ($BACKEND_PID)"
fi
pkill -f "[a]lgorithm_platform/backend/run.py" 2>/dev/null

# 停前端 vite（仅限本平台的 vite 进程）
pkill -f "[a]lgorithm_platform/frontend/node_modules/.bin/vite" 2>/dev/null
pkill -f "[a]lgorithm_platform/frontend" 2>/dev/null

echo "完成。"
