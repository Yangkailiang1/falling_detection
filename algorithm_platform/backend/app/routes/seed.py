"""
# 算法迭代平台 - 演示数据播种路由
# 功能: 生成一批脱敏演示事件（含合成骨骼序列），保证演示现场有数据
"""
from flask import Blueprint, request
from app.models import event_store
from app.services.demo_generator import generate_demo_events
from app.utils.auth import require_api_key
from app.utils.response import success_response, error_response

seed_bp = Blueprint('seed', __name__)


@seed_bp.route('/seed/demo', methods=['POST'])
@require_api_key
def seed_demo():
    """POST /api/seed/demo — Body {count?, force?} 生成演示事件并入库。
    force=false 时若库中已有 >= count 条则跳过（幂等）"""
    data = request.get_json(silent=True) or {}
    try:
        count = max(1, min(int(data.get('count', 40)), 200))
    except (TypeError, ValueError):
        return error_response('count 非法', 400)
    force = bool(data.get('force', False))

    if not force and event_store.count() >= count:
        existing = event_store.count()
        return success_response(
            {'inserted': 0, 'total': 0, 'existing': existing, 'skipped': True},
            message='库中已有足够事件，跳过播种（force=true 可强制）')

    events = generate_demo_events(count=count)
    inserted, skipped, updated = event_store.insert_many(events)
    return success_response(
        {'inserted': inserted, 'total': count, 'existing': event_store.count(),
         'skipped_duplicates': skipped, 'updated': updated},
        message='演示数据生成完成')
