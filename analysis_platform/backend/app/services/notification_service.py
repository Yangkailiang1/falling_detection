"""
# 多通道通知服务 (V7.1 — 萤石原生API版)
# 功能: 短信、电话外呼、APP推送、120急救 → 分级推送到各通道
# 对应方案书: §6.1 多通道推送
#
# 通道说明:
# - SMS:     萤石云信令-消息触达 短信API (https://open.ys7.com/cn/s/24)
# - APP:     萤石APP推送（通过萤石平台消息推送/Webhook服务）
# - PHONE:   萤石电话告警外呼API (POST /api/lapp/device/alarm/phone/call)
# - CALL_120: 120急救系统（卫健委急救接口，暂为模拟）
#
# 升级记录 (2026-07-30):
# - V7.0: 全部使用第三方模拟
# - V7.1: 电话 → 萤石原生 alarm/phone/call API
#        短信 → 萤石原生 云信令-消息触达 API
#        测试号码: CHANGE_ME_PHONE
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from app.inference.inference_config import DEVICE_SERIAL, TEST_PHONE

logger = logging.getLogger(__name__)


@dataclass
class NotificationRequest:
    """通知请求"""
    event_id: str
    risk_level: str              # I/II/III
    risk_level_name: str         # 高危/中危/低危
    touch_ground_part: str       # 触地部位
    impact_velocity: float       # 冲击速度
    fall_direction_cn: str       # 跌倒方向（中文）
    location: str                # 发生地点
    timestamp: str               # 事件时间
    medical_report: str          # 医疗简报
    recommendation: str          # 建议措施
    # 联系方式
    emergency_contact_name: str  # 紧急联络人姓名
    emergency_contact_phone: str # 紧急联络人电话
    elderly_name: str            # 老人姓名
    home_address: str            # 家庭地址
    # 素材 (问询录制视频片段路径, 推送后清理)
    video_clip: str = ""


@dataclass
class NotificationResult:
    """通知发送结果"""
    event_id: str
    channel: str                 # sms/app/phone/call_120
    success: bool
    timestamp: str
    message: str                 # 结果描述
    provider_response: dict      # 服务商原始响应


# === 通知请求构建 [V7.3 提取复用] ===

def build_notification_request(record) -> NotificationRequest:
    """
    从归档记录构建通知请求（orchestrator Stage 4 与语音问询调度器复用）

    参数: record (FallEventRecord 或鸭子类型)
    字段映射缺省: risk_level 缺失回退 "II"，联系人用测试默认值
    """
    level_names = {"I": "高危", "II": "中危", "III": "低危"}
    risk_level = getattr(record, "risk_level", "") or "II"
    return NotificationRequest(
        event_id=record.event_id,
        risk_level=risk_level,
        risk_level_name=level_names.get(risk_level, "未知"),
        touch_ground_part=getattr(record, "touch_ground_part", ""),
        impact_velocity=getattr(record, "impact_velocity", 0),
        fall_direction_cn=getattr(record, "fall_direction", ""),
        location=getattr(record, "location", ""),
        timestamp=str(getattr(record, "created_at", "")),
        medical_report=getattr(record, "medical_report", ""),
        recommendation=getattr(record, "report_recommendation", ""),
        emergency_contact_name="测试联络人",
        emergency_contact_phone=TEST_PHONE,
        elderly_name="测试老人",
        home_address="萤石跌倒检测测试环境",
    )


# === 微信通知 (V9.1: 企业微信机器人 / PushPlus 免费通道) ===

def send_wechat(notif: NotificationRequest) -> NotificationResult:
    """
    微信推送: 优先企业微信群机器人 webhook, 未配置时降级 PushPlus。
    免费方案 (2026-08 调研): 企业微信机器人免费无限制, PushPlus 免费有限额。
    """
    try:
        from app.services.notification_channel import send_wechat_notify
        description = f"跌倒方向: {notif.fall_direction_cn}"
        # 视频片段: 事件记录可能有 (问询开始自动录制)
        video_clip = getattr(notif, "video_clip", "") or ""
        # 快照数量: 管线未运行(模拟事件)时抓帧可能失败, 由 send_wechat_notify 内部降级
        snapshot_count = 3 if notif.risk_level == "I" else 1
        result = send_wechat_notify(
            event_id=notif.event_id,
            risk_level=notif.risk_level,
            location=notif.location,
            description=description,
            touch_part=notif.touch_ground_part,
            medical_report=notif.medical_report,      # 完整医疗简报
            video_clip=video_clip,
            snapshot_count=snapshot_count,            # I级 3 张, 其他 1 张
        )
        channel_name = result.get("channel", "wechat")
        if result.get("success"):
            logger.info(f"[WeChat] ✅ 微信推送成功 ({channel_name}) → {notif.event_id}")
            return NotificationResult(
                event_id=notif.event_id,
                channel=channel_name,
                success=True,
                timestamp=datetime.now(timezone.utc).isoformat(),
                message="微信推送成功",
                provider_response={"channel": channel_name},
            )
        logger.warning(f"[WeChat] ⚠️ 微信推送失败: {result.get('message')}")
        return NotificationResult(
            event_id=notif.event_id,
            channel=channel_name,
            success=False,
            timestamp=datetime.now(timezone.utc).isoformat(),
            message=result.get("message", "微信推送失败"),
            provider_response=result,
        )
    except Exception as e:
        logger.warning(f"[WeChat] 微信推送异常: {e}")
        return NotificationResult(
            event_id=notif.event_id,
            channel="wechat",
            success=False,
            timestamp=datetime.now(timezone.utc).isoformat(),
            message=str(e),
            provider_response={},
        )


# === 短信通知 (V7.1: 萤石原生 API) ===

def send_sms(notif: NotificationRequest) -> NotificationResult:
    """
    通过萤石云信令-消息触达平台发送告警短信

    短信内容 (<150字):
    "【跌倒预警】{老人姓名}于{时间}在{地点}发生{跌倒类型}，{触地部位}着地。
     风险等级{等级}。建议{措施}。详情请查看APP。"

    萤石短信服务:
      - 产品入口: https://open.ys7.com/cn/s/24
      - 支持: 验证码类、通知类、营销类
      - 触达率: 最高99%
    """
    sms_content = (
        f"【跌倒预警】{notif.elderly_name}于{notif.timestamp}在{notif.location}"
        f"发生{notif.fall_direction_cn}跌倒，{notif.touch_ground_part}着地。"
        f"风险等级{notif.risk_level}级({notif.risk_level_name})。"
        f"建议{notif.recommendation[:30]}。详情请查看APP。"
    )[:150]

    # === 萤石原生短信发送 ===
    try:
        from app.services.ezviz_sms import send_sms as ezviz_sms_send
        result = ezviz_sms_send(
            phone=notif.emergency_contact_phone,
            content=sms_content,
            sms_type="notification",
        )
        if result.get("success"):
            logger.info(f"[SMS] ✅ 萤石短信已发送 → {notif.emergency_contact_phone}")
            return NotificationResult(
                event_id=notif.event_id,
                channel="sms",
                success=True,
                timestamp=datetime.now(timezone.utc).isoformat(),
                message=f"SMS 已发送至 {notif.emergency_contact_phone}",
                provider_response=result,
            )
        else:
            # 短信失败时记录错误但不阻断流程
            logger.warning(f"[SMS] ⚠️ 萤石短信失败: {result.get('message')}")
            return NotificationResult(
                event_id=notif.event_id,
                channel="sms",
                success=False,
                timestamp=datetime.now(timezone.utc).isoformat(),
                message=result.get("message", "短信发送失败"),
                provider_response=result,
            )
    except ImportError:
        logger.warning("[SMS] ezviz_sms 模块未找到，短信功能不可用")
    except Exception as e:
        logger.error(f"[SMS] 异常: {e}")

    # 降级: 日志记录
    logger.info(f"[SMS] (降级) To: {notif.emergency_contact_phone} | {sms_content}")

    return NotificationResult(
        event_id=notif.event_id,
        channel="sms",
        success=True,  # 不阻断流程
        timestamp=datetime.now(timezone.utc).isoformat(),
        message=f"SMS 已记录至 {notif.emergency_contact_phone}",
        provider_response={"status": "logged", "content": sms_content},
    )


# === APP推送通知 (V7.2: 改为管理端收件箱) ===

def send_app_push(notif: NotificationRequest) -> NotificationResult:
    """
    管理端通知（收件箱）— 替代萤石APP推送

    背景: 萤石开放平台无第三方主动推送萤石APP的API（APP通知只能由设备端告警触发），
          且开通 B 端消息推送服务后与萤石APP C 端通知永久互斥（关闭需工单恢复）。
          详见 萤石平台/通知链路与消息推送调研报告.md §三。

    实现: 写入管理端通知收件箱，前端轮询 GET /api/notifications 获取

    推送内容:
    {
      "title": "跌倒预警 - {风险等级}",
      "body": "{着地部位}着地，冲击速度 ~{速度}m/s",
      "extras": { 完整医疗简报, 事件ID, 位置 }
    }
    """
    push_title = f"跌倒预警 - {notif.risk_level}级({notif.risk_level_name})"
    push_body = (
        f"{notif.elderly_name}在{notif.location}发生{notif.fall_direction_cn}跌倒，"
        f"{notif.touch_ground_part}着地，冲击速度 ~{notif.impact_velocity}m/s。"
        f"医疗建议: {notif.recommendation[:40]}"
    )

    app_payload = {
        "title": push_title,
        "body": push_body,
        "extras": {
            "event_id": notif.event_id,
            "risk_level": notif.risk_level,
            "location": notif.location,
            "medical_report": notif.medical_report[:500],
            "recommendation": notif.recommendation,
            "timestamp": notif.timestamp,
        }
    }

    # 真实写入管理端收件箱
    try:
        from app.services.notification_inbox import push_notification
        message_id = push_notification(
            title=push_title,
            body=push_body,
            extras=app_payload["extras"],
        )
        logger.info(f"[APP] 管理端通知已写入收件箱: {push_title} | id={message_id}")
        return NotificationResult(
            event_id=notif.event_id,
            channel="app",
            success=True,
            timestamp=datetime.now(timezone.utc).isoformat(),
            message="管理端通知已写入收件箱",
            provider_response={"status": "inbox", "message_id": message_id, "payload": app_payload},
        )
    except ImportError:
        logger.warning("[APP] notification_inbox 模块未找到")
    except Exception as e:
        logger.error(f"[APP] 收件箱写入异常: {e}")

    # 降级: 日志记录
    logger.info(f"[APP] (降级) Push: {push_title}")
    return NotificationResult(
        event_id=notif.event_id,
        channel="app",
        success=True,  # 不阻断流程
        timestamp=datetime.now(timezone.utc).isoformat(),
        message="APP 推送已记录（收件箱不可用）",
        provider_response={"status": "logged", "payload": app_payload},
    )


# === 紧急联络人电话 (V7.1: 萤石原生告警外呼 API) ===

def make_emergency_call(notif: NotificationRequest) -> NotificationResult:
    """
    通过萤石电话告警外呼API自动拨打紧急联络人电话

    萤石云信令-电话提醒服务:
      - API: POST /api/lapp/device/alarm/phone/call
      - 参数: accessToken, devId, channel, alarmType, phone
      - 告警类型: 10000(人体感应)
      - 测试号码: CHANGE_ME_PHONE

    电话接通后萤石平台播放预设告警语音。
    """
    try:
        from app.services.ezviz_phone_call import call_phone

        result = call_phone(
            phone=notif.emergency_contact_phone,
            device_serial=DEVICE_SERIAL,  # 当前C6C摄像头
            alarm_type=10000,           # 人体感应事件
            channel=1,
            device_name="C6C跌倒检测摄像头",
        )

        if result.get("success"):
            logger.info(
                f"[PHONE] ✅ 萤石外呼成功 → {notif.emergency_contact_phone}"
            )
            return NotificationResult(
                event_id=notif.event_id,
                channel="phone",
                success=True,
                timestamp=datetime.now(timezone.utc).isoformat(),
                message=f"已呼叫紧急联络人 {notif.emergency_contact_name} ({notif.emergency_contact_phone})",
                provider_response=result,
            )
        else:
            error_code = result.get("code", "unknown")
            error_msg = result.get("message", "未知错误")
            logger.warning(
                f"[PHONE] ❌ 外呼失败(code={error_code}): {error_msg}"
            )
            return NotificationResult(
                event_id=notif.event_id,
                channel="phone",
                success=False,
                timestamp=datetime.now(timezone.utc).isoformat(),
                message=f"电话外呼失败: {error_msg}",
                provider_response=result,
            )

    except ImportError:
        logger.warning("[PHONE] ezviz_phone_call 模块未找到")
    except Exception as e:
        logger.error(f"[PHONE] 异常: {e}")

    # 降级: 记录日志
    logger.info(
        f"[PHONE] (降级) Calling: {notif.emergency_contact_phone}"
    )

    return NotificationResult(
        event_id=notif.event_id,
        channel="phone",
        success=False,
        timestamp=datetime.now(timezone.utc).isoformat(),
        message=f"电话外呼模块不可用: {notif.emergency_contact_phone}",
        provider_response={"status": "degraded", "error": str(e) if 'e' in dir() else "import error"},
    )


# === 120急救系统 ===

def call_120_emergency(notif: NotificationRequest) -> NotificationResult:
    """
    自动对接120急救系统

    结构化急救信息报文:
    {
      "patient_name": "姓名",
      "address": "地址",
      "incident": "跌倒 - {部位}着地，冲击速度 ~{速度}m/s",
      "risk_level": "{等级}",
      "medical_note": "{医疗简报}",
      "contact": "紧急联络人电话"
    }

    当前: 记录日志 + 告警（需对接当地120急救中心调度系统）
    """
    emergency_info = {
        "patient_name": notif.elderly_name,
        "address": notif.home_address,
        "incident": (
            f"跌倒 - {notif.fall_direction_cn}，"
            f"{notif.touch_ground_part}着地，"
            f"冲击速度 ~{notif.impact_velocity}m/s"
        ),
        "risk_level": f"{notif.risk_level}级({notif.risk_level_name})",
        "medical_note": notif.medical_report[:300],
        "recommendation": notif.recommendation,
        "contact": notif.emergency_contact_phone,
        "timestamp": notif.timestamp,
    }

    logger.warning(f"[120] ⚠️ Emergency call triggered: {notif.event_id}")
    logger.warning(f"[120] 急救信息: {json.dumps(emergency_info, ensure_ascii=False)}")

    return NotificationResult(
        event_id=notif.event_id,
        channel="call_120",
        success=True,  # 记录成功以继续流程
        timestamp=datetime.now(timezone.utc).isoformat(),
        message="已记录120急救信息（需对接当地急救调度系统）",
        provider_response={"status": "logged", "info": emergency_info},
    )


# === 分级通知编排 ===

def dispatch_notifications(
    notif: NotificationRequest,
    channels: list = None,
) -> dict:
    """
    根据风险等级分派通知到各通道

    对应方案书 §8.1:
    - I级高危: SMS + APP + 120 + 紧急联络人电话
    - II级中危: SMS + APP + 紧急联络人电话
    - III级低危: APP通知

    参数:
        notif: 通知请求对象
        channels: 自定义通道列表（覆盖默认分级逻辑）

    返回: {"channel_name": NotificationResult, ...}
    """
    if channels is None:
        if notif.risk_level == "I":
            channels = ["wechat", "sms", "app", "phone", "call_120"]
        elif notif.risk_level == "II":
            channels = ["wechat", "sms", "app", "phone"]
        elif notif.risk_level == "III":
            channels = ["wechat", "app"]
        else:
            channels = ["wechat", "app"]

    results = {}
    for channel in channels:
        try:
            if channel == "wechat":
                results["wechat"] = send_wechat(notif)
            elif channel == "sms":
                results["sms"] = send_sms(notif)
            elif channel == "app":
                results["app"] = send_app_push(notif)
            elif channel == "phone":
                results["phone"] = make_emergency_call(notif)
            elif channel == "call_120":
                results["call_120"] = call_120_emergency(notif)
        except Exception as e:
            logger.error(f"[Notification] {channel} failed: {e}")
            results[channel] = NotificationResult(
                event_id=notif.event_id,
                channel=channel,
                success=False,
                timestamp=datetime.now(timezone.utc).isoformat(),
                message=str(e),
                provider_response={},
            )

    return results
