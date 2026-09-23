"""
# 算法迭代平台 - 数据接入路由
# 功能: 接收客户侧脱敏事件批量上传（写入需 X-API-Key）；采集接口信息
"""
import uuid
from flask import Blueprint, request, jsonify
from config import Config
from app.models import event_store
from app.services.anonymize import validate_anonymized_event
from app.utils.auth import require_api_key
from app.utils.response import success_response, error_response

ingest_bp = Blueprint('ingest', __name__)


@ingest_bp.route('/ingest/info', methods=['GET'])
def ingest_info():
    """GET /api/ingest/info — 采集接口说明（端点/key/示例命令），供前端 Ingest 页展示"""
    key = Config.ALGO_API_KEY
    return success_response({
        'endpoint': '/api/ingest/events',
        'method': 'POST',
        'auth': 'X-API-Key',
        'api_key_configured': bool(key),
        'max_batch': Config.MAX_BATCH_EVENTS,
        'sample_curl': (
            f'curl -X POST {request.host_url.rstrip("/")}/api/ingest/events '
            f'-H "Content-Type: application/json" -H "X-API-Key: {key}" '
            f'-d \'{{"source_id":"src_demo_a","events":[...]}}\''
        ),
        'collector_command': (
            'python3 tools/collector.py --archive <客户归档.json> '
            '--api http://localhost:5003 --api-key <KEY> --attach-skeletons'
        ),
    })


@ingest_bp.route('/ingest/events', methods=['POST'])
@require_api_key
def ingest_events():
    """POST /api/ingest/events — 批量接收脱敏事件
    Body: {source_id?, batch_id?, events:[AnonymizedEvent]}（≤MAX_BATCH_EVENTS 条）
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('events'), list):
        return error_response('请求体须为 {"events": [...]}', 400)
    events = data['events']
    if len(events) > Config.MAX_BATCH_EVENTS:
        return error_response(f'单批事件数超限（≤{Config.MAX_BATCH_EVENTS}）', 400)

    batch_id = data.get('batch_id') or f'batch-{uuid.uuid4().hex[:12]}'
    source_hint = data.get('source_id') or events[0].get('source_id') if events else ''

    inserted, skipped, updated = 0, 0, 0
    errors = 0
    valid = []
    for ev in events:
        err = validate_anonymized_event(ev)
        if err:
            errors += 1
            continue
        # 批次内未声明 source_id 时，以事件自带为准（校验已保证存在）
        if not data.get('source_id') and ev.get('source_id'):
            pass
        valid.append(ev)

    if valid:
        inserted, skipped, updated = event_store.insert_many(valid)

    event_store.log_ingest(batch_id, source_hint, len(events), inserted, skipped, errors)
    return success_response({
        'batch_id': batch_id,
        'total': len(events),
        'inserted': inserted,
        'skipped_duplicates': skipped,
        'updated': updated,   # [2026-08-12] 已存在且标签/状态变化的事件数（家属改判真实/误报）
        'errors': errors,
    }, message='接收完成')
