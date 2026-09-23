"""
# 算法迭代平台 - 数据集导出路由
# 功能: 导出全部脱敏事件 JSON / s-jepa 训练数据集 zip（均需 X-API-Key）
"""
import io
from flask import Blueprint, request, send_file
from app.models import event_store
from app.services.export_service import build_events_json, build_training_zip
from app.utils.auth import require_api_key
from app.utils.response import error_response

export_bp = Blueprint('export', __name__)


@export_bp.route('/export/events', methods=['GET'])
@require_api_key
def export_events():
    """GET /api/export/events — 全部脱敏事件 JSON 下载"""
    events = event_store.all_payloads()
    data = build_events_json(events)
    return send_file(
        io.BytesIO(data),
        mimetype='application/json',
        as_attachment=True,
        download_name='falling_events_anonymized.json',
    )


@export_bp.route('/export/training', methods=['GET'])
@require_api_key
def export_training():
    """GET /api/export/training?scene_prefix=Home_01 — s-jepa 训练数据集 zip"""
    events = event_store.all_payloads()
    scene_prefix = (request.args.get('scene_prefix') or '').strip()
    try:
        data = build_training_zip(events, scene_prefix=scene_prefix)
    except Exception as e:  # 骨骼序列缺失/畸形
        return error_response(f'构建训练数据集失败: {e}', 500)
    if len(data) < 200:  # 空 zip 头部 ~160B
        return error_response('暂无可导出的骨骼序列事件', 400)
    return send_file(
        io.BytesIO(data),
        mimetype='application/zip',
        as_attachment=True,
        download_name='falling_dataset.zip',
    )
