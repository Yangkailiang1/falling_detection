"""
# 萤石云信令 Webhook 接收路由 [V7.2 按新契约修复]
# 功能: 接收萤石平台推送的设备告警、状态变更等消息
# 对应文档: https://open.ys7.com/help/5128 (消息推送服务·开发参考)
#            https://open.ys7.com/help/1605 (开通消息推送服务)
#
# 契约要点:
#   - 消息结构: {"header": {type, deviceId, messageId, ...}, "body": {...}}
#   - 响应必须包含推送报文中的 messageId，否则判定推送失败:
#        {"messageId": "5e57f239793f2b007fecb0de"}
#   - 必须在 2 秒内返回 → 业务处理放异步线程
#   - 签名(可选): Signature = hmac_sha1(Secret, body + t)，Header: t / Signature
#
# 使用方式:
#   在萤石控制台 https://open.ys7.com/console/resource/message/index.html
#   配置回调地址为: https://your-public-url.com/api/webhook/ezviz
"""
import json
import threading
import logging
from flask import Blueprint, request, jsonify
from app.inference.inference_config import DEVICE_SERIAL, TEST_PHONE

logger = logging.getLogger(__name__)

webhook_bp = Blueprint('webhook', __name__)


@webhook_bp.route('/webhook/ezviz', methods=['POST'])
def receive_ezviz_push():
    """
    接收萤石消息推送回调

    萤石平台在以下事件发生时推送到此端点:
      - 设备告警 (ys.alarm): 移动侦测、人体感应等
      - 设备上下线 (ys.onoffline): 在线/离线状态变更
      - 呼叫消息 (ys.calling)
      - ISAPI 事件 (ys.open.isapi): 海康设备特有事件

    关键约束:
      - 必须在 2 秒内返回 200，且响应 JSON 回显请求 header 中的 messageId
      - 业务处理放到异步线程中
    """
    try:
        payload = request.get_json(force=True, silent=True)
        if not payload:
            logger.warning("[Webhook] 收到空消息或非JSON格式")
            return jsonify({"code": 200, "msg": "OK"}), 200

        # 新契约: header/body 结构；兼容旧格式 type/data
        header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
        msg_type = header.get("type") or payload.get("type", "unknown")
        message_id = header.get("messageId", "")
        message_time = header.get("messageTime", "")

        logger.info(
            f"[Webhook] 收到推送 | type={msg_type} | messageId={message_id} | "
            f"messageTime={message_time}"
        )

        # 签名验证（同步，毫秒级，不违反 2 秒约束）
        # Signature = hmac_sha1(Secret, body + t)，t 为毫秒时间戳（HTTP Header）
        from config import Config
        from app.services.webhook_handler import verify_signature
        secret = getattr(Config, 'EZS_WEBHOOK_SECRET', '')
        if secret:
            signature = request.headers.get("Signature", "")
            timestamp = request.headers.get("t", "")
            if not verify_signature(request.get_data(), signature, secret, timestamp):
                logger.warning("[Webhook] 签名验证失败")
                return jsonify({"code": 401, "msg": "签名验证失败"}), 401

        # 异步处理业务逻辑（不阻塞响应）
        def _async_process():
            from app.services.webhook_handler import process_message
            try:
                process_message(payload)
            except Exception as e:
                logger.error(f"[Webhook] 异步处理异常: {e}")

        thread = threading.Thread(target=_async_process, daemon=True)
        thread.start()

        # 契约要求: 响应必须回显 messageId（萤石以此判定推送成功）
        if message_id:
            return jsonify({"messageId": message_id}), 200
        # 旧格式/无 messageId 兜底（萤石将判定失败并重试，重试端已按 messageId 去重）
        logger.warning("[Webhook] 缺少 messageId，萤石将判定本次推送失败")
        return jsonify({"code": 200, "msg": "OK"}), 200

    except Exception as e:
        logger.error(f"[Webhook] 接收异常: {e}")
        return jsonify({"code": 500, "msg": str(e)}), 500


@webhook_bp.route('/webhook/ezviz/test', methods=['GET'])
def test_webhook_endpoint():
    """
    测试 Webhook 端点是否可访问

    GET /api/webhook/ezviz/test
    萤石控制台在保存回调地址前可能会发送验证请求
    """
    return jsonify({
        "code": 200,
        "msg": "Webhook endpoint is running",
        "timestamp": __import__('datetime').datetime.now().isoformat(),
    })


@webhook_bp.route('/webhook/ezviz/replay', methods=['POST'])
def replay_test_message():
    """
    手动发送测试消息以验证 webhook 处理链路（兼容新旧格式）

    POST /api/webhook/ezviz/replay
    Body (新格式):
    {
        "header": {
            "type": "ys.alarm",
            "deviceId": DEVICE_SERIAL,
            "channelNo": 1,
            "messageId": "5e57f239793f2b007fecb0de",
            "messageTime": 1754212000000
        },
        "body": {
            "alarmType": 10000,
            "describe": "移动侦测",
            "devSerial": DEVICE_SERIAL,
            "alarmTime": 1754212000000
        }
    }
    Body (旧格式): {"type": "ys.alarm", "data": {...}}
    """
    try:
        payload = request.get_json(force=True)
        if not payload:
            return jsonify({"code": 400, "msg": "请提供消息体"}), 400

        from app.services.webhook_handler import process_message
        process_message(payload)

        header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
        received_type = header.get("type") or payload.get("type", "unknown")
        message_id = header.get("messageId", "")

        result = {
            "code": 200,
            "msg": "测试消息已处理",
            "received_type": received_type,
        }
        if message_id:
            result["messageId"] = message_id
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"code": 500, "msg": str(e)}), 500
