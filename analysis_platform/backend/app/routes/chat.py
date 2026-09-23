"""
# 对应文档: 二.V2.0 - DeepSeek对话路由
# 功能: 大模型对话和跌倒医疗报告生成API端点
"""
from flask import Blueprint, request, Response
import json
from app.utils.response import success_response, error_response
from app.services import ezviz_chat
from app.utils.exceptions import EzvizAPIError

chat_bp = Blueprint('chat', __name__)


# [开发文档 三.3.3 - POST /api/chat/completions]
# 通用对话接口
@chat_bp.route('/chat/completions', methods=['POST'])
def chat_completion():
    """
    调用萤石DeepSeek大模型进行对话
    - 对应文档章节: 三.3.3 - DeepSeek对话API
    - 请求体: {'messages': [...], 'model': 'deepseek-v3', 'temperature': 0.7}
    - 返回: {'content': str, 'model': str, 'usage': {...}}
    """
    try:
        body = request.get_json() or {}
        messages = body.get('messages', [])
        if not messages:
            return error_response("消息列表不能为空", 400)

        model = body.get('model', None)
        temperature = body.get('temperature', 0.7)
        stream = body.get('stream', False)

        if stream:
            # 流式返回
            return _stream_chat_response(messages, model, temperature)

        result = ezviz_chat.chat_completion(messages, model, temperature)
        return success_response(result)
    except EzvizAPIError as e:
        return error_response(str(e), 502)


# [开发文档 二.V2.0 - 流式对话响应]
def _stream_chat_response(messages, model, temperature):
    """
    流式返回大模型对话结果（SSE格式）
    - 对应文档章节: 萤石平台/API实测报告 - SSE流式格式
    """
    def generate():
        try:
            for chunk in ezviz_chat.chat_completion_stream(
                messages, model, temperature
            ):
                yield f"data: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
    return Response(generate(), mimetype='text/event-stream')


# [开发文档 三.3.3 - POST /api/chat/fall-report]
# 跌倒医疗简报生成
@chat_bp.route('/chat/fall-report', methods=['POST'])
def generate_fall_report():
    """
    根据跌倒检测数据生成医疗简报
    - 对应文档章节: 项目方案书 - 萤石大模型医疗报告
    - 请求体: {'fall_data': {'time': '...', 'landing_part': 'hip', 'confidence': 0.95}}
    - 返回: {'report': str, 'risk_level': str, 'recommendation': str}
    """
    try:
        body = request.get_json() or {}
        fall_data = body.get('fall_data', {})
        if not fall_data:
            return error_response("跌倒数据不能为空", 400)

        result = ezviz_chat.generate_fall_medical_report(fall_data)
        return success_response(result)
    except EzvizAPIError as e:
        return error_response(str(e), 502)


# [开发文档 二.V2.0 - 简单对话]
@chat_bp.route('/chat/simple', methods=['POST'])
def simple_chat():
    """
    简单快速对话接口
    - 对应文档章节: 二.V3.0 - AI对话页面
    - 请求体: {'message': '你好', 'system_prompt': '...'}
    """
    try:
        body = request.get_json() or {}
        message = body.get('message', '')
        if not message:
            return error_response("消息不能为空", 400)

        system_prompt = body.get('system_prompt', None)
        result = ezviz_chat.simple_chat(message, system_prompt)
        return success_response(result)
    except EzvizAPIError as e:
        return error_response(str(e), 502)


# [开发文档 二.V2.0 - 可用模型列表]
@chat_bp.route('/chat/models', methods=['GET'])
def list_models():
    """
    获取可用的DeepSeek模型列表
    - 返回: [{'id': 'deepseek-v3', 'name': 'DeepSeek V3', 'available': true}]
    """
    return success_response([
        {'id': 'deepseek-v3', 'name': 'DeepSeek V3', 'available': True},
        {'id': 'deepseek-r1', 'name': 'DeepSeek R1', 'available': False,
         'note': '当前不可用，返回500错误'}
    ])
