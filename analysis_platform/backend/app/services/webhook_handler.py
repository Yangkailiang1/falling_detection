"""
# 萤石云信令 - 消息推送 Webhook 接收服务 [V7.2 按新契约修复]
# 功能: 接收萤石平台推送的设备告警、状态变更等消息
# 对应文档: https://open.ys7.com/help/5128 (消息推送服务·开发参考)
#            https://open.ys7.com/help/1605 (开通消息推送服务)
#
# 契约要点 (2026.03 新版):
#   - 消息结构: {"header": {type, deviceId, channelNo, messageId, messageTime}, "body": {...}}
#   - 响应要求: HTTP 200 + JSON 必须回显 header.messageId，否则判定推送失败
#   - 超时 2 秒，失败重试 1~3 次 → 接收端必须按 messageId 去重
#   - 签名(可选): Signature = hmac_sha1(Secret, Message + Timestamp)，Timestamp 在 Header `t`
#
# 推送消息类型:
#   ys.alarm       — 萤石设备告警消息 (移动侦测、人体感应等)
#   ys.onoffline   — 设备上下线消息 (旧版别名 ys.status 兼容)
#   ys.calling     — 呼叫消息
#   ys.open.isapi  — 海康设备 ISAPI 透传事件
#   ys.open.ai.resultData — AI 算法结果通知 (预留)
#
# 兼容策略: 新格式 header/body 优先，旧格式 type/data 兜底（replay 与历史测试数据不失效）
"""
import json
import hmac
import hashlib
import base64
import threading
import logging
from collections import deque
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# === 消息类型映射 ===
MESSAGE_HANDLERS = {
    "ys.alarm": "alarm",
    "ys.status": "status",        # 旧版上下线别名（兼容）
    "ys.onoffline": "status",     # 新版上下线
    "ys.calling": "calling",
    "ys.isapi": "isapi",          # 旧版 ISAPI 别名（兼容）
    "ys.open.isapi": "isapi",
}

# 告警类型码 → 中文名（补充 describe 缺失时用）
ALARM_TYPE_NAMES = {
    10000: "人体感应事件",
    10001: "移动侦测",
    10002: "遮挡报警",
    10003: "越界侦测",
    10004: "区域入侵",
    10005: "烟感告警",
    10008: "水浸告警",
    10151: "声音异常",
    10152: "人脸侦测",
}

# messageId 去重（防重试风暴）：上限 1000，超限丢弃最旧
_seen_ids = deque(maxlen=1000)
_seen_lock = threading.Lock()


# === 签名验证 ===

def verify_signature(body: bytes, signature: str, secret: str, timestamp: str = "") -> bool:
    """
    验证萤石推送签名 (HMAC-SHA1)

    算法 (官方文档 + Java 示例):
        Signature = hmac_sha1(Secret, Message + Timestamp)
      - Message = 原始 HTTP 请求体 (bytes)
      - Timestamp = HTTP Header `t`（毫秒级时间戳，字符串拼接）
      - 计算值转 hex 后与 Header `Signature` 比对

    未配置 secret 或签名缺失时跳过验证（保持灰度可开关）。
    """
    if not secret or not signature:
        return True

    message = body + timestamp.encode('utf-8')
    computed = hmac.new(
        secret.encode('utf-8'),
        message,
        hashlib.sha1
    ).hexdigest()
    return hmac.compare_digest(computed, signature.lower())


# === 消息归一化 ===

def _extract_message(payload: dict) -> tuple:
    """
    归一化推送消息，兼容新旧两种格式

    新格式 (2026.03 契约): {"header": {type, messageId, ...}, "body": {...}}
    旧格式 (历史):        {"type": ..., "data": {...}}

    返回: (msg_type, data, meta)
        meta: {"message_id", "message_time", "device_id", "channel_no"}
    """
    if not isinstance(payload, dict):
        return ("unknown", {}, {})

    header = payload.get("header")
    if isinstance(header, dict) and header.get("type"):
        return (
            str(header.get("type", "unknown")),
            payload.get("body") if isinstance(payload.get("body"), dict) else {},
            {
                "message_id": str(header.get("messageId", "")),
                "message_time": header.get("messageTime", ""),
                "device_id": str(header.get("deviceId", "")),
                "channel_no": header.get("channelNo", ""),
            },
        )
    # 旧格式兜底
    return (
        str(payload.get("type", "unknown")),
        payload.get("data") if isinstance(payload.get("data"), dict) else {},
        {},
    )


def _dedupe(message_id: str) -> bool:
    """按 messageId 去重，返回 True 表示已处理过（应跳过）"""
    if not message_id:
        return False
    with _seen_lock:
        if message_id in _seen_ids:
            return True
        _seen_ids.append(message_id)
        return False


def _alarm_type_name(alarm_type) -> str:
    """告警类型码 → 中文名"""
    try:
        code = int(alarm_type)
    except (TypeError, ValueError):
        return str(alarm_type)
    return ALARM_TYPE_NAMES.get(code, f"type-{code}")


# === 告警图片证据关联 ===

def _link_alarm_to_fall_event(dev_serial: str, alarm_time, picture_list: list):
    """
    告警证据关联：将告警图片 URL 写入最近 60s 内同设备的归档事件

    场景: 推理抓拍失败时，设备告警图片兜底作为现场证据。
    依赖: fall_event_archive.list_events / update_event（字段 capture_pic_url 需已存在）
    """
    if not picture_list or not dev_serial:
        return None
    try:
        from app.services.fall_event_archive import list_events, update_event
        events, _ = list_events(limit=10, days=1)
        for event in events:
            if event.device_serial != dev_serial:
                continue
            # 事件时间与告警时间比对（60 秒窗口）
            try:
                event_time = datetime.fromisoformat(event.created_at.replace("Z", "+00:00"))
                if alarm_time and isinstance(alarm_time, (int, float)):
                    alarm_dt = datetime.fromtimestamp(alarm_time / 1000, tz=timezone.utc)
                else:
                    alarm_dt = datetime.now(timezone.utc)
                if abs((alarm_dt - event_time).total_seconds()) > 60:
                    continue
            except (ValueError, TypeError):
                pass
            # 事件已有更早的现场图则跳过（主动抓拍优先）
            if event.capture_pic_url:
                continue
            first_pic = picture_list[0].get("url", "") if isinstance(picture_list[0], dict) else ""
            if not first_pic:
                continue
            # 下载并持久化本地 (萤石签名 URL 24h 过期 → 存本地避免后续加载失败) [V9.4]
            local_path = ""
            try:
                from app.services.capture_assets import persist_capture_pic
                local_path = persist_capture_pic(event.event_id, first_pic)
            except Exception:
                pass
            update_event(
                event.event_id,
                capture_pic_url=first_pic,
                capture_time=str(alarm_time or ""),
                capture_pic_path=local_path,
            )
            logger.info(f"[Webhook] 告警图片已关联事件 {event.event_id}: {first_pic[:80]}")
            return event.event_id
    except Exception as e:
        logger.warning(f"[Webhook] 告警证据关联异常(不影响流程): {e}")
    return None


# === 消息处理器 ===

def handle_alarm_message(data: dict, meta: dict = None) -> dict:
    """
    处理告警消息 (ys.alarm)

    新版 body 字段: alarmId/alarmTime/alarmType/channel/channelName/describe/
                    devSerial/location/relationId/pictureList[]
    旧版字段兜底:   alarmTypeName/alarmPicUrl/deviceSerial

    返回提取的告警信息 dict（便于单测断言）
    """
    alarm_type = data.get("alarmType", "unknown")
    describe = data.get("describe") or data.get("alarmTypeName", "")
    if not describe:
        describe = _alarm_type_name(alarm_type)
    dev_serial = data.get("devSerial") or data.get("deviceSerial", "unknown")
    alarm_time = data.get("alarmTime", 0)
    location = data.get("location", "")
    meta = meta or {}

    # 图片列表: 新版 pictureList[]（含 isEncrypted），旧版 alarmPicUrl 兜底
    picture_list = data.get("pictureList") or []
    if not picture_list and data.get("alarmPicUrl"):
        picture_list = [{"url": data["alarmPicUrl"], "isEncrypted": 0}]

    logger.info(
        f"[Webhook] 告警: {describe}(type={alarm_type}) "
        f"| 设备={dev_serial} | 位置={location or '未知'} | 图片数={len(picture_list)}"
    )
    for pic in picture_list:
        if isinstance(pic, dict) and pic.get("isEncrypted") == 1:
            logger.info(f"[Webhook] 告警图片已加密(isEncrypted=1)，解密待实现: {pic.get('url', '')[:80]}")

    # 证据关联: 告警图片写入最近归档事件（主动抓拍失败时的兜底证据）
    _link_alarm_to_fall_event(dev_serial, alarm_time, picture_list)

    return {
        "alarm_type": alarm_type,
        "describe": describe,
        "device_serial": dev_serial,
        "alarm_time": alarm_time,
        "location": location,
        "picture_count": len(picture_list),
        "message_id": meta.get("message_id", ""),
    }


def handle_status_message(data: dict, meta: dict = None) -> dict:
    """
    处理设备上下线消息 (ys.onoffline / 旧版 ys.status)

    ys.onoffline body: dasId/deviceName/devType/msgType(ONLINE|OFFLINE)/occurTime/subSerial
    旧版 body:        status(1在线)/online/deviceSerial
    """
    dev_serial = data.get("subSerial") or data.get("deviceSerial") or data.get("deviceId", "unknown")
    device_name = data.get("deviceName", "")
    msg_type = str(data.get("msgType", "")).upper()

    if msg_type in ("ONLINE", "OFFLINE"):
        online = msg_type == "ONLINE"
        detail = f"设备上线" if online else f"设备离线"
    else:
        # 旧格式兼容: status=1 在线 / online=true
        status = data.get("status", data.get("online"))
        online = status in (1, "1", True, "true", "True")
        detail = f"设备{'在线' if online else '离线'}"

    logger.info(f"[Webhook] 设备状态: {dev_serial} ({device_name}) → {detail}")
    return {"device_serial": dev_serial, "online": online, "msg_type": msg_type}


def handle_calling_message(data: dict, meta: dict = None) -> dict:
    """处理呼叫消息"""
    device_serial = data.get("devSerial") or data.get("deviceSerial", "unknown")
    status = data.get("status", 1)
    logger.info(f"[Webhook] 呼叫消息: {device_serial} | status={status}")
    return {"device_serial": device_serial, "status": status}


def handle_isapi_message(data: dict, meta: dict = None) -> dict:
    """处理 ISAPI 透传事件 (ys.open.isapi / 旧版 ys.isapi)"""
    device_serial = meta.get("device_id") or data.get("deviceSerial", "unknown")
    payload = data.get("payload", data)
    if isinstance(payload, dict):
        event_type = payload.get("eventType", "unknown")
    else:
        event_type = "unknown"
    logger.info(f"[Webhook] ISAPI事件: {event_type} | 设备={device_serial}")
    return {"device_serial": device_serial, "event_type": event_type}


# === 消息类型 → 处理器映射 ===
HANDLER_FUNCS = {
    "alarm": handle_alarm_message,
    "status": handle_status_message,
    "calling": handle_calling_message,
    "isapi": handle_isapi_message,
}


def process_message(payload: dict):
    """
    处理萤石推送消息（异步线程调用）

    1. 归一化 header/body 新格式（兼容旧格式）
    2. 按 messageId 去重（防重试风暴）
    3. 分发到对应类型处理器
    """
    msg_type, data, meta = _extract_message(payload)

    # 按 messageId 去重（重试推送的同一消息只处理一次）
    if _dedupe(meta.get("message_id", "")):
        logger.info(f"[Webhook] 重复消息已跳过: type={msg_type} messageId={meta.get('message_id')}")
        return

    handler_key = MESSAGE_HANDLERS.get(msg_type)
    handler = HANDLER_FUNCS.get(handler_key) if handler_key else None

    if handler:
        try:
            handler(data, meta)
        except Exception as e:
            logger.error(f"[Webhook] 处理 {msg_type} 失败: {e}")
    else:
        logger.info(
            f"[Webhook] 未处理的消息类型: {msg_type} "
            f"| data={json.dumps(data, ensure_ascii=False)[:200]}"
        )
