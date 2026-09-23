"""
萤石大模型对话服务 — 医疗简报生成 + 通用对话

认证方式（2026-07-30 更新）：
  推荐: API Key → openai.ezviz.com/v1 (OpenAI兼容，支持 system role / max_tokens)
  备用: accessToken → open.ys7.com/.../chat/completions (旧接口，仅 user role)
"""

import requests
import json
import os
import base64
import logging
from config import Config
from app.services.ezviz_auth import token_manager
from app.utils.exceptions import EzvizAPIError

logger = logging.getLogger(__name__)


# === 核心调用函数 ===========================================================

def chat_completion(messages, model=None, temperature=0.7, max_tokens=None, stream=False):
    """
    统一入口：优先使用 OpenAI 兼容接口（API Key），失败降级到旧接口（accessToken）。

    参数:
        messages(list): [{'role': 'user'|'system'|'assistant', 'content': '...'}]
        model(str|None): 模型名，默认从 Config 读取
        temperature(float): 0-2
        max_tokens(int|None): 最大输出 token 数
        stream(bool): 是否流式

    返回: {'content': str, 'model': str, 'usage': {...}}
    """
    if Config.EZS_API_KEY:
        try:
            return _chat_completion_openai(
                messages, model or Config.EZS_CHAT_MODEL,
                temperature, max_tokens, stream
            )
        except EzvizAPIError:
            logger.warning("OpenAI 兼容接口失败，降级到旧接口")
    return _chat_completion_legacy(messages, model, temperature, stream)


def chat_completion_stream(messages, model=None, temperature=0.7, max_tokens=None):
    """
    流式对话，自动选择接口。返回 generator。
    """
    if Config.EZS_API_KEY:
        try:
            yield from _chat_completion_openai(
                messages, model or Config.EZS_CHAT_MODEL,
                temperature, max_tokens, stream=True
            )
            return
        except EzvizAPIError:
            logger.warning("OpenAI 兼容接口流式失败，降级")
    yield from _chat_completion_legacy(
        messages, model, temperature, stream=True
    )


# === OpenAI 兼容接口（推荐）==================================================

def _chat_completion_openai(messages, model, temperature=0.7, max_tokens=None, stream=False):
    """
    API Key 方式调用萤石大模型
    接口: POST https://openai.ezviz.com/v1/chat/completions
    鉴权: Authorization: Bearer {EZS_API_KEY}
    特点: 支持 system role、max_tokens、15+ 模型
    """
    url = f"{Config.EZS_OPENAI_API_URL}/chat/completions"
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {Config.EZS_API_KEY}'
    }

    payload = {
        'model': model,
        'messages': messages,
        'temperature': temperature,
        'stream': stream
    }
    if max_tokens is not None:
        payload['max_tokens'] = max_tokens

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=120, stream=stream)
        response.raise_for_status()

        if stream:
            return _parse_stream_response(response)
        else:
            return _parse_response(response, model)

    except requests.RequestException as e:
        raise EzvizAPIError(f"萤石大模型 API 网络错误: {str(e)}")


# === 旧接口（accessToken，备用）=============================================

def _chat_completion_legacy(messages, model=None, temperature=0.7, stream=False):
    """
    旧接口：accessToken 鉴权，不支持 system role / max_tokens。
    作为 OpenAI 兼容接口不可用时的降级方案。
    """
    if model is None:
        model = Config.EZS_CHAT_MODEL

    token = token_manager.get_token()
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {token}',
        'Connection': 'close'
    }

    # 旧接口不支持 system role，合并到 user 消息中
    flat_messages = _flatten_system_messages(messages)

    payload = {
        'model': model,
        'messages': flat_messages,
        'temperature': temperature,
        'stream': stream
    }
    # 不设 max_tokens — 旧接口设置此项返回 500

    try:
        response = requests.post(
            Config.EZS_CHAT_API_URL,
            headers=headers,
            json=payload,
            timeout=120,
            stream=stream
        )
        response.raise_for_status()

        if stream:
            return _parse_legacy_stream(response)
        else:
            return _parse_response(response, model)

    except requests.RequestException as e:
        raise EzvizAPIError(f"DeepSeek API网络错误: {str(e)}")


# === 响应解析 ===============================================================

def _parse_response(response, model):
    data = response.json()
    choices = data.get('choices', [])
    if choices:
        msg = choices[0].get('message', {})
        return {
            'content': msg.get('content', ''),
            'model': data.get('model', model),
            'usage': data.get('usage', {}),
            'finish_reason': choices[0].get('finish_reason', '')
        }
    return {'content': '', 'model': model, 'usage': {}, 'finish_reason': 'empty'}


def _parse_stream_response(response):
    """解析 OpenAI 兼容格式的 SSE 流"""
    for line in response.iter_lines():
        if not line:
            continue
        line_str = line.decode('utf-8')
        if line_str.startswith('data: '):
            data_str = line_str[6:]
            if data_str.strip() == '[DONE]':
                break
            try:
                chunk = json.loads(data_str)
                choices = chunk.get('choices', [])
                if choices:
                    delta = choices[0].get('delta', {})
                    content = delta.get('content', '')
                    if content:
                        yield content
            except json.JSONDecodeError:
                continue


def _parse_legacy_stream(response):
    """解析旧接口 SSE 流（萤石特有格式）"""
    for line in response.iter_lines():
        if not line:
            continue
        line_str = line.decode('utf-8')
        if line_str.startswith('data: '):
            data_str = line_str[6:]
            if data_str.strip() == '[DONE]':
                break
            try:
                chunk = json.loads(data_str)
                choices = chunk.get('choices', [])
                if choices:
                    delta = choices[0].get('delta', {})
                    content = delta.get('content', '')
                    if content:
                        yield content
            except json.JSONDecodeError:
                continue


def _flatten_system_messages(messages):
    """将 system role 消息合并到 user 消息中（兼容旧接口）"""
    system_parts = []
    user_parts = []
    for msg in messages:
        if msg['role'] == 'system':
            system_parts.append(msg['content'])
        elif msg['role'] == 'user':
            user_parts.append(msg['content'])
    if system_parts:
        prefix = '；'.join(system_parts)
        if user_parts:
            user_parts[0] = f"{prefix}\n\n用户问题：{user_parts[0]}"
        else:
            user_parts.append(prefix)
    return [{'role': 'user', 'content': c} for c in user_parts]


# === 业务方法 ===============================================================

FALL_REPORT_SYSTEM_PROMPT = (
    "你是一位资深的老年医学专家和急诊科医生。"
    "请根据以下跌倒检测数据和现场抓拍图，生成一份面向家属的医疗建议简报。"
    "若附带了现场抓拍图，请结合图片观察：画面中是否有人倒地、姿态（侧卧/仰卧/坐地）、"
    "周围有无血迹/碰撞物等受伤迹象，作为伤情评估的补充依据。"
    "报告需简明扼要，包含：\n"
    "1. 可能的损伤类型和严重程度评估\n"
    "2. 应急处理措施（明确哪些不能做，如不要随意搬动）\n"
    "3. 是否需要立即就医及其理由\n"
    "4. 后续观察要点\n\n"
    "要求：语言通俗易懂，适合非医学专业的家属阅读，不超过200字。\n"
    "报告中无需提及具体时间（检测时间已由系统单独标注），"
    "不得使用'今晨/今早/今天/刚才/此刻'等时间词。"
)


def _read_image_b64(path: str, max_side: int = 768, quality: int = 85) -> str:
    """读取本地图片并转 base64 JPEG data URL（缩小尺寸控制 token 消耗）。失败返回空串。"""
    if not path or not os.path.isfile(path):
        return ""
    try:
        import cv2
        img = cv2.imread(path)
        if img is None:
            return ""
        h, w = img.shape[:2]
        scale = min(1.0, max_side / max(w, h))
        if scale < 1.0:
            img = cv2.resize(img, (int(w * scale), int(h * scale)))
        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            return ""
        return base64.b64encode(jpg.tobytes()).decode()
    except Exception as e:
        logger.warning(f"[EZChat] 医疗报告图片编码失败: {e}")
        return ""


def generate_fall_medical_report(fall_data, image_path: str = None):
    """
    根据跌倒检测数据(文字) + 现场抓拍图(可选, 视觉) 生成医疗简报。

    image_path: 本地抓拍图绝对路径 (backend/data/captures/<event_id>.jpg)。
        存在且可读时以多模态(image_url + base64)发送, 让模型结合画面判断是否倒地/受伤迹象。
    模型: Config.EZS_CHAT_MODEL (qwen3.5-flash 已实测支持视觉, 廉价档; 备选 qwen3-vl-plus)。
    视觉消息必须走 OpenAI 兼容接口 (API Key), 不做旧接口降级。
    """
    risk_map = {
        'head':     ('I级(高危)', '头部着地，颅脑损伤风险极高'),
        'spine':    ('I级(高危)', '脊柱着地，脊髓损伤风险'),
        'hip':      ('II级(中危)', '髋部着地，股骨颈骨折风险'),
        'shoulder': ('II级(中危)', '肩部着地，锁骨/肱骨损伤风险'),
        'hand':     ('III级(低危)', '手部着地，手腕/前臂损伤可能'),
        'elbow':    ('III级(低危)', '肘部着地，关节损伤可能'),
        'knee':     ('III级(低危)', '膝部着地，髌骨/半月板损伤可能'),
    }

    landing = fall_data.get('landing_part', 'unknown')
    level, assessment = risk_map.get(landing, ('未知', '需要进一步评估'))
    # [2026-08-13] 有实际分级时覆盖触地部位映射(真实跌倒 pose 风险可能比部位映射更严重,
    # 如冲击评分高/头躯干着地 → I级, 而肘部触地静态映射只是 III级)
    _explicit_level = fall_data.get('risk_level', '')
    if _explicit_level in ('I', 'II', 'III'):
        level = f"{_explicit_level}级({ {'I': '高危', 'II': '中危', 'III': '低危'}[_explicit_level] })"

    user_prompt = (
        f"跌倒检测数据如下：\n"
        f"- 检测时间：{fall_data.get('time', '未知')}\n"
        f"- 着地部位：{landing}（{assessment}）\n"
        f"- 跌倒方向：{fall_data.get('fall_direction', '未知')}\n"
        f"- 检测置信度：{fall_data.get('confidence', 0):.0%}\n"
        f"- 风险等级：{level}\n\n"
        f"请结合检测数据和现场抓拍图生成医疗简报。"
    )

    # 现场抓拍图 → 多模态消息 (文字 + 图片)
    img_b64 = _read_image_b64(image_path)
    if img_b64:
        messages = [
            {'role': 'system', 'content': FALL_REPORT_SYSTEM_PROMPT},
            {'role': 'user', 'content': [
                {'type': 'text', 'text': user_prompt},
                {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{img_b64}'}},
            ]},
        ]
        # 视觉消息必须走 OpenAI 兼容接口 (API Key), 不降级旧接口
        result = _chat_completion_openai(messages, Config.EZS_CHAT_MODEL, 0.5, 300, False)
    else:
        messages = [
            {'role': 'system', 'content': FALL_REPORT_SYSTEM_PROMPT},
            {'role': 'user', 'content': user_prompt},
        ]
        result = chat_completion(messages, max_tokens=300, temperature=0.5)

    return {
        # [2026-08-13] 确定性时间直接字符串拼接, 不让 LLM 生成(避免"今晨/今天"等错误推测);
        # 报告正文由 LLM 生成(已提示不再涉及时间)
        'report': f"⏰ 跌倒检测时间：{fall_data.get('time', '未知')}\n{result.get('content', '')}",
        'risk_level': level,
        'recommendation': assessment,
        'model': result.get('model', Config.EZS_CHAT_MODEL),
        'raw_data': fall_data,
    }


def simple_chat(user_message, system_prompt=None):
    """
    通用对话接口。
    参数:
        user_message(str): 用户消息
        system_prompt(str|None): 系统提示词（新接口支持，旧接口自动合并到 user 消息）
    返回: 模型回复的 content 字符串
    """
    messages = []
    if system_prompt:
        messages.append({'role': 'system', 'content': system_prompt})
    messages.append({'role': 'user', 'content': user_message})

    result = chat_completion(messages)
    return result.get('content', '')
