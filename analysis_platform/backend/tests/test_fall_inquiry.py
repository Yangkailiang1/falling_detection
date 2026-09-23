"""
# 语音问询调度器测试 [V7.3]
# 功能: 验证 Ⅰ/Ⅱ/Ⅲ 级问询调度（cancel/help/timeout）与通知触发
# 对应方案书: §7.2 语音交互确认、§8.1 响应机制
#
# 策略: 不跑 orchestrator 全流程（避免 LLM/网络），直接建事件 + start_inquiry；
#       timer 用 0.5s；monkeypatch dispatch_notifications 为记录型。
"""
import threading
import time
from datetime import datetime, timezone

import pytest
from app import create_app

from app.services.fall_event_archive import (
    FallEventRecord, create_event, get_event, _events,
)


@pytest.fixture
def client():
    """创建测试客户端"""
    app = create_app()
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


# === 辅助 ===

def _make_event(risk_level="II", countdown=30, event_id=None):
    """创建一个测试事件记录（默认 II 级）"""
    eid = event_id or f"test-{risk_level}-{int(time.time()*1000)}-{threading.get_ident()}"
    record = FallEventRecord(
        event_id=eid,
        status="reported",
        created_at=datetime.now(timezone.utc).isoformat(),
        detection_confidence=0.9,
        detection_latency_ms=100,
        video_window_frames=32,
        video_window_duration_s=1.28,
        touch_ground_part="head" if risk_level == "I" else "hip",
        fall_direction="forward",
        impact_velocity=2.5,
        body_tilt_angle=30,
        center_of_mass_velocity=1.5,
        risk_level=risk_level,
        risk_level_name={"I": "高危", "II": "中危", "III": "低危"}[risk_level],
        location="客厅",
        device_serial="CHANGE_ME_DEVICE_SERIAL",
        scenario_key="test",
        scenario_name="测试场景",
        description="测试",
        medical_report="测试医疗简报",
        report_recommendation="建议就医",
        countdown_seconds=countdown,
    )
    create_event(record)
    return eid


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """清空事件表与问询注册表；monkeypatch dispatch 为记录型"""
    _events.clear()
    from app.services import fall_inquiry
    fall_inquiry.clear_inquiries()

    # _dispatch_emergency 内部是运行时 import（from app.services.notification_service import ...），
    # monkeypatch 模块属性即可生效
    monkeypatch.setattr(
        "app.services.notification_service.dispatch_notifications",
        lambda notif, channels=None: {},
    )
    return


def _wait_timeout(eid, timeout=3.0):
    """等待事件 voice_confirm_status 变为非 pending"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rec = get_event(eid)
        if rec and rec.voice_confirm_status != "pending":
            return rec
        time.sleep(0.05)
    return get_event(eid)


def _wait_dispatch(eid, timeout=3.0):
    """等待异步紧急联络落地（status 变为 notified/archived）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rec = get_event(eid)
        if rec and rec.status in ("notified", "archived"):
            return rec
        time.sleep(0.05)
    return get_event(eid)


# === 用例 ===

def test_level_i_timeout_notify_all_channels(_clean_state):
    """Ⅰ级(头部) + 无回应 → 倒计时结束自动通知四通道"""
    from app.services.fall_inquiry import start_inquiry
    eid = _make_event(risk_level="I", countdown=0.5)
    start_inquiry(eid, "I", 0.5)
    rec = _wait_timeout(eid)
    assert rec.voice_confirm_status == "timeout"
    rec = _wait_dispatch(eid)
    assert rec.status in ("notified", "archived")


def test_level_i_cancel_no_notify(_clean_state):
    """Ⅰ级 + 我没事 → 取消告警、记录误报、无通知"""
    from app.services.fall_inquiry import start_inquiry, resolve_inquiry
    eid = _make_event(risk_level="I", countdown=10)
    start_inquiry(eid, "I", 10)
    result = resolve_inquiry(eid, "cancel", "我没事")
    assert result["transitioned"] is True
    assert result["voice_confirm_status"] == "cancelled"
    rec = get_event(eid)
    assert rec.status == "voice_cancelled"
    assert rec.feedback and rec.feedback["is_false_alarm"] is True
    time.sleep(0.2)  # 等 dispatch（不应发生）


def test_level_ii_cancel_no_notify(_clean_state):
    """Ⅱ级 + 我没事 → 取消、无通知"""
    from app.services.fall_inquiry import start_inquiry, resolve_inquiry
    eid = _make_event(risk_level="II", countdown=30)
    start_inquiry(eid, "II", 30)
    resolve_inquiry(eid, "cancel", "取消")
    rec = get_event(eid)
    assert rec.voice_confirm_status == "cancelled"
    time.sleep(0.2)


def test_level_ii_help_immediate_notify(_clean_state):
    """Ⅱ级 + 帮我呼叫 → 立即紧急联络（II 级三通道）"""
    from app.services.fall_inquiry import start_inquiry, resolve_inquiry
    eid = _make_event(risk_level="II", countdown=30)
    start_inquiry(eid, "II", 30)
    result = resolve_inquiry(eid, "help", "帮我呼叫")
    assert result["transitioned"] is True
    assert result["voice_confirm_status"] == "help_requested"
    rec = _wait_dispatch(eid)  # 等异步 dispatch 落地
    assert rec.status in ("notified", "archived")


def test_level_ii_timeout_auto_notify(_clean_state):
    """Ⅱ级 + 无回应 → 倒计时结束自动通知（II 级三通道）"""
    from app.services.fall_inquiry import start_inquiry
    eid = _make_event(risk_level="II", countdown=0.5)
    start_inquiry(eid, "II", 0.5)
    rec = _wait_timeout(eid)
    assert rec.voice_confirm_status == "timeout"


def test_level_iii_cancel_no_notify(_clean_state):
    """Ⅲ级 + 我没事 → 取消、无通知"""
    from app.services.fall_inquiry import start_inquiry, resolve_inquiry
    eid = _make_event(risk_level="III", countdown=60)
    start_inquiry(eid, "III", 60)
    resolve_inquiry(eid, "cancel", "我没事")
    assert get_event(eid).voice_confirm_status == "cancelled"
    time.sleep(0.2)


def test_level_iii_timeout_auto_notify(_clean_state):
    """Ⅲ级 + 无回应 → 倒计时结束自动通知（仅 APP 通道）"""
    from app.services.fall_inquiry import start_inquiry
    eid = _make_event(risk_level="III", countdown=0.5)
    start_inquiry(eid, "III", 0.5)
    rec = _wait_timeout(eid)
    assert rec.voice_confirm_status == "timeout"


def test_double_resolve_single_dispatch(_clean_state):
    """竞态: help 后 cancel → 只处理一次（CAS 保证单发）"""
    from app.services.fall_inquiry import start_inquiry, resolve_inquiry
    eid = _make_event(risk_level="II", countdown=30)
    start_inquiry(eid, "II", 30)
    r1 = resolve_inquiry(eid, "help")
    r2 = resolve_inquiry(eid, "cancel")   # 已被 help 抢先
    assert r1["transitioned"] is True
    assert r2["transitioned"] is False
    assert r2["voice_confirm_status"] == "help_requested"


def test_start_inquiry_idempotent(_clean_state):
    """start_inquiry 幂等: 重复调用不重复启动"""
    from app.services.fall_inquiry import start_inquiry
    eid = _make_event(risk_level="II", countdown=30)
    r1 = start_inquiry(eid, "II", 30)
    r2 = start_inquiry(eid, "II", 30)
    assert r1["started"] is True
    assert r2["started"] is False


def test_rearm_from_archive_restores(_clean_state):
    """服务重启恢复: inquiring+pending 事件重新挂倒计时"""
    from app.services.fall_inquiry import rearm_from_archive, _registry
    eid = _make_event(risk_level="II", countdown=0.5, event_id="rearm-test-1")
    from app.services.fall_event_archive import update_event
    update_event(eid, status="inquiring")   # 模拟重启前状态
    restored = rearm_from_archive()
    assert restored >= 1
    rec = _wait_timeout(eid)
    assert rec.voice_confirm_status == "timeout"


def test_voice_confirm_endpoint(client, _clean_state):
    """HTTP 端点: cancel → cancelled；非法 action → 400；非 pending 幂等"""
    eid = _make_event(risk_level="II", countdown=30)
    from app.services.fall_inquiry import start_inquiry
    start_inquiry(eid, "II", 30)

    # 非法 action
    resp = client.post(f'/api/fall-events/{eid}/voice/confirm', json={"action": "timeout"})
    assert resp.status_code == 400

    # cancel
    resp = client.post(f'/api/fall-events/{eid}/voice/confirm',
                       json={"action": "cancel", "recognized_text": "我没事"})
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data["voice_confirm_status"] == "cancelled"

    # 幂等: 已结束后再次 cancel
    resp = client.post(f'/api/fall-events/{eid}/voice/confirm', json={"action": "cancel"})
    assert resp.status_code == 200
    assert resp.get_json()["data"]["already_resolved"] is True
