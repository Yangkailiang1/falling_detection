"""
# 萤石摄像头语音播报路由
# 功能: TTS 文字 → 语音文件 → 下发到摄像头扬声器/浏览器播放
# 对应方案书: §7.2 语音交互确认
"""
import io
import uuid
import threading
from flask import send_file, jsonify
from flask import Blueprint, request
from app.utils.response import success_response, error_response
from app.services.ezviz_voice import speak_text, speak_fall_prompt, HAS_GTTS
from app.services.ezviz_auth import token_manager
from app.inference.inference_config import DEVICE_SERIAL, TEST_PHONE

voice_bp = Blueprint('voice', __name__)


@voice_bp.route('/voice/speak', methods=['POST'])
def speak_to_device():
    """
    TTS 文字转语音并下发到摄像头扬声器

    POST /api/voice/speak
    Body: {
        "text": "检测到您可能摔倒了",
        "device_serial": DEVICE_SERIAL,
        "channel_no": 1
    }

    返回: {"code": "200", "msg": "操作成功!"}
    """
    try:
        body = request.get_json() or {}
        text = body.get('text', '').strip()
        if not text:
            return error_response("播报文字不能为空", 400)
        if len(text) > 500:
            return error_response("播报文字不能超过500字", 400)

        device_serial = body.get('device_serial', DEVICE_SERIAL)
        channel_no = body.get('channel_no', 1)

        # 复用预热后的 MP3，告警首句不再重复等待 edge-tts；仍通过萤石
        # voice/sendonce 从摄像头扬声器播放。
        try:
            from app.services.ezviz_voice import send_voice_to_device
            audio_buf = io.BytesIO(_cached_tts_bytes(text))
            audio_buf.name = 'tts.mp3'
            result = send_voice_to_device(device_serial, audio_buf, channel_no)
        except Exception:
            # 保留原有路径作为兼容兜底（例如缓存生成失败时）。
            result = speak_text(text, device_serial, channel_no)

        if result.get('code') == 'no_tts':
            return error_response(
                "gTTS 未安装，摄像头语音播报暂不可用。"
                "请安装: pip install gtts",
                501
            )

        # 检查各类 EZVIZ 返回码
        code = result.get('code', '200')
        if code == '111000':
            # 资源包不足 — 不是技术错误，前端应降级到浏览器 TTS
            return success_response({
                "device_serial": device_serial,
                "text": text[:100],
                "fallback": True,
                "reason": "萤石语音资源包余量不足，已降级为浏览器 TTS 播报",
            }, "语音资源包不足，请使用浏览器播报")
        elif code != '200':
            return error_response(result.get('msg', '未知错误'), 502)

        return success_response({
            "device_serial": device_serial,
            "text": text[:100],
            "result": result,
        }, "语音已下发到摄像头扬声器")

    except Exception as e:
        error_str = str(e)
        if "111000" in error_str or "资源包" in error_str:
            return success_response({
                "fallback": True,
                "reason": "萤石语音资源包不足，已降级为浏览器 TTS",
            }, "语音资源包不足，请使用浏览器播报")
        return error_response(f"语音播报失败: {error_str}", 500)


@voice_bp.route('/voice/fall-prompt', methods=['POST'])
def speak_fall_interaction():
    """
    播放跌倒检测语音问询（用于分级干预）

    POST /api/voice/fall-prompt
    Body: {
        "risk_level": "II",
        "stage": 0,
        "device_serial": DEVICE_SERIAL
    }

    stage: 0=首次播报, 1=50%节点提醒, 2=80%节点最后提醒
    """
    try:
        body = request.get_json() or {}
        risk_level = body.get('risk_level', 'II')
        stage = body.get('stage', 0)
        device_serial = body.get('device_serial', DEVICE_SERIAL)

        if risk_level not in ('I', 'II', 'III'):
            return error_response("无效风险等级，请使用 I/II/III", 400)
        if stage not in (0, 1, 2):
            return error_response("无效阶段，请使用 0/1/2", 400)

        result = speak_fall_prompt(risk_level, stage, device_serial)

        if result.get('code') == 'no_tts':
            return error_response("gTTS 未安装，请安装: pip install gtts", 501)

        code = result.get('code', '200')
        if code == '111000':
            return success_response({
                "risk_level": risk_level, "stage": stage,
                "device_serial": device_serial,
                "fallback": True,
                "reason": "萤石语音资源包不足，已降级为浏览器 TTS",
            }, "语音资源包不足，请使用浏览器播报")
        elif code != '200':
            return error_response(result.get('msg', '未知错误'), 502)

        return success_response({
            "risk_level": risk_level,
            "stage": stage,
            "device_serial": device_serial,
            "result": result,
        }, f"{risk_level}级第{stage}阶段语音问询已播报")

    except Exception as e:
        return error_response(f"语音问询失败: {str(e)}", 500)


@voice_bp.route('/voice/status', methods=['GET'])
def voice_status():
    """
    查询语音 TTS 服务状态
    返回: {"gtts_available": true/false, "message": "..."}
    """
    if HAS_GTTS:
        return success_response({
            "gtts_available": True,
            "message": "TTS 服务正常，可通过 POST /api/voice/speak 下发语音到摄像头",
            "supported_formats": ["mp3"],
            "max_duration_seconds": 60,
            "max_file_size": "20MB",
        })
    else:
        return success_response({
            "gtts_available": False,
            "message": "gTTS 未安装。请运行: pip install gtts。"
                       "摄像头语音播报暂不可用，请使用前端 Web Speech API 方案。",
        })


# === TTS 音频文件端点 ===

_tts_cache = {}  # 内存缓存: {text: bytes}
_tts_cache_lock = threading.Lock()


def _cached_tts_bytes(text: str) -> bytes:
    """复用预热后的 TTS 字节，避免告警首句再次等待语音合成。"""
    with _tts_cache_lock:
        cached = _tts_cache.get(text)
    if cached:
        return cached
    from app.services.ezviz_voice import tts_to_mp3_bytes
    buf = tts_to_mp3_bytes(text)
    data = buf.getvalue()
    with _tts_cache_lock:
        _tts_cache[text] = data
    return data

@voice_bp.route('/voice/tts.mp3', methods=['GET'])
def get_tts_audio():
    """
    获取 TTS 生成的 MP3 音频文件

    GET /api/voice/tts.mp3?text=检测到您可能摔倒了

    前端用 <audio> 标签播放，同时 EZUIKit startTalk() 会把麦克风采集到的
    这个音频转发到摄像头扬声器。
    """
    text = request.args.get('text', '').strip()
    if not text:
        return error_response("缺少 text 参数", 400)
    if len(text) > 500:
        return error_response("文字不能超过500字", 400)

    try:
        data = _cached_tts_bytes(text)
        audio_buf = io.BytesIO(data)
        audio_buf.seek(0)
        return send_file(
            audio_buf,
            mimetype='audio/mpeg',
            as_attachment=False,
            download_name='tts.mp3',
        )
    except Exception as e:
        return error_response(f"TTS 生成失败: {str(e)}", 500)


# === LLM 语音意图分析端点 ===

@voice_bp.route('/voice/analyze-response', methods=['POST'])
def analyze_voice_response():
    """
    用萤石 DeepSeek 大模型分析老人的语音回应，判断意图

    POST /api/voice/analyze-response
    Body: {
        "transcript": "我没事我没事",
        "risk_level": "II",
        "context": "髋部着地，冲击速度3.2m/s"
    }

    返回: {
        "action": "cancel" | "help" | "unclear" | "no_response",
        "confidence": 0.95,
        "reason": "...",
        "raw_response": "..."
    }
    """
    try:
        body = request.get_json() or {}
        transcript = body.get('transcript', '').strip()
        risk_level = body.get('risk_level', 'II')
        context = body.get('context', '')

        if not transcript:
            return error_response("缺少 transcript 参数", 400)

        from app.services.ezviz_chat import chat_completion

        # 构建 LLM 分析提示词
        prompt = (
            f"你是一个跌倒检测系统的语音分析模块。老人摔倒后，系统通过摄像头扬声器问询，"
            f"老人回应了以下内容（语音识别结果可能不精准）：\n\n"
            f"老人说: \"{transcript}\"\n\n"
            f"背景信息: 风险等级{risk_level}级，{context}\n\n"
            f"请判断老人的意图，仅返回JSON格式:\n"
            f'{{"action": "cancel"|"help"|"unclear"|"no_response", "confidence": 0.0-1.0, "reason": "简短说明"}}\n'
            f"判断规则:\n"
            f"- cancel: 老人表示没事、取消、不用帮忙、扶一下就好\n"
            f"- help: 老人明确求助、说疼、动不了、需要帮助\n"
            f"- unclear: 听不清楚或意图不明确\n"
            f"- no_response: 没有有效回应\n"
            f"只返回JSON，不要其他内容。"
        )
        messages = [{'role': 'user', 'content': prompt}]

        try:
            result = chat_completion(messages, temperature=0.1)
            content = result.get('content', '{}')

            # 尝试解析 JSON（DeepSeek 可能返回额外文字）
            import json as json_mod
            # 提取 JSON 部分
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                content = content[json_start:json_end]

            analysis = json_mod.loads(content)
            analysis['raw_response'] = transcript
        except Exception:
            # LLM 解析失败，用规则兜底
            analysis = _rule_based_classify(transcript)

        return success_response(analysis)

    except Exception as e:
        return error_response(f"意图分析失败: {str(e)}", 502)


def _rule_based_classify(transcript: str) -> dict:
    """规则基兜底：当 LLM 不可用时用关键词匹配"""
    text = transcript.lower().replace(' ', '')
    # 取消类关键词
    if any(kw in text for kw in ['没事', '取消', '不用', '可以', '还好', '没问题', '扶我', '起来']):
        return {"action": "cancel", "confidence": 0.85, "reason": "关键词匹配: 表示没问题", "raw_response": transcript}
    # 求助类关键词
    if any(kw in text for kw in ['帮我', '救命', '疼', '动不了', '起不来', '帮忙', '不行', '受不了']):
        return {"action": "help", "confidence": 0.85, "reason": "关键词匹配: 表示需要帮助", "raw_response": transcript}
    # 不清晰的
    if len(text) > 1:
        return {"action": "unclear", "confidence": 0.5, "reason": "无法判断意图", "raw_response": transcript}
    # 无回应
    return {"action": "no_response", "confidence": 0.9, "reason": "无有效语音回应", "raw_response": transcript}


# === [V7.3] 讯飞 STT: 摄像头麦克风音频转文字 ===

# 最短有效音频: 0.5s 的 16k/16bit 单声道 PCM
FRAME_MIN = 16000 * 2 // 2

@voice_bp.route('/voice/stt', methods=['POST'])
def stt_transcribe():
    """
    摄像头麦克风音频 → 文本识别（讯飞语音听写流式版）

    请求: raw body = 16kHz 单声道 PCM 音频字节流
      (前端将 MediaRecorder 的 webm 经 AudioContext 重采样为 16k PCM)
      Content-Type: audio/pcm; rate=16000
    返回: {"success": true, "transcript": "我没事", ...}
    """
    try:
        pcm_bytes = request.get_data()
        if not pcm_bytes:
            return error_response("无音频数据", 400)
        if len(pcm_bytes) < FRAME_MIN:
            return error_response("音频过短", 400)

        from app.services.stt_service import transcribe_pcm
        result = transcribe_pcm(pcm_bytes)
        if result.get("success"):
            return success_response({
                "transcript": result["transcript"],
                "duration_ms": int(len(pcm_bytes) / 32000 * 1000),  # 16k 16bit 单声道
            }, "识别成功")
        return error_response(f"识别失败: {result.get('message')}", 502)
    except Exception as e:
        return error_response(f"STT 异常: {str(e)}", 500)

