"""
# 萤石原生语音下发服务
# 功能: 通过 EZVIZ /api/lapp/voice/sendonce 将 TTS 语音下发到摄像头扬声器播放
# 对应方案书: §7.2 语音交互确认 — 摄像头扬声器播放问询
"""
import io
import os
import tempfile
from config import Config
from app.services.ezviz_auth import token_manager
from app.utils.exceptions import EzvizAPIError
from app.inference.inference_config import DEVICE_SERIAL, TEST_PHONE

# TTS 引擎 [2026-08-03]: edge-tts(微软, 国内可用) 优先, gTTS(Google, 国内不可达) 降级
try:
    import edge_tts
    HAS_EDGE_TTS = True
except ImportError:
    HAS_EDGE_TTS = False

try:
    from gtts import gTTS
    HAS_GTTS = True
except ImportError:
    HAS_GTTS = False

# 语音播报模板 — 对应方案书 §7.2
# 分级: I级(头部/脊柱)10s问询 / II级30s / III级60s
VOICE_PROMPTS = {
    "I": [
        "检测到您摔倒了，请问是否需要呼叫家人？需要帮助请说帮我呼叫，如果没事请说我没事。",
        "您还好吗？请尽快回应，系统将在十秒后呼叫紧急联络人。",
        "即将呼叫紧急联络人，请回应或取消。",
    ],
    "II": [
        "检测到您摔倒了，请问是否需要呼叫家人？需要帮助请说帮我呼叫，如果没事请说我没事。",
        "您还好吗？检测到您可能摔倒了。请回应。",
        "最后提醒，若无回应，系统将自动呼叫紧急联络人。",
    ],
    "III": [
        "检测到您摔倒了，请问是否需要呼叫家人？需要帮助请说帮我呼叫，如果没事请说我没事。",
        "您还好吗？请不要慌张，需要帮助吗？",
        "温馨提示，系统将继续关注您的状态。",
    ],
}


def tts_to_mp3_bytes(text: str, lang: str = "zh-CN") -> io.BytesIO:
    """
    将文字转为 MP3 音频流（edge-tts 优先，gTTS 降级）

    引擎说明:
      - edge-tts (微软): 国内可访问、免费无需key、中文音质好 [2026-08-03 默认]
      - gTTS (Google): 国内网络不可达（超时），仅作降级
    参数:
        text: 要转换的文字
        lang: 语言代码
    返回: BytesIO 对象（MP3 音频数据）
    """
    import asyncio

    if HAS_EDGE_TTS:
        try:
            voice_map = {"zh": "zh-CN-XiaoxiaoNeural", "zh-CN": "zh-CN-XiaoxiaoNeural",
                         "en": "en-US-AriaNeural"}
            voice = voice_map.get(lang, "zh-CN-XiaoxiaoNeural")

            fd, temp_path = tempfile.mkstemp(prefix="edge_tts_", suffix=".mp3")
            os.close(fd)

            async def _gen():
                comm = edge_tts.Communicate(text=text, voice=voice)
                await comm.save(temp_path)

            asyncio.run(_gen())
            with open(temp_path, "rb") as f:
                data = f.read()
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            buf = io.BytesIO(data)
            buf.seek(0)
            buf.name = "tts.mp3"
            return buf
        except Exception as e:
            print(f"[TTS] edge-tts 失败: {e}，降级 gTTS")

    if HAS_GTTS:
        tts = gTTS(text=text, lang=lang[:2], slow=False)
        buf = io.BytesIO()
        tts.write_to_fp(buf)
        buf.seek(0)
        buf.name = "tts.mp3"  # 给 BytesIO 一个文件名
        return buf

    raise EzvizAPIError("TTS 引擎不可用，请安装: pip install edge-tts gtts")


def send_voice_to_device(
    device_serial: str,
    audio_data: io.BytesIO,
    channel_no: int = 1,
) -> dict:
    """
    通过萤石 API 下发语音文件到摄像头扬声器播放

    API: POST /api/lapp/voice/sendonce
    Content-Type: multipart/form-data

    参数:
        device_serial: 设备序列号 (如 CHANGE_ME_DEVICE_SERIAL)
        audio_data: 音频文件二进制数据 (BytesIO, mp3/wav/aac, max 20MB, max 60s)
        channel_no: 通道号

    返回: {"code": "200", "msg": "操作成功!"}

    错误码:
        - 20007: 设备不在线
        - 20015: 设备不支持对讲
        - 111000: 资源包余量不足
    """
    import requests

    token = token_manager.get_token()
    url = f"{Config.EZS_API_BASE_URL}/api/lapp/voice/sendonce"

    # 确保 BytesIO 有文件名属性（multipart 需要）
    if not hasattr(audio_data, 'name') or not audio_data.name:
        audio_data.name = 'tts.mp3'

    try:
        response = requests.post(
            url,
            data={
                "accessToken": token,
                "deviceSerial": device_serial,
                "channelNo": channel_no,
            },
            files={
                "voiceFile": (audio_data.name, audio_data, "audio/mpeg"),
            },
            timeout=30,
        )
        data = response.json()

        if data.get("code") != "200":
            error_code = data.get("code", "unknown")
            error_msg_map = {
                "20002": "设备不存在",
                "20007": "设备不在线",
                "20015": "设备不支持对讲",
                "20018": "该用户不拥有该设备",
                "111000": "语音资源包余量不足",
            }
            human_msg = error_msg_map.get(error_code, data.get("msg", "未知错误"))
            raise EzvizAPIError(
                f"语音下发失败[{error_code}]: {human_msg}",
                ezviz_code=error_code,
            )

        return data

    except requests.RequestException as e:
        raise EzvizAPIError(f"语音下发网络错误: {str(e)}")


def speak_text(
    text: str,
    device_serial: str = DEVICE_SERIAL,
    channel_no: int = 1,
) -> dict:
    """
    一键语音播报: TTS 生成 + 下发到摄像头扬声器

    使用场景:
    - 跌倒检测后的语音问询
    - 高危告警的紧急语音播报
    - 定时温馨提醒

    参数:
        text: 要播报的文字内容
        device_serial: 设备序列号
        channel_no: 通道号

    返回: {"code": "200", "msg": "操作成功!"}

    示例:
        speak_text("检测到您可能摔倒了，请说我没事。", DEVICE_SERIAL)
    """
    if not (HAS_EDGE_TTS or HAS_GTTS):
        return {
            "code": "no_tts",
            "msg": "TTS 引擎未安装（edge-tts / gTTS）。请运行: pip install edge-tts gtts。"
                   "语音播报功能暂时不可用，请使用前端 Web Speech API 方案。"
        }

    # 1. TTS 生成 MP3
    audio_buf = tts_to_mp3_bytes(text)

    # 2. 下发到摄像头
    return send_voice_to_device(device_serial, audio_buf, channel_no)


def speak_fall_prompt(
    risk_level: str,
    stage: int = 0,
    device_serial: str = DEVICE_SERIAL,
) -> dict:
    """
    播放跌倒检测语音问询

    参数:
        risk_level: 风险等级 (II/III), I级需直接紧急联络不播报
        stage: 播报阶段 (0=首次, 1=50%节点, 2=80%节点)
        device_serial: 设备序列号

    返回: API 响应字典
    """
    prompts = VOICE_PROMPTS.get(risk_level, VOICE_PROMPTS["II"])
    idx = min(stage, len(prompts) - 1)
    text = prompts[idx]

    print(f"\n{'='*60}")
    print(f"  [摄像头语音播报]")
    print(f"  设备: {device_serial}")
    print(f"  风险等级: {risk_level}级")
    print(f"  播报内容: {text}")
    print(f"{'='*60}\n")

    return speak_text(text, device_serial)

