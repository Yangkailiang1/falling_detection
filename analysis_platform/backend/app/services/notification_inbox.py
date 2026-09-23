"""
# 管理端通知收件箱 [V7.2 新增]
# 功能: 替代"萤石APP推送"假实现 — 跌倒事件通知写入收件箱，前端轮询展示
#
# 背景: 萤石开放平台无第三方主动推送 APP 的 API（APP 通知只能由设备端告警触发），
#       且开通 B 端消息推送服务后与萤石 APP C 端通知永久互斥（关闭需工单恢复，
#       详见 萤石平台/通知链路与消息推送调研报告.md §三）。
#       故 send_app_push() 改为真实的管理端收件箱：GET /api/notifications 查询。
#
# 存储: 内存 deque(上限200) + JSON 持久化（参考 fall_event_archive.py 模式）
"""
import json
import os
import threading
import uuid
import logging
from collections import deque
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# 收件箱持久化文件
INBOX_FILE = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'notification_inbox.json')

# 内存存储（环形队列，最多保留 200 条）
_lock = threading.Lock()
_inbox: deque = deque(maxlen=200)


def _ensure_data_dir():
    """确保数据目录存在"""
    data_dir = os.path.dirname(INBOX_FILE)
    if not os.path.exists(data_dir):
        os.makedirs(data_dir, exist_ok=True)


def _load_from_disk():
    """从磁盘加载收件箱"""
    global _inbox
    _ensure_data_dir()
    if os.path.exists(INBOX_FILE):
        try:
            with open(INBOX_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                _inbox = deque(data[-200:], maxlen=200)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass


def _save_to_disk():
    """持久化到磁盘"""
    _ensure_data_dir()
    with _lock:
        with open(INBOX_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(_inbox), f, ensure_ascii=False, indent=2)


def push_notification(title: str, body: str, extras: dict = None) -> str:
    """
    写入一条管理端通知

    参数:
        title: 通知标题（如"跌倒预警 - I级(高危)"）
        body: 通知内容
        extras: 附加数据（event_id / risk_level / medical_report 等）
    返回: 通知 ID
    """
    message_id = uuid.uuid4().hex[:16]
    entry = {
        "id": message_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "body": body,
        "extras": extras or {},
        "read": False,
    }
    with _lock:
        _inbox.appendleft(entry)  # 最新在前
    _save_to_disk()
    logger.info(f"[Inbox] 通知已写入: {title} | id={message_id}")
    return message_id


def list_notifications(limit: int = 50) -> list:
    """查询通知列表（最新在前）"""
    with _lock:
        return list(_inbox)[:limit]


def mark_read(message_id: str) -> bool:
    """标记通知已读"""
    with _lock:
        for entry in _inbox:
            if entry.get("id") == message_id:
                entry["read"] = True
                _save_to_disk()
                return True
    return False


# 启动时加载
_load_from_disk()
