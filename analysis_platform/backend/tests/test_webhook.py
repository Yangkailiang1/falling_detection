"""
# 萤石消息推送 Webhook 契约测试 [V7.2]
# 功能: 验证 webhook 接收路由与消息处理器按新契约工作
# 对应文档: 萤石平台/通知链路与消息推送调研报告.md §一（消息推送服务契约要点）
#           https://open.ys7.com/help/5128
"""
import hmac
import hashlib
import json
from collections import deque
import pytest
from app import create_app


@pytest.fixture
def client():
    """创建测试客户端"""
    app = create_app()
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


@pytest.fixture(autouse=True)
def _isolated_inbox(tmp_path, monkeypatch):
    """收件箱隔离: 使用临时文件+空队列，避免污染真实运行时数据"""
    import app.services.notification_inbox as inbox
    monkeypatch.setattr(inbox, 'INBOX_FILE', str(tmp_path / 'inbox_test.json'))
    monkeypatch.setattr(inbox, '_inbox', deque(maxlen=200))
    yield


# === 新格式消息（header/body 契约） ===

NEW_ALARM_PAYLOAD = {
    "header": {
        "type": "ys.alarm",
        "deviceId": "CHANGE_ME_DEVICE_SERIAL",
        "channelNo": 1,
        "messageId": "5e57f239793f2b007fecb0de",
        "messageTime": 1754212000000,
    },
    "body": {
        "alarmId": "alarm-001",
        "alarmType": 10000,
        "describe": "移动侦测",
        "devSerial": "CHANGE_ME_DEVICE_SERIAL",
        "channel": 1,
        "channelName": "C6C",
        "location": "客厅",
        "alarmTime": 1754212000000,
        "pictureList": [{"url": "https://xxx/pic1.jpg", "isEncrypted": 0}],
    },
}

OLD_ALARM_PAYLOAD = {
    "type": "ys.alarm",
    "data": {
        "alarmType": 10000,
        "alarmTypeName": "人体感应事件",
        "deviceSerial": "CHANGE_ME_DEVICE_SERIAL",
        "channelNo": 1,
        "alarmTime": 1754212000000,
        "alarmPicUrl": "https://xxx/pic_old.jpg",
    },
}


# === 契约测试: 响应必须回显 messageId ===

def test_webhook_new_format_returns_message_id(client):
    """新格式 ys.alarm: 响应必须回显 header.messageId（契约核心）"""
    resp = client.post('/api/webhook/ezviz', json=NEW_ALARM_PAYLOAD)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("messageId") == "5e57f239793f2b007fecb0de"


def test_webhook_old_format_fallback(client):
    """旧格式 type/data: 兼容兜底，不崩溃"""
    resp = client.post('/api/webhook/ezviz', json=OLD_ALARM_PAYLOAD)
    assert resp.status_code == 200


def test_webhook_no_message_id_fallback(client):
    """无 messageId: 兜底 200 不崩溃（萤石判定失败后重试，重试端去重）"""
    payload = {"header": {"type": "ys.onoffline", "deviceId": "CHANGE_ME_DEVICE_SERIAL"}}
    resp = client.post('/api/webhook/ezviz', json=payload)
    assert resp.status_code == 200


def test_webhook_empty_body(client):
    """空消息/非JSON: 200 不崩溃"""
    resp = client.post('/api/webhook/ezviz', data='not-json', content_type='application/json')
    assert resp.status_code == 200


# === 签名验证 ===

def _sign(body: bytes, secret: str, timestamp: str) -> str:
    """按官方算法构造签名: Signature = hmac_sha1(Secret, Message + Timestamp)"""
    return hmac.new(secret.encode(), body + timestamp.encode(), hashlib.sha1).hexdigest()


def test_webhook_signature_valid(client, monkeypatch):
    """配置 secret 且签名正确: 200"""
    monkeypatch.setattr('config.Config.EZS_WEBHOOK_SECRET', 'mysecret')
    raw = json.dumps(NEW_ALARM_PAYLOAD, ensure_ascii=False).encode()
    resp = client.post(
        '/api/webhook/ezviz',
        data=raw,
        content_type='application/json',
        headers={"Signature": _sign(raw, 'mysecret', '1772435918362'), "t": "1772435918362"},
    )
    assert resp.status_code == 200
    assert resp.get_json().get("messageId") == "5e57f239793f2b007fecb0de"


def test_webhook_signature_invalid(client, monkeypatch):
    """配置 secret 且签名错误: 401"""
    monkeypatch.setattr('config.Config.EZS_WEBHOOK_SECRET', 'mysecret')
    raw = json.dumps(NEW_ALARM_PAYLOAD, ensure_ascii=False).encode()
    resp = client.post(
        '/api/webhook/ezviz',
        data=raw,
        content_type='application/json',
        headers={"Signature": "wrongsignature", "t": "1772435918362"},
    )
    assert resp.status_code == 401


def test_webhook_signature_disabled_no_secret(client):
    """未配置 secret: 跳过签名验证"""
    resp = client.post('/api/webhook/ezviz', json=NEW_ALARM_PAYLOAD)
    assert resp.status_code == 200


# === 消息处理器 ===

def test_process_message_new_format():
    """新格式分发: ys.alarm → 新版字段提取"""
    from app.services.webhook_handler import process_message, _extract_message
    msg_type, data, meta = _extract_message(NEW_ALARM_PAYLOAD)
    assert msg_type == "ys.alarm"
    assert data["describe"] == "移动侦测"
    assert meta["message_id"] == "5e57f239793f2b007fecb0de"
    process_message(NEW_ALARM_PAYLOAD)  # 不抛异常


def test_process_message_old_format():
    """旧格式兼容: type/data 解析"""
    from app.services.webhook_handler import _extract_message
    msg_type, data, meta = _extract_message(OLD_ALARM_PAYLOAD)
    assert msg_type == "ys.alarm"
    assert data["alarmTypeName"] == "人体感应事件"
    assert meta == {}


def test_handle_alarm_new_fields():
    """新版告警字段: describe/pictureList 提取"""
    from app.services.webhook_handler import handle_alarm_message
    result = handle_alarm_message(NEW_ALARM_PAYLOAD["body"], NEW_ALARM_PAYLOAD["header"])
    assert result["describe"] == "移动侦测"
    assert result["device_serial"] == "CHANGE_ME_DEVICE_SERIAL"
    assert result["picture_count"] == 1


def test_handle_alarm_old_fields_fallback():
    """旧版告警字段兜底: alarmTypeName/alarmPicUrl"""
    from app.services.webhook_handler import handle_alarm_message
    result = handle_alarm_message(OLD_ALARM_PAYLOAD["data"])
    assert result["describe"] == "人体感应事件"
    assert result["picture_count"] == 1


def test_message_type_mapping_onoffline():
    """ys.onoffline 与旧 ys.status 均映射到 status 处理器"""
    from app.services.webhook_handler import MESSAGE_HANDLERS, HANDLER_FUNCS
    assert MESSAGE_HANDLERS["ys.onoffline"] == "status"
    assert MESSAGE_HANDLERS["ys.status"] == "status"
    assert HANDLER_FUNCS["status"] is not None


def test_process_message_dedupe():
    """相同 messageId 去重: 第二次处理被跳过（不抛异常）"""
    from app.services.webhook_handler import process_message, _dedupe
    assert _dedupe("dup-id-123") is False   # 首次
    assert _dedupe("dup-id-123") is True    # 重复
    process_message(NEW_ALARM_PAYLOAD)      # 完整流程不抛异常


def test_unknown_type_no_crash():
    """未知消息类型: 仅日志，不抛异常"""
    from app.services.webhook_handler import process_message
    process_message({"header": {"type": "ys.unknown.type", "messageId": "x1"}, "body": {}})


# === 通知收件箱 ===

def test_send_app_push_writes_inbox():
    """send_app_push 写入管理端收件箱"""
    from app.services.notification_service import NotificationRequest, send_app_push
    from app.services.notification_inbox import list_notifications

    before = len(list_notifications())
    notif = NotificationRequest(
        event_id="evt-test-001",
        risk_level="I",
        risk_level_name="高危",
        touch_ground_part="头部",
        impact_velocity=2.5,
        fall_direction_cn="前向",
        location="客厅",
        timestamp="2026-08-03 12:00:00",
        medical_report="测试简报",
        recommendation="立即就医",
        emergency_contact_name="家属",
        emergency_contact_phone="test-phone",
        elderly_name="测试老人",
        home_address="测试地址",
    )
    result = send_app_push(notif)
    assert result.success is True
    assert result.channel == "app"
    assert len(list_notifications()) == before + 1
    latest = list_notifications()[0]
    assert latest["extras"]["event_id"] == "evt-test-001"
    assert latest["read"] is False


def test_notifications_api(client):
    """GET /api/notifications 返回收件箱"""
    resp = client.get('/api/notifications')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["code"] == 200
    assert "items" in data


# === 归档新字段 ===

def test_archive_new_fields_serializable():
    """FallEventRecord 新字段 capture_pic_url 可序列化/反序列化"""
    from dataclasses import asdict
    from app.services.fall_event_archive import FallEventRecord
    record = FallEventRecord(
        event_id="evt-pic-1",
        status="archived",
        created_at="2026-08-03T12:00:00+00:00",
        detection_confidence=0.9,
        detection_latency_ms=100,
        video_window_frames=32,
        video_window_duration_s=1.28,
        touch_ground_part="头部",
        fall_direction="forward",
        impact_velocity=2.5,
        body_tilt_angle=30,
        center_of_mass_velocity=1.5,
        risk_level="I",
        risk_level_name="高危",
        capture_pic_url="https://xxx/pic.jpg",
        capture_time="1754212000000",
    )
    d = asdict(record)
    assert d["capture_pic_url"] == "https://xxx/pic.jpg"
    # 反序列化兼容（旧 JSON 无新字段也能重建）
    d.pop("capture_pic_url")
    d.pop("capture_time")
    rebuilt = FallEventRecord(**d)
    assert rebuilt.capture_pic_url == ""
    assert rebuilt.capture_time == ""


# === replay 端点 ===

def test_replay_new_format(client):
    """replay 端点兼容新格式并回显 messageId"""
    resp = client.post('/api/webhook/ezviz/replay', json=NEW_ALARM_PAYLOAD)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["received_type"] == "ys.alarm"
    assert data.get("messageId") == "5e57f239793f2b007fecb0de"
