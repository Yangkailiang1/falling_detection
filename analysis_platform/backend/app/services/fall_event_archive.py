"""
# 跌倒事件归档系统
# 功能: 存储、检索、更新跌倒事件记录，包含完整响应时间线和误报反馈
# 对应方案书: §8.2 事件归档 — 每次跌倒事件自动生成结构化报告
"""
import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional

# 归档文件路径
ARCHIVE_FILE = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'fall_events_archive.json')


@dataclass
class TimelineEntry:
    """事件时间线节点"""
    time: str         # ISO8601 时间戳
    stage: str        # 阶段: detected/analyzed/reporting/notifying/archived
    label: str        # 中文标签: 跌倒检测/骨骼分析/医疗报告/通知推送/归档完成
    detail: str       # 详细信息
    status: str       # success/error/pending
    duration_ms: int  # 该阶段耗时(ms)


@dataclass
class FallEventRecord:
    """跌倒事件归档记录
    对应方案书: §8.2 每次跌倒事件自动生成结构化报告
    """
    event_id: str                      # 唯一事件ID
    status: str                        # detected/analyzed/confirmed/notified/archived/false_alarm
    created_at: str                    # 事件创建时间 ISO8601

    # 跌倒检测参数 (模拟 V-JEPA 2 输出)
    detection_confidence: float        # 检测置信度
    detection_latency_ms: float        # 推理延迟(ms)
    video_window_frames: int           # 视频窗口帧数
    video_window_duration_s: float     # 视频窗口时长(s)

    # 骨骼分析结果
    touch_ground_part: str             # 触地部位
    fall_direction: str                # 跌倒方向
    impact_velocity: float             # 冲击速度 (m/s)
    body_tilt_angle: float             # 身体倾角 (度)
    center_of_mass_velocity: float     # 重心移动速度 (m/s)

    # 风险等级
    risk_level: str                    # I / II / III
    risk_level_name: str               # 高危 / 中危 / 低危
    likely_injury_types: list = field(default_factory=list)

    # 事件上下文
    location: str = ""                 # 发生地点
    device_serial: str = ""            # 设备序列号
    scenario_key: str = ""             # 场景类型 key
    scenario_name: str = ""            # 场景名称
    description: str = ""              # 场景描述

    # 大模型输出
    medical_report: str = ""           # 医疗简报全文
    report_recommendation: str = ""    # 建议措施摘要

    # 响应策略
    response_strategy: str = ""        # 干预策略描述
    countdown_seconds: int = 0         # 语音问询倒计时(秒): I=10 / II=30 / III=60
    voice_confirm_status: str = "pending"  # pending/cancelled/help_requested/timeout
    # 摄像头语音执行器的持久化出站消息，由唯一 WebRTC 客户端串行播放。
    voice_feedback_text: str = ""
    voice_feedback_claimed_by: str = ""
    voice_feedback_played: bool = False
    voice_executor_id: str = ""
    voice_executor_claimed_at: float = 0.0
    # (旧存档值 confirmed_cancel/confirmed_help 兼容，前端按"非 pending 即已结束"处理)

    # 通知状态
    notification_status: dict = field(default_factory=dict)
    # {"sms_sent": bool, "app_sent": bool, "120_called": bool, "caretaker_notified": bool}

    # 现场证据 [V7.2] — 现场抓拍/告警图片 URL（device/capture 或 Webhook pictureList 写入）
    capture_pic_url: str = ""            # 现场图片 URL
    capture_time: str = ""               # 抓拍时间（ISO8601 或毫秒时间戳字符串）
    # 现场图片本地持久化 [V9.4] — 萤石签名 URL 24h 过期, 抓拍时下载存 backend/data/captures/
    capture_pic_path: str = ""           # 本地图片文件绝对路径（存在时优先用本地, 不依赖云端 URL）

    # 现场视频 [V9.3] — 问询开始录制的事件视频片段（推送后保留供小程序查看）
    video_clip: str = ""                 # 视频文件绝对路径

    # 真实骨骼序列 [2026-08-13] — v2.2 推理收集的 COCO-17 跌倒序列, 供算法平台训练
    skeleton_sequence: dict = field(default_factory=dict)

    # 完整时间线
    timeline: list = field(default_factory=list)  # list[TimelineEntry]

    # 用户反馈
    feedback: Optional[dict] = None    # {"is_false_alarm": bool, "comment": str, "reported_by": str}

    # 算法迭代平台同步 [2026-08-12] — 是否已脱敏推送到算法平台（避免重复推送）
    pushed_to_algo: bool = False


# === 内存存储 ===
_lock = threading.Lock()
_events: dict = {}  # event_id -> FallEventRecord

# 一个摄像头/一个正式版实例同一时间只允许一条跌倒处置流程运行。
# 这些状态覆盖事件创建、分析、问询和通知阶段；cancelled/archived/
# workflow_failed 等结果状态不再占用流程槽位。
ACTIVE_WORKFLOW_STATUSES = frozenset({"detected", "analyzed", "inquiring", "notifying"})


def get_active_workflow() -> Optional[FallEventRecord]:
    """返回当前唯一活动流程；事件归档本身就是跨线程的权威状态源。"""
    with _lock:
        active = [
            event for event in _events.values()
            if event.status in ACTIVE_WORKFLOW_STATUSES
        ]
        if not active:
            return None
        active.sort(key=lambda event: event.created_at or "")
        return active[0]


def workflow_is_active() -> bool:
    """快速查询是否已有跌倒处置流程占用活动槽位。"""
    return get_active_workflow() is not None

def _ensure_data_dir():
    """确保归档数据目录存在"""
    data_dir = os.path.dirname(ARCHIVE_FILE)
    if not os.path.exists(data_dir):
        os.makedirs(data_dir, exist_ok=True)


def _merge_records_locked(data: dict, overwrite: bool = True) -> int:
    """把磁盘归档记录并入内存 _events（调用方必须已持 _lock）。

    [2026-08-13 根因修复] 逐条容错: 单条记录字段不兼容只跳过, 不拖垮整轮加载——
    否则 _load_from_disk 遇一条坏记录整轮失败 → _events 为空 → 下次 _save_to_disk
    用空 dict 覆盖磁盘, 永久清空归档。

    overwrite=True : 同 event_id 用磁盘版本覆盖内存（外部写入为准, _load_from_disk/_reload_from_disk 用）
    overwrite=False: 只补内存缺失的 event_id, 不覆盖内存新鲜状态（_save_to_disk 防清空用）
    """
    if not isinstance(data, dict):
        return 0
    merged = 0
    for event_id, record in data.items():
        if not isinstance(record, dict) or not event_id:
            continue
        if not overwrite and event_id in _events:
            continue
        try:
            _events[event_id] = FallEventRecord(**record)
            merged += 1
        except (TypeError, ValueError):
            continue
    return merged


def _load_from_disk():
    """从磁盘加载事件归档（逐条容错 [2026-08-13]）"""
    global _events
    _ensure_data_dir()
    if not os.path.exists(ARCHIVE_FILE):
        return
    try:
        with open(ARCHIVE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return
    with _lock:
        _merge_records_locked(data)


def _save_to_disk():
    """持久化到磁盘（原子写入 + 写前合并磁盘防清空 + .bak 备份）"""
    _ensure_data_dir()
    with _lock:
        # [2026-08-13 根因修复] 写前把磁盘已有事件补入内存（只补缺失, 不覆盖内存新鲜状态),
        # 即使内存因任何原因为空, 也绝不用空 dict 覆盖磁盘。
        try:
            if os.path.exists(ARCHIVE_FILE):
                with open(ARCHIVE_FILE, 'r', encoding='utf-8') as f:
                    disk_data = json.load(f)
                _merge_records_locked(disk_data, overwrite=False)
        except (json.JSONDecodeError, OSError):
            pass
        data = {}
        for event_id, record in _events.items():
            data[event_id] = asdict(record)
        # 写前备份 (防极端情况)
        try:
            import shutil
            shutil.copy2(ARCHIVE_FILE, ARCHIVE_FILE + ".bak")
        except OSError:
            pass
        tmp = f"{ARCHIVE_FILE}.tmp.{os.getpid()}"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ARCHIVE_FILE)
    # 锁外触发算法平台实时推送（网络调用不阻塞写归档）
    _maybe_push_terminal_events()


def _reload_from_disk():
    """从磁盘归档重新加载进内存（合并外部进程如 Windows standalone 的写入）。

    **为什么 [2026-08-12]**: 小程序家属改判反馈走 standalone(Windows) 直接写共享归档 JSON,
    WSL 后端内存里的 _events 不会自动更新, 若不重载会: ①看不到新反馈导致不重推; ②下次
    _save_to_disk 用陈旧内存覆盖磁盘, 把 standalone 的改判冲掉。磁盘是共享真相源。
    """
    if not os.path.exists(ARCHIVE_FILE):
        return
    try:
        with open(ARCHIVE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return
    with _lock:
        _merge_records_locked(data)


def _maybe_push_terminal_events():
    """把进入终态且未推送过的事件脱敏后实时推送到算法迭代平台 [2026-08-12]。
    非阻塞：失败仅记录日志，下次落盘自动重试（服务端按 event_id 去重，幂等）。
    先从磁盘重载, 兜住 standalone(Windows) 写的家属改判反馈。"""
    try:
        from app.services import algo_sync
        if not algo_sync.enabled():
            return
        _reload_from_disk()   # [2026-08-12] 合并外部进程(standalone)的写入
        with _lock:
            candidates = [
                rec for rec in _events.values()
                if rec.status in algo_sync.TERMINAL_STATUSES and not rec.pushed_to_algo
            ]
        if not candidates:
            return
        # 锁外推送（网络调用）
        succeeded = [rec.event_id for rec in candidates if algo_sync.push_event(asdict(rec))]
        if succeeded:
            with _lock:
                for rec in _events.values():
                    if rec.event_id in succeeded:
                        rec.pushed_to_algo = True
            # 写回已推送标记；失败者保留 False，下次自动重试
            _save_to_disk()
    except Exception as e:  # 同步绝不能阻断归档主流程
        print(f"[algo_sync] 推送失败: {e}", flush=True)


def _start_sync_poller():
    """后台守护线程: 周期性重载磁盘归档并重推终态事件 [2026-08-12]。

    弥补"standalone(Windows) 因 WSL 网络隔离无法直连算法平台"的空档:
    家属在小程序改判 → standalone 写归档+置 pushed_to_algo=False → 本线程兜底重推。
    """
    import time as _time

    def _loop():
        while True:
            try:
                _maybe_push_terminal_events()
            except Exception:
                pass
            _time.sleep(15)

    threading.Thread(target=_loop, daemon=True).start()


def create_event(record: FallEventRecord) -> Optional[FallEventRecord]:
    """原子创建事件；已有活动流程时拒绝新的事件。"""
    record.created_at = record.created_at or datetime.now(timezone.utc).isoformat()
    with _lock:
        if any(event.status in ACTIVE_WORKFLOW_STATUSES for event in _events.values()):
            return None
        _events[record.event_id] = record
    _save_to_disk()
    return record


def get_event(event_id: str) -> Optional[FallEventRecord]:
    """按ID获取事件"""
    with _lock:
        return _events.get(event_id)


def list_events(
    limit: int = 20,
    offset: int = 0,
    risk_level: str = None,
    status: str = None,
    days: int = 7,
) -> tuple:
    """
    列出事件记录
    参数:
        limit: 每页条数
        offset: 偏移量
        risk_level: 按风险等级筛选 (I/II/III)
        status: 按状态筛选
        days: 最近N天
    返回: (items, total)
    """
    with _lock:
        # 按创建时间倒序排列
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        filtered = []
        for event in _events.values():
            if event.created_at < cutoff:
                continue
            if risk_level and event.risk_level != risk_level:
                continue
            if status and event.status != status:
                continue
            filtered.append(event)

        filtered.sort(key=lambda e: e.created_at, reverse=True)
        total = len(filtered)
        items = filtered[offset:offset + limit]
        return items, total


def update_event(event_id: str, **kwargs) -> Optional[FallEventRecord]:
    """更新事件记录"""
    with _lock:
        event = _events.get(event_id)
        if not event:
            return None
        for key, value in kwargs.items():
            if hasattr(event, key):
                setattr(event, key, value)
    _save_to_disk()
    return event


def add_timeline_entry(event_id: str, entry: TimelineEntry) -> Optional[FallEventRecord]:
    """向事件添加时间线节点"""
    with _lock:
        event = _events.get(event_id)
        if not event:
            return None
        event.timeline.append(entry)
    _save_to_disk()
    return event


def transition_voice_status(event_id: str, expected: str, new: str) -> bool:
    """
    原子地仅当 voice_confirm_status == expected 时更新为 new

    用于语音问询状态机（pending → cancelled/help_requested/timeout），
    解决"倒计时到期瞬间与老人回应同时到达"的竞态——只有一个调用方 CAS 成功，
    只有成功者触发通知，杜绝双发。
    """
    with _lock:
        event = _events.get(event_id)
        if not event or event.voice_confirm_status != expected:
            return False
        event.voice_confirm_status = new
    _save_to_disk()
    return True


def claim_voice_executor(event_id: str, executor_id: str) -> bool:
    """Atomically assign one live voice executor to an inquiring event."""
    if not executor_id:
        return False
    with _lock:
        event = _events.get(event_id)
        if not event or event.status != "inquiring" or event.voice_confirm_status != "pending":
            return False
        if event.voice_executor_id and event.voice_executor_id != executor_id:
            claimed_at = float(event.voice_executor_claimed_at or 0.0)
            if claimed_at and time.time() - claimed_at < 15:
                return False
        event.voice_executor_id = executor_id
        event.voice_executor_claimed_at = time.time()
    _save_to_disk()
    return True


def claim_voice_feedback(event_id: str, executor_id: str) -> Optional[str]:
    """Claim one pending camera feedback message for serialized playback."""
    if not executor_id:
        return None
    with _lock:
        event = _events.get(event_id)
        if not event or not event.voice_feedback_text or event.voice_feedback_played:
            return None
        if event.voice_feedback_claimed_by:
            return None
        event.voice_feedback_claimed_by = executor_id
        text = event.voice_feedback_text
    _save_to_disk()
    return text


def acknowledge_voice_feedback(event_id: str, executor_id: str) -> bool:
    """Mark a claimed feedback message as played exactly once."""
    with _lock:
        event = _events.get(event_id)
        if not event or event.voice_feedback_claimed_by != executor_id:
            return False
        event.voice_feedback_played = True
    _save_to_disk()
    return True


def submit_feedback(event_id: str, is_false_alarm: bool, comment: str = "") -> Optional[FallEventRecord]:
    """
    提交误报反馈
    对应方案书: §8.2 误报反馈入口（家属可标注"非跌倒事件"，用于持续优化模型）

    [2026-08-12] 手动反馈是权威 ground truth: 重置 pushed_to_algo, 让终态事件被
    _save_to_disk→_maybe_push_terminal_events 重新推送到算法平台(upsert 覆盖旧标签)。
    """
    with _lock:
        event = _events.get(event_id)
        if not event:
            return None
        event.feedback = {
            "is_false_alarm": is_false_alarm,
            "comment": comment,
            "reported_at": datetime.now(timezone.utc).isoformat(),
        }
        if is_false_alarm:
            event.status = "false_alarm"
        elif event.status == "false_alarm":
            # 家属确认是真实跌倒 → 从误报状态还原为已归档 [2026-08-12]
            event.status = "archived"
        # 状态可能已变化 → 允许重推（终态 + 未推 → 推送钩子重推）
        event.pushed_to_algo = False
    _save_to_disk()
    return event


def get_stats() -> dict:
    """获取归档统计信息"""
    with _lock:
        total = len(_events)
        by_level = {"I": 0, "II": 0, "III": 0, "unknown": 0}
        by_status = {}
        false_alarms = 0

        for event in _events.values():
            level = event.risk_level if event.risk_level in by_level else "unknown"
            by_level[level] += 1
            status = event.status
            by_status[status] = by_status.get(status, 0) + 1
            if event.feedback and event.feedback.get("is_false_alarm"):
                false_alarms += 1

        return {
            "total_events": total,
            "by_risk_level": by_level,
            "by_status": by_status,
            "false_alarms": false_alarms,
        }


# 启动时加载
_load_from_disk()
# 后台同步轮询: 兜底重推 standalone(Windows) 写入的家属改判反馈到算法平台 [2026-08-12]
_start_sync_poller()
