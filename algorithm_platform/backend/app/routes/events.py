"""
# 算法迭代平台 - 事件浏览路由
"""
import re
from datetime import datetime

from flask import Blueprint, request
from app.models import event_store
from app.utils.response import success_response, error_response, paginated_response

events_bp = Blueprint('events', __name__)


def _normalize_report_time(event):
    """兼容旧同步记录：简报时间与事件 created_at 统一为本地时间。"""
    report = event.get('medical_report') or {}
    text = report.get('full_text') if isinstance(report, dict) else ''
    created_at = event.get('created_at')
    if not text or not created_at:
        return event
    try:
        dt = datetime.fromisoformat(str(created_at).replace('Z', '+00:00')).astimezone()
    except (TypeError, ValueError):
        return event
    event['medical_report'] = dict(report)
    event['medical_report']['full_text'] = re.sub(
        r'^(⏰\s*跌倒检测时间：)\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}',
        rf'\g<1>{dt.strftime("%Y-%m-%d %H:%M:%S")}', text, count=1,
    )
    return event


def _hide_unreliable_direction(event):
    """只保留有明确语义的前后/左右方向；first_side/simultaneous 不属于跌倒方向。"""
    analysis = event.get('skeleton_analysis') or {}
    if analysis.get('fall_direction') not in {'forward', 'backward', 'sideways_left', 'sideways_right'}:
        event['skeleton_analysis'] = dict(analysis)
        event['skeleton_analysis']['fall_direction'] = ''
    return event


def _parse_bool(s):
    """'1'/'true' → True；空/None → None"""
    if s is None or s == '':
        return None
    return s.lower() in ('1', 'true', 'yes')


@events_bp.route('/events', methods=['GET'])
def list_events():
    """GET /api/events — 事件列表（筛选+分页），列表项不含 skeleton_sequence"""
    args = request.args
    try:
        limit = min(int(args.get('limit', 20)), 100)
        offset = max(int(args.get('offset', 0)), 0)
    except ValueError:
        return error_response('limit/offset 非法', 400)

    filters = {
        'source_id': args.get('source_id') or None,
        'risk_level': args.get('risk_level') or None,
        'status': args.get('status') or None,
        'is_fall': _parse_bool(args.get('is_fall')),
        'has_skeleton': _parse_bool(args.get('has_skeleton')),
        'date_from': args.get('date_from') or None,
        'date_to': args.get('date_to') or None,
        'q': args.get('q') or None,
    }
    items, total = event_store.list_events(filters, limit=limit, offset=offset)
    page = offset // limit + 1 if limit else 1
    return paginated_response(items, total, page, limit)


@events_bp.route('/events/<event_id>', methods=['GET'])
def event_detail(event_id):
    """GET /api/events/<id> — 完整事件（含骨骼序列）"""
    event = event_store.get_event(event_id)
    if not event:
        return error_response('事件不存在', 404)
    return success_response(_hide_unreliable_direction(_normalize_report_time(event)))
