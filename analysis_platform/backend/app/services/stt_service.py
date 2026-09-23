"""
# 讯飞语音听写（流式版）服务 [V7.3]
# 功能: 将 16kHz PCM 音频识别为文本（摄像头麦克风 → 老人回应 → 意图分析）
# 文档: https://www.xfyun.cn/doc/asr/voicedictation/API.html
#
# 接口: wss://iat-api.xfyun.cn/v2/iat (engine_type=sm_iat 通用普通话)
# 鉴权: HMAC-SHA256 签名 URL（APIKey + APISecret，服务端构造）
# 免费额度: 创建应用默认每日 500 次
#
# 流程: 浏览器录制摄像头麦克风音频 → 前端转 16k PCM → POST /api/voice/stt
#       → 本服务分片(1280B/40ms)流式发送讯飞 → 汇总识别文本返回
"""
import base64
import hashlib
import hmac
import json
import logging
import threading
import time
from datetime import datetime
from urllib.parse import quote

import websocket

logger = logging.getLogger(__name__)

# === 讯飞接口 ===
XF_URL = "wss://iat-api.xfyun.cn/v2/iat"
FRAME_SIZE = 1280   # 16k PCM 40ms 一帧
FRAME_INTERVAL = 0.04  # 40ms


def _get_credentials():
    """从 .env 读取讯飞密钥（避免 import config 循环依赖，运行时读取）"""
    import os
    from config import Config
    return {
        "app_id": getattr(Config, 'XFYUN_APPID', '') or os.getenv('XFYUN_APPID', ''),
        "api_key": getattr(Config, 'XFYUN_API_KEY', '') or os.getenv('XFYUN_API_KEY', ''),
        "api_secret": getattr(Config, 'XFYUN_API_SECRET', '') or os.getenv('XFYUN_API_SECRET', ''),
    }


def build_auth_url(api_key: str, api_secret: str) -> str:
    """
    构造带鉴权的 WebSocket URL（讯飞 HMAC-SHA256 签名）

    签名原文: host: {host}\ndate: {date}\nGET /v2/iat HTTP/1.1
    """
    host = "iat-api.xfyun.cn"
    date = datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
    signature_origin = f"host: {host}\ndate: {date}\nGET /v2/iat HTTP/1.1"
    signature_sha = hmac.new(api_secret.encode('utf-8'),
                             signature_origin.encode('utf-8'),
                             hashlib.sha256).digest()
    signature = base64.b64encode(signature_sha).decode('utf-8')
    authorization_origin = (
        f'api_key="{api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"'
    )
    authorization = base64.b64encode(authorization_origin.encode('utf-8')).decode('utf-8')
    return (f"wss://{host}/v2/iat?authorization={quote(authorization)}"
            f"&date={quote(date)}&host={host}")


def transcribe_pcm(pcm_bytes: bytes, timeout: float = 8.0) -> dict:
    """
    将 16kHz 单声道 PCM 音频识别为文本（阻塞式 create_connection + recv 循环）

    参数:
        pcm_bytes: 原始 PCM 数据（audio/L16;rate=16000）
        timeout: 总超时秒数
    返回:
        {"success": bool, "transcript": str, "code": int, "message": str}
    """
    cred = _get_credentials()
    if not all(cred.values()):
        return {"success": False, "transcript": "", "code": -1,
                "message": "讯飞密钥未配置（XFYUN_APPID/API_KEY/API_SECRET）"}

    ws_url = build_auth_url(cred["api_key"], cred["api_secret"])
    text_parts = []
    error_code = 0
    error_msg = ""
    finished = False

    ws = websocket.create_connection(ws_url, timeout=timeout)
    ws.settimeout(5)

    try:
        # === 发送阶段: 分片流式上传（40ms 间隔） ===
        total = len(pcm_bytes)
        sent = 0
        while sent < total:
            chunk = pcm_bytes[sent:sent + FRAME_SIZE]
            if not chunk:
                break
            if sent + len(chunk) >= total:
                status = 2  # 最后一帧
            elif sent == 0:
                status = 0  # 第一帧
            else:
                status = 1  # 中间帧
            frame = {
                # 讯飞要求每帧都带 common.app_id（business 仅第一帧）
                "common": {"app_id": cred["app_id"]},
                "business": ({
                    "language": "zh_cn",
                    "domain": "iat",
                    "accent": "mandarin",
                    # The browser already stops after ~600 ms of silence;
                    # keeping the cloud VAD tail short avoids an extra 5 s.
                    "vad_eos": 800,
                }) if status == 0 else {},
                "data": {
                    "status": status,
                    "format": "audio/L16;rate=16000",
                    "encoding": "raw",
                    "audio": base64.b64encode(chunk).decode('utf-8'),
                },
            }
            ws.send(json.dumps(frame))
            sent += len(chunk)
            # The complete utterance is already buffered by the camera-side
            # recorder.  Send chunks back-to-back so recognition does not
            # replay the user's speech in real time (the server still receives
            # the protocol's 40 ms framed payloads).

        # === 接收阶段: 循环读取直到完成或超时 ===
        deadline = time.time() + timeout
        while not finished and time.time() < deadline:
            try:
                message = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue  # 无消息，继续等
            if not message:
                continue
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue
            code = data.get("code", -1)
            if code != 0:
                error_code = code
                error_msg = data.get("message", "")
                break
            if data.get("data", {}).get("status") == 2:
                finished = True
            # 增量结果 (dwa=wpgs)
            for ws_item in data.get("data", {}).get("result", {}).get("ws", []):
                for w in ws_item.get("cw", []):
                    text_parts.append(w.get("w", ""))

        transcript = "".join(text_parts).strip()
        if error_code != 0:
            return {"success": False, "transcript": "",
                    "code": error_code, "message": error_msg}
        return {"success": True, "transcript": transcript, "code": 0, "message": ""}
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"[STT] 讯飞识别异常: {e}")
        return {"success": False, "transcript": "", "code": -3, "message": str(e)}
    finally:
        try:
            ws.close()
        except Exception:
            pass


def transcribe_audio_file(audio_bytes: bytes, source_format: str = "pcm") -> dict:
    """
    上传音频字节识别（兼容 wav/mp3 等格式的简易入口）
    注: 浏览器端已转 16k PCM，直接用 transcribe_pcm；此函数供测试/扩展用
    """
    return transcribe_pcm(audio_bytes)

