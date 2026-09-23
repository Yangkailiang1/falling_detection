"""
# 跌倒事件路由
# 功能: 模拟跌倒检测、骨骼分析、事件归档、误报反馈等 API 端点
# 对应方案书: §3.1 事后分级干预与全闭环处置、§8.2 事件归档
"""
import os
from flask import Blueprint, request, send_file
from datetime import datetime, timezone
from app.utils.response import success_response, error_response, paginated_response
from app.services.fall_event_simulator import (
    simulate_fall_event, simulate_fall_event_custom, generate_scenarios_meta,
    FALL_SCENARIOS,
)
from app.services.fall_orchestrator import (
    process_fall_event, quick_simulate, batch_simulate,
)
from app.services.fall_event_archive import (
    get_event, list_events, submit_feedback, get_stats, FallEventRecord,
    update_event, add_timeline_entry, TimelineEntry,
    _events as archive_events,
)
from app.services.skeleton_analyzer import (
    analyze_static, TOUCH_GROUND_RISK_MAP, FALL_DIRECTION_MAP,
)
from app.services.capture_assets import persist_capture_pic, CAPTURES_DIR, CLIPS_DIR
from app.inference.inference_config import DEVICE_SERIAL, TEST_PHONE

fall_events_bp = Blueprint('fall_events', __name__)


# === 场景元信息 ===

@fall_events_bp.route('/fall-events/scenarios', methods=['GET'])
def list_scenarios():
    """
    获取所有可用的模拟跌倒场景列表
    返回: [{key, name, description, location, touch_ground_part, risk_level_expected}, ...]
    """
    scenarios = generate_scenarios_meta()
    risk_levels = {}
    for k, v in TOUCH_GROUND_RISK_MAP.items():
        risk_levels[k] = {
            "level": v["level"],
            "name": v["level_name"],
            "injury_types": v["injury_types"],
            "response": v["response"],
        }
    directions = {k: v for k, v in FALL_DIRECTION_MAP.items()}
    return success_response({
        "scenarios": scenarios,
        "risk_levels": risk_levels,
        "fall_directions": directions,
    })


# === 模拟跌倒事件 ===

@fall_events_bp.route('/fall-events/simulate', methods=['POST'])
def trigger_simulate():
    """
    模拟一次跌倒事件并执行全流程处理

    请求体:
    {
        "scenario_key": "hip_sideways",  // 可选, 不传则随机
        "device_serial": DEVICE_SERIAL,    // 可选
        "detection_confidence": 0.95,    // 可选
        "impact_velocity": 3.5,          // 可选
        "touch_ground_part": "head",     // 可选, 覆盖场景默认值
        "fall_direction": "forward",     // 可选
        "location": "客厅",             // 可选
        "skip_medical_report": false     // 可选
    }

    返回: 完整的 FallEventRecord, 包含时间线、骨骼分析、医疗报告、干预策略
    """
    try:
        body = request.get_json() or {}
        scenario_key = body.get('scenario_key')
        skip_report = body.get('skip_medical_report', False)

        if scenario_key == "custom" or body.get('touch_ground_part'):
            # 自定义参数事件
            event = simulate_fall_event_custom(
                touch_ground_part=body.get('touch_ground_part', 'hip'),
                fall_direction=body.get('fall_direction', 'sideways_right'),
                detection_confidence=body.get('detection_confidence', 0.90),
                impact_velocity=body.get('impact_velocity', 3.0),
                body_tilt_angle=body.get('body_tilt_angle', 45.0),
                com_velocity=body.get('com_velocity', 1.5),
                location=body.get('location', '客厅'),
                device_serial=body.get('device_serial', DEVICE_SERIAL),
            )
        else:
            event = simulate_fall_event(
                scenario_key=scenario_key,
                device_serial=body.get('device_serial', DEVICE_SERIAL),
                detection_confidence=body.get('detection_confidence'),
                impact_velocity=body.get('impact_velocity'),
                body_tilt_angle=body.get('body_tilt_angle'),
                com_velocity=body.get('com_velocity'),
                location=body.get('location'),
            )

        record = process_fall_event(event, skip_medical_report=skip_report)

        return success_response(_record_to_dict(record), "跌倒事件模拟完成")
    except Exception as e:
        return error_response(f"模拟失败: {str(e)}", 500)


@fall_events_bp.route('/fall-events/batch', methods=['POST'])
def trigger_batch_simulate():
    """
    批量模拟多个跌倒事件

    请求体:
    {
        "count": 5,
        "scenario_keys": ["head_forward", "hip_sideways"],  // 可选
        "skip_medical_report": false   // 可选, 默认 false (生成医疗报告)
    }
    """
    try:
        body = request.get_json() or {}
        count = body.get('count', 5)
        scenario_keys = body.get('scenario_keys')
        skip_report = body.get('skip_medical_report', False)

        results = batch_simulate(scenario_keys=scenario_keys, count=count,
                                 skip_medical_report=skip_report)
        return success_response({
            "total": len(results),
            "results": results,
        }, f"批量模拟完成，共 {len(results)} 个事件")
    except Exception as e:
        return error_response(f"批量模拟失败: {str(e)}", 500)


# === 事件查询 ===

@fall_events_bp.route('/fall-events', methods=['GET'])
def query_events():
    """
    查询跌倒事件列表

    参数:
        limit(query): 每页条数, 默认20
        offset(query): 偏移量, 默认0
        risk_level(query): 风险等级筛选 (I/II/III)
        status(query): 状态筛选
        days(query): 最近N天, 默认7
    """
    limit = request.args.get('limit', 20, type=int)
    offset = request.args.get('offset', 0, type=int)
    risk_level = request.args.get('risk_level')
    status = request.args.get('status')
    days = request.args.get('days', 7, type=int)

    items, total = list_events(
        limit=limit, offset=offset,
        risk_level=risk_level, status=status, days=days,
    )
    return paginated_response(
        [_record_to_dict(r) for r in items],
        total, offset // limit if limit > 0 else 0, limit
    )


@fall_events_bp.route('/fall-events/<event_id>', methods=['GET'])
def query_event_detail(event_id):
    """
    获取单个跌倒事件详情
    返回: 完整的 FallEventRecord 字典
    """
    record = get_event(event_id)
    if not record:
        return error_response(f"事件 {event_id} 不存在", 404)
    return success_response(_record_to_dict(record))


@fall_events_bp.route('/fall-events/<event_id>/capture-image', methods=['GET'])
def get_capture_image(event_id):
    """
    返回事件现场抓拍图 (本地持久化文件 backend/data/captures/<event_id>.jpg)。
    萤石签名 URL 24h 过期 → 优先返回本地文件, 不再依赖云端。
    """
    record = get_event(event_id)
    if not record:
        return error_response(f"事件 {event_id} 不存在", 404)
    # 1) 记录里的本地路径
    local = record.capture_pic_path
    if local and os.path.isfile(local):
        return send_file(local, mimetype="image/jpeg")
    # 2) 按事件ID查目录 (兼容历史记录未存路径但文件已生成)
    fallback = CAPTURES_DIR / f"{event_id}.jpg"
    if fallback.is_file():
        return send_file(str(fallback), mimetype="image/jpeg")
    return error_response("图片不存在或已过期", 404)


@fall_events_bp.route('/fall-events/<event_id>/clip', methods=['GET'])
def get_event_clip(event_id):
    """
    返回事件跌倒录像片段 (backend/data/clips/fall_<event_id前8位>_*.mp4)。

    问询开始时后台录制 RTSP 片段 (fall_inquiry), 录完写回 record.video_clip。
    优先返回归档路径, 兼容历史事件按文件名前缀匹配。
    """
    record = get_event(event_id)
    if not record:
        return error_response(f"事件 {event_id} 不存在", 404)
    # 1) 归档里记录的视频路径
    if record.video_clip and os.path.isfile(record.video_clip):
        return send_file(record.video_clip, mimetype="video/mp4")
    # 2) 按事件ID前缀匹配 clips 目录 (兼容历史记录未存路径但文件已生成)
    prefix = f"fall_{event_id[:8]}_"
    if CLIPS_DIR.exists():
        for f in CLIPS_DIR.iterdir():
            if f.name.startswith(prefix) and f.suffix == ".mp4":
                return send_file(str(f), mimetype="video/mp4")
    return error_response("事件无视频片段", 404)


@fall_events_bp.route('/fall-events/<event_id>/capture/refresh', methods=['POST'])
def refresh_capture_image(event_id):
    """
    重新抓拍现场图片 (旧事件云端签名 URL 过期后恢复用)。
    触发萤石抓拍 → 下载持久化本地 → 更新归档 → 返回新的图片 URL。
    """
    record = get_event(event_id)
    if not record:
        return error_response(f"事件 {event_id} 不存在", 404)
    try:
        from app.services.ezviz_capture import capture_device
        result = capture_device(record.device_serial, channel_no=1) or {}
        pic_url = result.get("pic_url", "")
        if not pic_url:
            return error_response("抓拍失败（设备可能离线）", 502)
        local_path = persist_capture_pic(event_id, pic_url)
        capture_time = datetime.now().astimezone().isoformat()
        update_event(event_id,
            capture_pic_url=pic_url,
            capture_time=capture_time,
            capture_pic_path=local_path)
        return success_response({
            "event_id": event_id,
            "capture_pic_url": f"/api/fall-events/{event_id}/capture-image",
            "capture_time": capture_time,
            "local_persisted": bool(local_path),
        }, "重新抓拍成功")
    except Exception as e:
        return error_response(f"重新抓拍失败: {str(e)[:100]}", 502)
    """
    获取事件的处理时间线
    返回: [{time, stage, label, detail, status, duration_ms}, ...]
    """
    record = get_event(event_id)
    if not record:
        return error_response(f"事件 {event_id} 不存在", 404)
    return success_response({
        "event_id": event_id,
        "status": record.status,
        "timeline": [t.__dict__ if hasattr(t, '__dict__') else t
                     for t in record.timeline],
    })


# === 统计分析 ===

@fall_events_bp.route('/fall-events/stats', methods=['GET'])
def query_stats():
    """
    获取归档统计信息
    返回: {total_events, by_risk_level, by_status, false_alarms}
    """
    return success_response(get_stats())


# === 误报反馈 ===

@fall_events_bp.route('/fall-events/<event_id>/feedback', methods=['POST'])
def submit_event_feedback(event_id):
    """
    提交误报反馈
    对应方案书: §8.2 误报反馈入口

    请求体:
    {
        "is_false_alarm": true,
        "comment": "老人只是在弯腰捡东西，未摔倒"
    }
    """
    try:
        body = request.get_json() or {}
        is_false = body.get('is_false_alarm', False)
        comment = body.get('comment', '')

        record = submit_feedback(event_id, is_false, comment)
        if not record:
            return error_response(f"事件 {event_id} 不存在", 404)
        return success_response({
            "event_id": event_id,
            "status": record.status,
            "feedback": record.feedback,
        }, "反馈已记录" if not is_false else "已标记为误报")
    except Exception as e:
        return error_response(f"提交反馈失败: {str(e)}", 500)


# === 快捷模拟 ===

@fall_events_bp.route('/fall-events/quick/<scenario_key>', methods=['POST'])
def quick_simulate_endpoint(scenario_key):
    """
    快捷模拟端点: POST /api/fall-events/quick/head_forward
    直接在URL中指定场景类型，一键模拟 + 处理
    """
    if scenario_key not in FALL_SCENARIOS:
        available = list(FALL_SCENARIOS.keys())
        return error_response(f"无效场景 '{scenario_key}'，可用: {', '.join(available)}", 400)

    skip_report = (request.args.get('report', 'true').lower() == 'false')
    try:
        record = quick_simulate(scenario_key=scenario_key, skip_medical_report=skip_report)
        return success_response(_record_to_dict(record),
            f"模拟完成: {record.scenario_name}")
    except Exception as e:
        return error_response(f"模拟失败: {str(e)}", 500)


# === 语音交互 ===

@fall_events_bp.route('/fall-events/<event_id>/voice/claim', methods=['POST'])
def voice_claim(event_id):
    """Claim an inquiring event for the single camera voice executor."""
    body = request.get_json() or {}
    executor_id = str(body.get('executor_id', '')).strip()
    from app.services.fall_event_archive import claim_voice_executor
    ok = claim_voice_executor(event_id, executor_id)
    return success_response(
        {"event_id": event_id, "claimed": ok},
        "语音事件已认领" if ok else "事件已被其他语音执行器认领",
    )


@fall_events_bp.route('/fall-events/<event_id>/voice/feedback/claim', methods=['POST'])
def voice_feedback_claim(event_id):
    """Claim a queued camera feedback message for serialized playback."""
    body = request.get_json() or {}
    executor_id = str(body.get('executor_id', '')).strip()
    from app.services.fall_event_archive import claim_voice_feedback
    text = claim_voice_feedback(event_id, executor_id)
    return success_response(
        {"event_id": event_id, "claimed": bool(text), "text": text or ""},
        "反馈已认领" if text else "暂无待播报反馈",
    )


@fall_events_bp.route('/fall-events/<event_id>/voice/feedback/ack', methods=['POST'])
def voice_feedback_ack(event_id):
    """Acknowledge that the camera finished playing a feedback message."""
    body = request.get_json() or {}
    executor_id = str(body.get('executor_id', '')).strip()
    from app.services.fall_event_archive import acknowledge_voice_feedback
    ok = acknowledge_voice_feedback(event_id, executor_id)
    return success_response(
        {"event_id": event_id, "played": ok},
        "反馈播报已确认" if ok else "反馈确认无效",
    )


@fall_events_bp.route('/fall-events/<event_id>/voice/confirm', methods=['POST'])
def voice_confirm_status(event_id):
    """
    语音问询结果上报（由前端 VoiceInteraction 在老人回应后调用）[V7.3]

    请求体: {"action": "cancel"|"help", "recognized_text": "我没事"}
      cancel — 老人说"我没事"/旁人"取消": 停倒计时、取消告警、记录误报、不通知
      help   — 老人说"帮我呼叫": 停倒计时、立即紧急联络（通知由调度器触发）

    注意:
      - timeout 由服务端调度器倒计时到期内部触发，不接受此 action（400）
      - 事件已结束（非 pending）时幂等返回 already_resolved，不覆写已归档状态
    """
    try:
        record = get_event(event_id)
        if not record:
            return error_response(f"事件 {event_id} 不存在", 404)

        body = request.get_json() or {}
        action = body.get('action', '')
        recognized_text = body.get('recognized_text', '')

        if action not in ("cancel", "help"):
            return error_response("action 仅支持 cancel / help（timeout 由服务端调度器触发）", 400)

        from app.services.fall_inquiry import resolve_inquiry
        result = resolve_inquiry(event_id, action, recognized_text)

        if not result.get("transitioned"):
            # 已结束/已被其他请求处理，幂等返回
            return success_response({
                "event_id": event_id,
                "already_resolved": True,
                "voice_confirm_status": result.get("voice_confirm_status", "unknown"),
            }, "事件已结束，忽略本次回应")

        # 重新读取最新状态（record 是请求时的旧引用）
        fresh = get_event(event_id)
        return success_response({
            "event_id": event_id,
            "voice_confirm_status": result["voice_confirm_status"],
            "status": fresh.status if fresh else record.status,
        }, "语音交互状态已更新")
    except Exception as e:
        return error_response(f"更新失败: {str(e)}", 500)


@fall_events_bp.route('/fall-events/<event_id>/voice/pause', methods=['POST'])
def voice_pause(event_id):
    """
    暂停语音问询倒计时（前端开始额外播报时调用）[V-fix]

    播报时间不计入回应窗口；服务端定时器暂停，剩余时间由 resume 恢复。
    """
    from app.services.fall_inquiry import pause_inquiry
    result = pause_inquiry(event_id)
    return success_response(result, "倒计时已暂停" if result.get("ok") else "无需暂停")


@fall_events_bp.route('/fall-events/<event_id>/voice/resume', methods=['POST'])
def voice_resume(event_id):
    """
    恢复/开始语音问询倒计时（前端播完一条语音后调用）[V-fix]

    首条播完调用 → 从完整 countdown 开始（修复 Ⅰ级提前超时）；
    额外播报播完调用 → 按暂停前剩余恢复。
    """
    from app.services.fall_inquiry import resume_inquiry
    result = resume_inquiry(event_id)
    return success_response(result, "倒计时已恢复")


@fall_events_bp.route('/fall-events/<event_id>/voice/failure', methods=['POST'])
def voice_failure(event_id):
    """摄像头首句问询连续播报失败时触发安全通知并结束流程。"""
    body = request.get_json() or {}
    detail = str(body.get("detail", "摄像头问询播报失败")).strip()[:240]
    from app.services.fall_inquiry import fail_inquiry
    result = fail_inquiry(event_id, detail)
    return success_response(result, "问询失败，已启动安全通知" if result.get("ok") else "事件已结束")


@fall_events_bp.route('/fall-events/<event_id>/voice/timeout', methods=['POST'])
def voice_force_timeout(event_id):
    """
    前端本地倒计时归零时兜底触发服务端超时（幂等）[V-fix]

    服务端定时器是权威源，正常会先到期；此处兜底保证紧急联络不遗漏。
    """
    from app.services.fall_inquiry import force_timeout_inquiry
    result = force_timeout_inquiry(event_id)
    return success_response(result, "已触发超时" if result.get("ok") else "事件已结束")


# === 通知触发 ===

@fall_events_bp.route('/fall-events/<event_id>/notify', methods=['POST'])
def trigger_notification(event_id):
    """
    手动触发或重新发送通知
    请求体: {"channels": ["sms", "app", "phone", "call_120"]}

    当语音问询超时或老人确认求助后，可通过此端点触发真实的短信/APP/电话通知
    """
    try:
        record = get_event(event_id)
        if not record:
            return error_response(f"事件 {event_id} 不存在", 404)

        body = request.get_json() or {}
        channels = body.get('channels')

        if not record.risk_level:
            channels = channels or ["app"]

        from app.services.notification_service import (
            NotificationRequest, dispatch_notifications
        )
        notif = NotificationRequest(
            event_id=event_id,
            risk_level=record.risk_level or "II",
            risk_level_name=record.risk_level_name or "中危",
            touch_ground_part=record.touch_ground_part or "unknown",
            impact_velocity=record.impact_velocity or 0,
            fall_direction_cn=record.fall_direction or "未知",
            location=record.location or "未知",
            timestamp=record.created_at,
            medical_report=record.medical_report or "",
            recommendation=record.report_recommendation or "",
            emergency_contact_name=body.get('contact_name', '家属（模拟）'),
            emergency_contact_phone=body.get('contact_phone', '138****0000'),
            elderly_name=body.get('elderly_name', '老人（模拟）'),
            home_address=body.get('home_address', '模拟家庭地址'),
        )
        results = dispatch_notifications(notif, channels=channels)

        return success_response({
            "event_id": event_id,
            "channels": list(results.keys()),
            "results": {
                k: {"success": v.success, "message": v.message}
                for k, v in results.items()
            },
        }, "通知已发送")
    except Exception as e:
        return error_response(f"通知发送失败: {str(e)}", 500)


# === 辅助函数 ===

def _record_to_dict(record: FallEventRecord) -> dict:
    """将 FallEventRecord 转换为前端友好的字典"""
    timeline_list = []
    for t in record.timeline:
        if isinstance(t, dict):
            timeline_list.append(t)
        else:
            timeline_list.append({
                "time": t.time,
                "stage": t.stage,
                "label": t.label,
                "detail": t.detail,
                "status": t.status,
                "duration_ms": t.duration_ms,
            })

    return {
        "event_id": record.event_id,
        "status": record.status,
        "created_at": record.created_at,
        # 检测参数
        "detection": {
            "confidence": record.detection_confidence,
            "latency_ms": record.detection_latency_ms,
            "video_window_frames": record.video_window_frames,
            "video_window_duration_s": record.video_window_duration_s,
        },
        # 骨骼分析
        "skeleton_analysis": {
            "touch_ground_part": record.touch_ground_part,
            "fall_direction": record.fall_direction,
            "impact_velocity": record.impact_velocity,
            "body_tilt_angle": record.body_tilt_angle,
            "center_of_mass_velocity": record.center_of_mass_velocity,
        },
        # 风险等级
        "risk": {
            "level": record.risk_level,
            "level_name": record.risk_level_name,
            "likely_injury_types": record.likely_injury_types,
        },
        # 医疗报告
        "medical_report": {
            "full_text": record.medical_report,
            "recommendation": record.report_recommendation,
        },
        # 响应策略
        "response": {
            "strategy": record.response_strategy,
            "countdown_seconds": record.countdown_seconds,
            "voice_confirm_status": record.voice_confirm_status,
        },
        # 通知状态
        "notification": record.notification_status,
        # 事件上下文
        "context": {
            "location": record.location,
            "device_serial": record.device_serial,
            "scenario_key": record.scenario_key,
            "scenario_name": record.scenario_name,
            "description": record.description,
        },
        # 时间线
        "timeline": timeline_list,
        # 反馈
        "feedback": record.feedback,
        # 现场图片 (V7.2) — 本地已持久化时返回相对 URL(不依赖会过期的萤石签名) [V9.4]
        "capture_pic_url": _resolve_capture_pic_url(record),
        "capture_time": record.capture_time,
        # 现场录像 (V9.3) — 问询录制片段, 网页端回放 [2026-08-12]
        "video_url": _resolve_video_url(record),
    }


def _resolve_capture_pic_url(record: FallEventRecord) -> str:
    """
    决定事件图片 URL: 本地已持久化 → 返回相对路径(经 vite proxy 访问后端); 否则返回云端签名 URL。
    云端签名 URL 24h 过期(403), 本地文件永久可用。
    """
    if record.capture_pic_path and os.path.isfile(record.capture_pic_path):
        return f"/api/fall-events/{record.event_id}/capture-image"
    if (CAPTURES_DIR / f"{record.event_id}.jpg").is_file():
        return f"/api/fall-events/{record.event_id}/capture-image"
    return record.capture_pic_url


def _resolve_video_url(record: FallEventRecord) -> str:
    """
    决定事件录像 URL: 归档 record.video_clip 或按事件ID前缀匹配 clips 目录,
    返回相对路径(经 vite proxy 访问 WSL 后端 /clip 端点); 无录像返回空串。
    与 standalone _clip_video_url 逻辑对齐。
    """
    if record.video_clip and os.path.isfile(record.video_clip):
        return f"/api/fall-events/{record.event_id}/clip"
    if record.event_id:
        prefix = f"fall_{record.event_id[:8]}_"
        try:
            if CLIPS_DIR.exists():
                for f in CLIPS_DIR.iterdir():
                    if f.name.startswith(prefix) and f.suffix == ".mp4":
                        return f"/api/fall-events/{record.event_id}/clip"
        except Exception:
            pass
    return ""
