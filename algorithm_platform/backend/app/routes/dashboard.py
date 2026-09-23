"""
# 算法迭代平台 - 仪表盘统计路由
"""
from flask import Blueprint, request
from app.models import event_store
from app.utils.response import success_response, error_response

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/stats', methods=['GET'])
def stats():
    """GET /api/stats — 聚合统计"""
    return success_response(event_store.stats())


@dashboard_bp.route('/stats/trend', methods=['GET'])
def trend():
    """GET /api/stats/trend?days=30 — 近 N 天按日 falls/false_alarms/total"""
    try:
        days = min(int(request.args.get('days', 30)), 365)
    except ValueError:
        return error_response('days 非法', 400)
    return success_response(event_store.trend(days))
