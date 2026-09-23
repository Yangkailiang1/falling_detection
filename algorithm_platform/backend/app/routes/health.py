"""
# 算法迭代平台 - 健康检查路由
"""
from flask import Blueprint
from config import Config
from app.models import event_store
from app.utils.response import success_response

health_bp = Blueprint('health', __name__)


@health_bp.route('/health', methods=['GET'])
def health():
    """GET /api/health — 健康检查 + 基础状态"""
    return success_response({
        'status': 'ok',
        'version': '1.0.0',
        'service': '算法迭代平台',
        'db_count': event_store.count(),
        'api_key_configured': bool(Config.ALGO_API_KEY),
    })
