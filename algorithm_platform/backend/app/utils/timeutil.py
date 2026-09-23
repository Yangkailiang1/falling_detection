"""
# 算法迭代平台 - 时间工具
# 功能: ISO8601 字符串解析归一化（处理 ±hh:mm / Z 混用时区）
"""
from datetime import datetime, timezone


def parse_iso_to_ts(iso_str, fallback_ts=None):
    """
    将 ISO8601 字符串解析为 epoch 秒(UTC)
    兼容 '2026-08-03T08:04:27.427455+00:00' / '+08:00' / 'Z'
    解析失败时返回 fallback_ts（或 None）
    """
    if not iso_str:
        return fallback_ts
    try:
        s = iso_str.replace('Z', '+00:00') if iso_str.endswith('Z') else iso_str
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).timestamp()
    except (ValueError, TypeError):
        return fallback_ts


def iso_now():
    """当前 UTC 时间 ISO8601"""
    return datetime.now(timezone.utc).isoformat()
