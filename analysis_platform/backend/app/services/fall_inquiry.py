"""
# 语音问询调度器 [V7.3]
# 功能: Ⅰ/Ⅱ/Ⅲ级跌倒事件的倒计时问询调度 + 回应解析 + 紧急联络触发
# 对应方案书: §7.2 语音交互确认、§8.1 响应机制
#
# 分级倒计时: Ⅰ级(头部/脊柱) 10s / Ⅱ级(髋部/肩部) 30s / Ⅲ级(手/肘/膝) 60s
# 老人回应:
#   "我没事" → cancel   → 取消告警，记录误报，不通知
#   "帮我呼叫" → help  → 立即紧急联络
#   无回应    → timeout → 倒计时结束自动紧急联络
#
# 架构: 服务端调度器是状态机唯一事实源（倒计时 + 通知触发），
#       前端是播报/监听执行器（WebRTC 注入 TTS + STT）。
#       状态迁移通过 archive.transition_voice_status CAS 保证原子性（防竞态双发通知）。
"""
import os
import threading
import logging
import time as _time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# 安全兜底宽限: 页面未打开/前端从未上报时，问询在 countdown + 该宽限后兜底超时。
# 足够大以吸收前端"打开详情页 → 预热摄像头 → 播完首条"的耗时，避免与前端抢时间。
INQUIRY_WAIT_GRACE = 60.0

# event_id -> {"timer": threading.Timer, "risk_level": str, "countdown": int,
#              "remaining": float|None, "expires_at": float|None, "state": str}
# state: waiting_begin(等前端播完首条)/ counting(倒计时中)/ paused(播报中暂停)
_registry: dict = {}
_lock = threading.Lock()

# event_id -> video_clip_path (问询开始时后台录制, 推送时取出)
_clip_registry: dict = {}

# 已触发过录制的 event_id 集合 (幂等: 编排/问询多处可能调用, 每个事件只录一次) [2026-08-12]
_recorded_events: set = set()


def record_event_clip(event_id: str, seconds: int = 12):
    """每个事件只触发一次后台 RTSP 视频录制（幂等）。

    **为什么**: 此前录制只在 start_inquiry 里触发, 若问询未启动(非 pending)事件就没有录像;
    且编排 Stage 4 与问询都可能调用。统一走本入口 + 去重, 保证所有事件都尝试录制。
    """
    if not event_id or event_id in _recorded_events:
        return
    _recorded_events.add(event_id)
    _record_clip_async(event_id, seconds=seconds)


def _record_clip_async(event_id: str, seconds: int = 12):
    """问询开始即后台录制 RTSP 视频片段, 完成后存 _clip_registry + 写回归档。"""
    def _worker():
        try:
            from app.services.capture_assets import record_clip
            path = record_clip(seconds, prefix=f"fall_{event_id[:8]}")
            if path:
                _clip_registry[event_id] = path
                # [V9.3] 视频写回归档, 供小程序"查看实时画面"回放 (推送后不再删除)
                try:
                    from app.services.fall_event_archive import update_event
                    update_event(event_id, video_clip=path)
                except Exception as e:
                    logger.warning(f"[Inquiry] 视频写回归档失败: {event_id} | {e}")
                logger.info(f"[Inquiry] 视频片段就绪: {event_id} → {path}")
        except Exception as e:
            logger.warning(f"[Inquiry] 视频录制失败: {event_id} | {e}")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def _pop_clip(event_id: str) -> str:
    """取出事件视频片段路径（取出即移除）。"""
    return _clip_registry.pop(event_id, "")


def _send_clip_when_ready(event_id: str, wait_seconds: int = 25):
    """视频未就绪时异步补发: 等 _clip_registry 出现该事件视频后推送到企业微信。
    [2026-08-13] 同时检查事件归档里的 video_clip(管线缓冲录制, _dump_fall_clip 写回归档),
    RTSP 录制失败时也能补发缓冲录像。"""
    def _worker():
        import time as _t
        from app.services.fall_event_archive import get_event
        waited = 0
        clip = ""
        while waited < wait_seconds:
            if event_id in _clip_registry:
                clip = _clip_registry.pop(event_id, "")
                break
            # 归档里已有管线缓冲录好的视频 → 直接用它
            rec = get_event(event_id)
            if rec:
                _c = getattr(rec, "video_clip", "") or ""
                if _c and os.path.exists(_c):
                    clip = _c
                    break
            _t.sleep(1)
            waited += 1
        if not clip and event_id in _clip_registry:
            clip = _clip_registry.pop(event_id, "")
        if clip and os.path.exists(clip):
            try:
                from app.services.notification_channel import send_wecom_file
                send_wecom_file(clip)
            except Exception as e:
                logger.warning(f"[Inquiry] 视频补发失败: {e}")
            # [V9.3] 视频保留供小程序回放, 不再删除文件
    threading.Thread(target=_worker, daemon=True).start()


def _arm_timer(event_id: str, seconds: float) -> None:
    """创建并启动倒计时定时器（自动取消旧定时器），记录单调时钟到期时刻。"""
    entry = _registry.get(event_id)
    if not entry:
        return
    old = entry.get("timer")
    if old:
        old.cancel()
    seconds = max(0.1, float(seconds))
    timer = threading.Timer(seconds, _on_timeout, args=(event_id,))
    timer.daemon = True
    entry["timer"] = timer
    entry["remaining"] = seconds
    entry["expires_at"] = _time.monotonic() + seconds
    entry["state"] = "counting"
    timer.start()


def start_inquiry(event_id: str, risk_level: str, countdown_seconds: int) -> dict:
    """
    启动语音问询（幂等）

    Ⅰ/Ⅱ/Ⅲ 级统一入口。倒计时改为"前端播完首条后上报 resume 才真正开始"（修复 Ⅰ级提前超时）：
    - 此处只挂安全兜底定时器 countdown + INQUIRY_WAIT_GRACE（页面未打开/从未上报时兜底超时）；
    - 前端首条播完后调 resume_inquiry → 从完整 countdown 开始倒计时；
    - 额外播报（第2/3条/请再说一次）期间前端调 pause_inquiry / resume_inquiry 暂停/恢复。
    期间老人回应由 resolve_inquiry() 处理。
    """
    from app.services.fall_event_archive import get_event, add_timeline_entry, TimelineEntry

    with _lock:
        if event_id in _registry:
            return {"started": False, "event_id": event_id, "reason": "already_running"}

        record = get_event(event_id)
        if not record or record.voice_confirm_status != "pending":
            return {"started": False, "event_id": event_id, "reason": "not_pending"}

        safety = max(0.1, float(countdown_seconds)) + INQUIRY_WAIT_GRACE
        timer = threading.Timer(safety, _on_timeout, args=(event_id,))
        timer.daemon = True
        _registry[event_id] = {
            "timer": timer,
            "risk_level": risk_level,
            "countdown": countdown_seconds,
            "remaining": safety,
            "expires_at": _time.monotonic() + safety,
            "state": "waiting_begin",
        }
        timer.start()

    add_timeline_entry(event_id, TimelineEntry(
        time=datetime.now(timezone.utc).isoformat(),
        stage="inquiry",
        label="语音问询",
        detail=f"{risk_level}级风险，{countdown_seconds}s 回应窗口已就绪（前端播完首条后开始），等待老人回应",
        status="pending",
        duration_ms=0,
    ))
    # 问询开始即后台录制视频片段（供告警推送附带 / 小程序+网页回放）
    record_event_clip(event_id, seconds=12)
    logger.info(f"[Inquiry] 启动问询: {event_id} | {risk_level}级 | {countdown_seconds}s | 等待前端 begin")
    return {"started": True, "event_id": event_id, "countdown": countdown_seconds}


def pause_inquiry(event_id: str) -> dict:
    """
    暂停倒计时（前端开始额外播报时调用，幂等）。

    播报时间不计入回应窗口；剩余时间按单调时钟结算。
    """
    with _lock:
        entry = _registry.get(event_id)
        if not entry:
            return {"ok": False, "event_id": event_id, "reason": "no_inquiry"}
        if entry["state"] != "counting":
            return {"ok": True, "event_id": event_id,
                    "remaining": entry.get("remaining") or entry["countdown"],
                    "state": entry["state"]}
        remaining = max(0.5, entry["expires_at"] - _time.monotonic())
        entry["timer"].cancel()
        entry["remaining"] = remaining
        entry["state"] = "paused"
        logger.info(f"[Inquiry] 暂停倒计时: {event_id} | 剩余 {remaining:.1f}s")
        return {"ok": True, "event_id": event_id, "remaining": remaining, "state": "paused"}


def resume_inquiry(event_id: str) -> dict:
    """
    恢复/开始倒计时（前端播完一条语音后调用，幂等）。

    waiting_begin（首条播完）→ 从完整 countdown 开始；
    paused（额外播报播完）→ 按暂停前剩余恢复。
    """
    with _lock:
        entry = _registry.get(event_id)
        if not entry:
            return {"ok": False, "event_id": event_id, "reason": "no_inquiry"}
        if entry["state"] == "counting":
            return {"ok": True, "event_id": event_id, "remaining": entry["remaining"], "state": "counting"}
        if entry["state"] == "paused":
            seconds = entry["remaining"]
        else:  # waiting_begin
            seconds = entry["countdown"]
        _arm_timer(event_id, seconds)
        logger.info(f"[Inquiry] 恢复倒计时: {event_id} | {seconds:.1f}s | 从 {entry['state']}")
        return {"ok": True, "event_id": event_id, "remaining": entry["remaining"], "state": "counting"}


def fail_inquiry(event_id: str, detail: str = "摄像头问询播报失败") -> dict:
    """问询语音连续发送失败时结束当前流程并走安全通知。"""
    from app.services.fall_event_archive import (
        get_event, update_event, add_timeline_entry, TimelineEntry,
        transition_voice_status,
    )
    with _lock:
        entry = _registry.pop(event_id, None)
        if entry and entry.get("timer"):
            entry["timer"].cancel()
    record = get_event(event_id)
    if not record:
        return {"ok": False, "event_id": event_id, "reason": "not_found"}
    if record.voice_confirm_status != "pending":
        return {"ok": False, "event_id": event_id, "reason": "already_resolved"}
    if not transition_voice_status(event_id, "pending", "technical_failure"):
        return {"ok": False, "event_id": event_id, "reason": "already_resolved"}
    add_timeline_entry(event_id, TimelineEntry(
        time=datetime.now(timezone.utc).isoformat(),
        stage="inquiry",
        label="问询播报失败",
        detail=detail,
        status="error",
        duration_ms=0,
    ))
    update_event(event_id, status="notifying")
    logger.error(f"[Inquiry] 摄像头问询失败，启动安全通知: {event_id} | {detail}")
    _dispatch_emergency_async(event_id, reason="voice_failure")
    return {
        "ok": True,
        "event_id": event_id,
        "voice_confirm_status": "technical_failure",
    }


def force_timeout_inquiry(event_id: str) -> dict:
    """
    前端本地倒计时归零时的兜底触发（幂等）。

    服务端定时器是权威源，正常会先到期；此处仅兜底保证即使前端先归零也能触发紧急联络。
    """
    from app.services.fall_event_archive import transition_voice_status

    with _lock:
        entry = _registry.pop(event_id, None)
        if entry and entry.get("timer"):
            entry["timer"].cancel()

    if not transition_voice_status(event_id, "pending", "timeout"):
        return {"ok": False, "event_id": event_id, "reason": "already_resolved"}
    _on_timeout_body(event_id, "前端倒计时归零兜底触发")
    return {"ok": True, "event_id": event_id, "voice_confirm_status": "timeout"}


def resolve_inquiry(event_id: str, action: str, recognized_text: str = "") -> dict:
    """
    处理老人回应（由 voice/confirm 端点调用）

    action:
      cancel — 老人说"我没事"/旁人"取消"：停倒计时、取消告警、记录误报、不通知
      help   — 老人说"帮我呼叫"：停倒计时、立即紧急联络

    通过 CAS 原子迁移 pending → cancelled/help_requested，
    CAS 失败（已被 timeout/其他请求抢先）时返回 transitioned=False。
    """
    from app.services.fall_event_archive import (
        get_event, update_event, add_timeline_entry, TimelineEntry,
        transition_voice_status,
    )

    if action not in ("cancel", "help"):
        raise ValueError(f"未知动作: {action}")

    record = get_event(event_id)
    if not record:
        return {"transitioned": False, "event_id": event_id, "reason": "not_found"}

    new_status = "cancelled" if action == "cancel" else "help_requested"
    if not transition_voice_status(event_id, "pending", new_status):
        # 已被 timeout/其他回应抢先，幂等返回
        return {
            "transitioned": False,
            "event_id": event_id,
            "voice_confirm_status": record.voice_confirm_status,
        }

    # 停掉倒计时
    with _lock:
        entry = _registry.pop(event_id, None)
        if entry:
            entry["timer"].cancel()

    if action == "cancel":
        update_event(event_id,
                     status="voice_cancelled",
                     feedback={
                         "is_false_alarm": True,
                         "comment": recognized_text or "老人回应'我没事'，取消告警",
                         "reported_by": "voice",
                     })
        add_timeline_entry(event_id, TimelineEntry(
            time=datetime.now(timezone.utc).isoformat(),
            stage="inquiry",
            label="告警已取消",
            detail=f"老人回应 '{recognized_text or '我没事'}'，取消告警，记录误报",
            status="success",
            duration_ms=0,
        ))
        update_event(
            event_id,
            voice_feedback_text="好的，已为您取消告警，请放心休息。",
            voice_feedback_claimed_by="",
            voice_feedback_played=False,
        )
        logger.info(f"[Inquiry] 取消告警: {event_id} | 回应={recognized_text}")
        return {"transitioned": True, "event_id": event_id, "voice_confirm_status": "cancelled"}

    # help → 立即紧急联络（异步线程，不阻塞请求）
    add_timeline_entry(event_id, TimelineEntry(
        time=datetime.now(timezone.utc).isoformat(),
        stage="inquiry",
        label="老人求助",
        detail=f"老人回应 '{recognized_text or '帮我呼叫'}'，立即紧急联络",
        status="success",
        duration_ms=0,
    ))
    logger.info(f"[Inquiry] 老人求助: {event_id} | 回应={recognized_text}")
    update_event(
        event_id,
        voice_feedback_text="已为您发送求助信息，请保持冷静，家人将尽快联系您。",
        voice_feedback_claimed_by="",
        voice_feedback_played=False,
    )
    update_event(event_id, status="notifying")
    _dispatch_emergency_async(event_id, reason="help")
    return {"transitioned": True, "event_id": event_id, "voice_confirm_status": "help_requested"}


def _on_timeout(event_id: str):
    """倒计时到期（timer 线程回调）：CAS pending→timeout，自动紧急联络"""
    from app.services.fall_event_archive import transition_voice_status

    with _lock:
        _registry.pop(event_id, None)

    if not transition_voice_status(event_id, "pending", "timeout"):
        return  # 已被 cancel/help 抢先
    _on_timeout_body(event_id)


def _on_timeout_body(event_id: str, detail: str = "倒计时结束无回应，自动启动紧急联络"):
    """CAS 成功后统一处理：timeline + 播报 + 紧急联络"""
    from app.services.fall_event_archive import add_timeline_entry, update_event, TimelineEntry

    add_timeline_entry(event_id, TimelineEntry(
        time=datetime.now(timezone.utc).isoformat(),
        stage="inquiry",
        label="语音超时",
        detail=detail,
        status="warning",
        duration_ms=0,
    ))
    update_event(
        event_id,
        voice_feedback_text="检测到您长时间没有回应，已自动联系您的家人。",
        voice_feedback_claimed_by="",
        voice_feedback_played=False,
    )
    logger.info(f"[Inquiry] 语音超时，自动紧急联络: {event_id} | {detail}")
    update_event(event_id, status="notifying")
    _dispatch_emergency_async(event_id, reason="timeout")


NOTIFICATION_MAX_SECONDS = 45.0


def _dispatch_emergency_async(event_id: str, reason: str):
    """异步触发紧急联络，并在外部通道卡住时释放活动流程。"""
    def _watchdog():
        _time.sleep(NOTIFICATION_MAX_SECONDS)
        from app.services.fall_event_archive import get_event, update_event, add_timeline_entry, TimelineEntry
        record = get_event(event_id)
        if record and record.status in {"inquiring", "notifying"}:
            update_event(event_id, status="workflow_failed")
            add_timeline_entry(event_id, TimelineEntry(
                time=datetime.now(timezone.utc).isoformat(),
                stage="notifying",
                label="通知超时",
                detail=f"通知通道超过 {NOTIFICATION_MAX_SECONDS:.0f}s 未完成，已释放活动流程",
                status="error",
                duration_ms=int(NOTIFICATION_MAX_SECONDS * 1000),
            ))
            logger.error(f"[Inquiry] 通知超时，释放活动流程: {event_id} | reason={reason}")

    def _worker():
        try:
            _dispatch_emergency(event_id, reason)
        except Exception as e:
            from app.services.fall_event_archive import update_event
            update_event(event_id, status="workflow_failed")
            logger.error(f"[Inquiry] 紧急联络异常: {event_id} | {e}")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    threading.Thread(target=_watchdog, daemon=True, name=f"notify-watchdog-{event_id[:12]}").start()


def recover_inquiries() -> None:
    """服务重启后恢复 pending 问询，并释放上次崩溃遗留的已决事件。"""
    from app.services.fall_event_archive import list_events, update_event, get_event, add_timeline_entry, TimelineEntry
    items, _ = list_events(limit=200, days=3650)
    for record in items:
        if record.status != "inquiring":
            continue
        if record.voice_confirm_status == "pending":
            start_inquiry(
                event_id=record.event_id,
                risk_level=record.risk_level or "II",
                countdown_seconds=record.countdown_seconds or 30,
            )
            logger.info(f"[Inquiry] 重启恢复 pending 问询: {record.event_id}")
        elif record.voice_confirm_status in {"timeout", "help_requested", "technical_failure"}:
            # 若通知已写入则仅补齐归档；否则重新进入通知阶段，避免重启丢失告警。
            if record.notification_status:
                update_event(record.event_id, status="archived")
            else:
                update_event(record.event_id, status="notifying")
                _dispatch_emergency_async(record.event_id, reason="restart_recovery")
            add_timeline_entry(record.event_id, TimelineEntry(
                time=datetime.now(timezone.utc).isoformat(),
                stage="recovery",
                label="服务重启恢复",
                detail="恢复上次未完成的跌倒处置流程",
                status="success",
                duration_ms=0,
            ))


def _dispatch_emergency(event_id: str, reason: str):
    """
    触发分级通知 + 归档

    channels 由 risk_level 自动决定（dispatch_notifications channels=None）:
      I级:  sms+app+phone+call_120 / II级: sms+app+phone / III级: app
    """
    from app.services.fall_event_archive import get_event, update_event, add_timeline_entry, TimelineEntry
    from app.services.notification_service import build_notification_request, dispatch_notifications

    record = get_event(event_id)
    if not record:
        return

    update_event(event_id, status="notifying")
    # 小程序订阅消息推送 (家属微信收告警) — 捕获结果, 写入通知状态 [2026-08-12]
    mini_summary = {"success": False, "sent": 0, "total": 0, "message": ""}
    try:
        from app.services.subscribe_message import notify_fall_event
        mini_summary = notify_fall_event(
            event_id=event_id,
            risk_level=record.risk_level or "II",
            location=record.location or "家中",
            description="",
            touch_part=record.touch_ground_part or "",
        ) or mini_summary
    except Exception as e:
        logger.warning(f"[WX] 订阅消息推送异常: {e}")
        mini_summary["message"] = f"推送异常: {str(e)[:80]}"

    notif = build_notification_request(record)
    # 附带问询开始录制的视频片段（可能未就绪）
    clip = _pop_clip(event_id)
    if not clip:
        # RTSP 录制常失败(ffmpeg 超时), 优先用管线缓冲已录制的视频
        # (_dump_fall_clip 已写回归档 record.video_clip) [2026-08-13]
        _archived_clip = getattr(record, "video_clip", "") or ""
        if _archived_clip and os.path.exists(_archived_clip):
            clip = _archived_clip
    if clip:
        try:
            notif.video_clip = clip
        except Exception:
            pass
    else:
        # 视频录制中(13s) → 文本/图先推, 视频就绪后异步补发
        _send_clip_when_ready(event_id)
    results = dispatch_notifications(notif)  # channels=None → 按风险等级自动分级

    # 组装通知状态（贴合真实通道: 企业微信 / 小程序订阅 / 管理端收件箱; 短信/电话/120 为预留）[2026-08-12]
    wechat = results.get("wechat")
    inbox = results.get("app")
    notification_status = {
        # 真实已接通通道
        "wechat_sent": bool(wechat and wechat.success),
        "wechat_message": (wechat.message if wechat else "") or "",
        "mini_subscribed": bool(mini_summary.get("success")),
        "mini_sent": int(mini_summary.get("sent", 0)),
        "mini_total": int(mini_summary.get("total", 0)),
        "mini_message": mini_summary.get("message", ""),
        "inbox_written": bool(inbox and inbox.success),
        # 预留/未开通通道
        "sms": "unconfigured",     # 萤石短信服务未开通
        "phone": "unconfigured",   # 萤石电话外呼服务未开通
        "call_120": "reserved",    # 120急救预留(需对接当地急救调度系统)
        # 保留旧字段兼容 (旧事件/其它读取方)
        "sms_sent": "sms" in results,
        "app_sent": "app" in results,
        "120_called": "call_120" in results,
        "caretaker_notified": "phone" in results,
        "notification_details": {
            k: {"success": v.success, "message": v.message}
            for k, v in results.items()
        } if results else {},
    }

    level_names = {"I": "Ⅰ级高危", "II": "Ⅱ级中危", "III": "Ⅲ级低危"}
    add_timeline_entry(event_id, TimelineEntry(
        time=datetime.now(timezone.utc).isoformat(),
        stage="notifying",
        label="分级干预",
        detail=f"{level_names.get(record.risk_level, record.risk_level)} {reason}触发，已通知家属（企业微信+小程序订阅）",
        status="success",
        duration_ms=0,
    ))
    update_event(event_id, notification_status=notification_status, status="notified")
    update_event(event_id, status="archived")
    add_timeline_entry(event_id, TimelineEntry(
        time=datetime.now(timezone.utc).isoformat(),
        stage="archived",
        label="归档完成",
        detail="紧急联络完成，事件归档",
        status="success",
        duration_ms=0,
    ))
    logger.info(f"[Inquiry] 紧急联络完成: {event_id} | reason={reason} | channels={list(results.keys())}")


def clear_inquiries():
    """取消全部倒计时并清空注册表（测试/重置用）"""
    with _lock:
        for entry in _registry.values():
            entry["timer"].cancel()
        _registry.clear()


def rearm_from_archive() -> int:
    """
    服务重启后恢复遗留问询（扫描 inquiring+pending 事件重建倒计时）

    V-fix 后倒计时从"前端播完首条"才开始，重启后统一重新走 start_inquiry 的
    waiting_begin（完整 countdown + 安全宽限），不再按墙钟反推剩余（墙钟已包含播报时间）。
    返回恢复的问询数。
    """
    from app.services.fall_event_archive import _events

    restored = 0
    for event_id, record in list(_events.items()):
        if record.voice_confirm_status != "pending" or record.status != "inquiring":
            continue
        countdown = float(record.countdown_seconds or 0)
        if countdown <= 0:
            _on_timeout(event_id)  # 无窗口配置 → 立即兜底
        else:
            start_inquiry(event_id, record.risk_level, countdown)
        restored += 1
    if restored:
        logger.info(f"[Inquiry] rearm_from_archive: 恢复 {restored} 个遗留问询")
    return restored


# 服务启动时恢复遗留问询（重启不丢超时）
rearm_from_archive()
