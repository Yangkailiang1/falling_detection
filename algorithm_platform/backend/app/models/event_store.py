"""
# 算法迭代平台 - 事件存储层（SQLite CRUD）
# 功能: 入库(去重)/列表(筛选+分页)/详情/统计/趋势
# 设计: payload 存完整脱敏 JSON；可筛选字段反规范化成索引列
"""
import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from app.db import get_conn, get_write_lock
from app.utils.timeutil import parse_iso_to_ts


def _row_to_event(row) -> dict:
    """数据库行 → 事件 dict（payload 反序列化）"""
    return json.loads(row['payload'])


def _prepared(event: dict) -> Tuple[str, str, str, float, str, Optional[str], Optional[str], Optional[int], int]:
    """事件 dict → 存储行字段"""
    created_at = event.get('created_at', '')
    created_ts = parse_iso_to_ts(created_at) or parse_iso_to_ts(event.get('ingested_at')) or 0.0
    risk = event.get('risk', {}).get('level')
    status = event.get('status')
    is_fall = event.get('ground_truth', {}).get('is_fall')
    has_skeleton = 1 if event.get('skeleton_sequence') else 0
    return (
        event['event_id'], event['source_id'], created_at, created_ts,
        event.get('ingested_at', ''), risk, status,
        1 if is_fall else 0 if is_fall is not None else None,
        has_skeleton,
    )


def insert_many(events: List[dict]) -> Tuple[int, int, int]:
    """批量入库（upsert）：新事件插入；已存在且数据(标签/状态/payload)变化则更新。

    [2026-08-12] 从 INSERT OR IGNORE 升级: 客户侧家属事后改判真实/误报后重推,
    需要覆盖旧 ground_truth 标签, 否则训练集 is_fall 永远停留在第一次推送。

    返回 (inserted, skipped_duplicates, updated):
      inserted — 新插入
      skipped  — 已存在且完全相同（无需更新）
      updated  — 已存在但状态/标签/payload 发生变化（如 真实→误报）
    """
    if not events:
        return 0, 0, 0
    conn = get_conn()
    inserted = skipped = updated = 0
    with get_write_lock():
        try:
            conn.execute('BEGIN')
            for event in events:
                (eid, sid, created_at, created_ts, ingested_at,
                 risk, status, is_fall, has_skeleton) = _prepared(event)
                payload = json.dumps(event, ensure_ascii=False)
                exists = conn.execute(
                    'SELECT 1 FROM events WHERE event_id=?', (eid,)).fetchone()
                if exists:
                    # 仅当数据确实变化才 UPDATE（identical 重传保持 skipped 语义）
                    cur = conn.execute(
                        'UPDATE events SET '
                        ' source_id=?, created_at=?, created_ts=?, ingested_at=?, '
                        ' risk_level=?, status=?, is_fall=?, has_skeleton=?, payload=? '
                        'WHERE event_id=? '
                        '  AND (status IS NOT ? OR is_fall IS NOT ? OR payload IS NOT ?)',
                        (sid, created_at, created_ts, ingested_at,
                         risk, status, is_fall, has_skeleton, payload,
                         eid, status, is_fall, payload),
                    )
                    if cur.rowcount:
                        updated += 1
                    else:
                        skipped += 1
                else:
                    conn.execute(
                        'INSERT INTO events '
                        '(event_id, source_id, created_at, created_ts, ingested_at, '
                        ' risk_level, status, is_fall, has_skeleton, payload) '
                        'VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (eid, sid, created_at, created_ts, ingested_at,
                         risk, status, is_fall, has_skeleton, payload),
                    )
                    inserted += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return inserted, skipped, updated


def log_ingest(batch_id, source_id, total, inserted, skipped, errors):
    """记录一批接收日志"""
    conn = get_conn()
    with get_write_lock():
        conn.execute(
            'INSERT INTO ingest_log (ingested_at, batch_id, source_id, total, inserted, skipped_duplicates, errors) '
            'VALUES (?,?,?,?,?,?,?)',
            (datetime.now(timezone.utc).isoformat(), batch_id, source_id,
             total, inserted, skipped, errors),
        )
        conn.commit()


def list_events(filters: dict, limit: int = 20, offset: int = 0,
                with_skeleton: bool = False) -> Tuple[List[dict], int]:
    """按条件筛选事件。filters: source_id/risk_level/status/is_fall/has_skeleton/date_from/date_to/q。
    返回 (items, total)；items 默认剥离 skeleton_sequence（列表页瘦身）"""
    conn = get_conn()
    where, params = [], []

    if filters.get('source_id'):
        where.append('source_id = ?'); params.append(filters['source_id'])
    if filters.get('risk_level'):
        where.append('risk_level = ?'); params.append(filters['risk_level'])
    if filters.get('status'):
        where.append('status = ?'); params.append(filters['status'])
    if filters.get('is_fall') is not None:
        where.append('is_fall = ?'); params.append(1 if filters['is_fall'] else 0)
    if filters.get('has_skeleton'):
        where.append('has_skeleton = 1')
    if filters.get('date_from'):
        ts = parse_iso_to_ts(filters['date_from'])
        if ts:
            where.append('created_ts >= ?'); params.append(ts)
    if filters.get('date_to'):
        ts = parse_iso_to_ts(filters['date_to'])
        if ts:
            where.append('created_ts <= ?'); params.append(ts)
    if filters.get('q'):
        where.append('(event_id LIKE ? OR source_id LIKE ?)')
        params += [f"%{filters['q']}%", f"%{filters['q']}%"]

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    # 总数
    total = conn.execute(f'SELECT COUNT(*) c FROM events {where_sql}', params).fetchone()['c']
    # 分页
    rows = conn.execute(
        f'SELECT * FROM events {where_sql} ORDER BY created_ts DESC LIMIT ? OFFSET ?',
        params + [limit, offset],
    ).fetchall()

    items = []
    for row in rows:
        event = _row_to_event(row)
        if not with_skeleton:
            event.pop('skeleton_sequence', None)
        items.append(event)
    return items, total


def get_event(event_id: str) -> Optional[dict]:
    """完整事件（含骨骼序列）"""
    conn = get_conn()
    row = conn.execute('SELECT * FROM events WHERE event_id = ?', (event_id,)).fetchone()
    return _row_to_event(row) if row else None


def count() -> int:
    conn = get_conn()
    return conn.execute('SELECT COUNT(*) c FROM events').fetchone()['c']


def stats() -> dict:
    """仪表盘聚合统计"""
    conn = get_conn()
    def group(col):
        rows = conn.execute(f'SELECT {col} k, COUNT(*) c FROM events GROUP BY {col}').fetchall()
        return {r['k']: r['c'] for r in rows}

    by_risk = group('risk_level')
    by_status = group('status')
    by_source = group('source_id')
    by_is_fall = {True: 0, False: 0}
    for r in conn.execute('SELECT is_fall, COUNT(*) c FROM events GROUP BY is_fall').fetchall():
        if r['is_fall'] is not None:
            by_is_fall[bool(r['is_fall'])] = r['c']
    total = sum(by_is_fall.values()) or sum(by_risk.values())
    total = count()

    has_sk = conn.execute('SELECT COUNT(*) c FROM events WHERE has_skeleton = 1').fetchone()['c']
    false_alarm_rate = round(by_is_fall[False] / total, 4) if total else 0.0
    return {
        'total_events': total,
        'total_sources': len(by_source),
        'total_with_skeleton': has_sk,
        'by_risk_level': by_risk,
        'by_status': by_status,
        'by_source': by_source,
        'by_is_fall': {'falls': by_is_fall[True], 'false_alarms': by_is_fall[False]},
        'false_alarm_rate': false_alarm_rate,
    }


def trend(days: int = 30) -> list:
    """近 days 天按日统计 falls/false_alarms/total，缺天补零"""
    conn = get_conn()
    start_ts = (datetime.now(timezone.utc) - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()
    rows = conn.execute(
        'SELECT CAST(created_ts / 86400 AS INTEGER) AS day, is_fall, COUNT(*) c '
        'FROM events WHERE created_ts >= ? GROUP BY day, is_fall',
        (start_ts,),
    ).fetchall()

    day_map = {}
    for r in rows:
        if r['is_fall'] is None:
            continue
        day_map.setdefault(r['day'], {'falls': 0, 'false_alarms': 0})
        key = 'falls' if r['is_fall'] else 'false_alarms'
        day_map[r['day']][key] += r['c']

    out = []
    for offset in range(days):
        day_dt = datetime.fromtimestamp(start_ts, timezone.utc) + timedelta(days=offset)
        day = int(day_dt.timestamp() / 86400)
        item = day_map.get(day, {'falls': 0, 'false_alarms': 0})
        out.append({
            'date': day_dt.strftime('%Y-%m-%d'),
            'total': item['falls'] + item['false_alarms'],
            'falls': item['falls'],
            'false_alarms': item['false_alarms'],
        })
    return out


def all_payloads() -> List[dict]:
    """全部事件 payload（导出用，含骨骼序列）"""
    conn = get_conn()
    rows = conn.execute('SELECT payload FROM events ORDER BY created_ts DESC').fetchall()
    return [json.loads(r['payload']) for r in rows]
