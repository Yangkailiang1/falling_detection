#!/bin/bash
# ============================================
# 萤石平台接入分析平台 - 一键停止脚本
# 功能: 停止 无头语音daemon + watchdog + Flask后端(5001) + Vue3前端(5173)
#       + 小程序 standalone API(5002, Windows侧)
# 注意: 必须【先停 watchdog】再停后端, 否则 watchdog 检测到后端退出会立刻重新拉起
# 注意: 不管理 ComfyUI(port 8188) 等其他 GPU 进程——那是独立进程, 非本平台组件。
#       如需释放 GPU 显存(v2.2 用), 手动: nvidia-smi 查 PID → kill -9 <PID>
# ============================================

echo "=========================================="
echo "  萤石平台接入分析平台 - 停止中..."
echo "=========================================="

# --- [1/6] 停止无头语音 daemon ---
echo ""
echo "[1/6] 停止无头语音 daemon..."
VOICE_PIDS=$(pgrep -f "[h]eadless_voice.js" 2>/dev/null)
if [ -n "$VOICE_PIDS" ]; then
    for pid in $VOICE_PIDS; do
        kill "$pid" 2>/dev/null && echo "  语音 daemon (PID: $pid) 已停止"
    done
else
    echo "  未找到运行中的语音 daemon"
fi
# 清理 Puppeteer 残留的 headless Chromium (node 被强杀后浏览器变孤儿, 2026-08-13 实测)
CHROME_PIDS=$(pgrep -f "puppeteer_dev_chrome_profile" 2>/dev/null)
if [ -n "$CHROME_PIDS" ]; then
    for pid in $CHROME_PIDS; do
        kill -9 "$pid" 2>/dev/null
    done
    echo "  已清理 Puppeteer Chromium 残留 ($(echo "$CHROME_PIDS" | wc -w) 进程)"
fi

# --- [2/6] 停止 watchdog (必须在后端之前, 否则会重启 run.py) ---
echo ""
echo "[2/6] 停止后端 watchdog..."
WD_PIDS=$(pgrep -f "[w]atchdog.sh" 2>/dev/null)
if [ -n "$WD_PIDS" ]; then
    for pid in $WD_PIDS; do
        kill "$pid" 2>/dev/null && echo "  watchdog (PID: $pid) 已停止"
    done
else
    echo "  未找到运行中的 watchdog"
fi

# --- [3/6] 停止Flask后端 ---
echo ""
echo "[3/6] 停止Flask后端..."
# 查找占用5001端口的进程
BACKEND_PIDS=$(lsof -ti:5001)
if [ -n "$BACKEND_PIDS" ]; then
    for pid in $BACKEND_PIDS; do
        kill "$pid" 2>/dev/null && echo "  后端进程 (PID: $pid) 已停止"
    done
else
    echo "  未找到运行中的后端进程"
fi

# --- [4/6] 停止Vue3前端 ---
echo ""
echo "[4/6] 停止Vue3前端..."
# 查找占用5173端口的进程（Vite开发服务器）
FRONTEND_PIDS=$(lsof -ti:5173)
if [ -n "$FRONTEND_PIDS" ]; then
    for pid in $FRONTEND_PIDS; do
        kill "$pid" 2>/dev/null && echo "  前端进程 (PID: $pid) 已停止"
    done
else
    echo "  未找到运行中的前端进程"
fi

# --- [5/6] 停止小程序 standalone API (Windows 侧, 5002) ---
echo ""
echo "[5/6] 停止小程序 standalone API (Windows 侧, 5002)..."
if command -v powershell.exe >/dev/null 2>&1; then
    # 按命令行匹配 standalone_mini_api.py 的 python 进程
    KILLED=$(powershell.exe -NoProfile -Command "\$n=0; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*standalone_mini_api.py*' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force; \$n++ }; Write-Host \$n" 2>/dev/null | tail -1)
    if [ -n "$KILLED" ] && [ "$KILLED" -gt 0 ] 2>/dev/null; then
        echo "  standalone 进程 (${KILLED}个) 已停止"
    else
        echo "  未找到运行中的 standalone 进程 (或 Windows 互操作未开)"
    fi
else
    echo "  ⚠ 未检测到 powershell.exe，跳过 standalone 停止"
fi

# --- [6/6] 暴力清理：按进程名查找并终止（仅限本平台，排除算法平台 algorithm_platform）---
echo ""
echo "[6/6] 清理残留进程..."

# 清理 run.py 相关进程（排除算法平台; [r] 避免匹配本脚本自身）
RUN_PIDS=$(pgrep -f "[r]un.py" 2>/dev/null | while read p; do
  cmd=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null)
  echo "$cmd" | grep -q "algorithm_platform" && continue
  echo "$p"
done)
if [ -n "$RUN_PIDS" ]; then
    for pid in $RUN_PIDS; do
        kill "$pid" 2>/dev/null && echo "  run.py (PID: $pid) 已终止"
    done
fi

# 清理 vite 相关进程（仅本平台 analysis_platform/frontend; 不匹配算法平台 algorithm_platform）
VITE_PIDS=$(pgrep -f "analysis_platform/frontend" 2>/dev/null)
if [ -n "$VITE_PIDS" ]; then
    for pid in $VITE_PIDS; do
        kill "$pid" 2>/dev/null && echo "  vite (PID: $pid) 已终止"
    done
fi

# 清理后端残留的 ffmpeg RTSP 子进程 [V9.9 加固]
# 根因: kill run.py 时 video/audio 两条 ffmpeg 不一定随之退出 → 孤儿占用摄像头 RTSP 会话,
#       下次启动视频流连不上("流异常, 重连")。实测 8h 孤儿。匹配 pipe:1 输出为后端专用。
FFMPEG_PIDS=$(pgrep -f "ffmpeg.*rtsp://.*pipe:1" 2>/dev/null)
if [ -n "$FFMPEG_PIDS" ]; then
    for pid in $FFMPEG_PIDS; do
        kill "$pid" 2>/dev/null && echo "  ffmpeg RTSP 子进程 (PID: $pid) 已终止"
    done
else
    echo "  无残留 ffmpeg RTSP 子进程"
fi

# --- 清理公网隧道 ---
echo ""
echo "[额外] 清理公网隧道..."
# serveo.net SSH 隧道
SERVEO_PIDS=$(pgrep -f "serveo.net" 2>/dev/null)
if [ -n "$SERVEO_PIDS" ]; then
    for pid in $SERVEO_PIDS; do
        kill "$pid" 2>/dev/null && echo "  serveo 隧道 (PID: $pid) 已断开"
    done
fi
# localtunnel 隧道
LT_PIDS=$(pgrep -f "localtunnel" 2>/dev/null)
if [ -n "$LT_PIDS" ]; then
    for pid in $LT_PIDS; do
        kill "$pid" 2>/dev/null && echo "  localtunnel 隧道 (PID: $pid) 已断开"
    done
fi
# ngrok 隧道
NGROK_PIDS=$(pgrep -f "ngrok" 2>/dev/null)
if [ -n "$NGROK_PIDS" ]; then
    for pid in $NGROK_PIDS; do
        kill "$pid" 2>/dev/null && echo "  ngrok 隧道 (PID: $pid) 已断开"
    done
fi

echo ""
echo "=========================================="
echo "  所有服务已停止"
echo "=========================================="
