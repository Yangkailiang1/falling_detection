"""
# 管理端通知收件箱路由 [V7.2 新增]
# 功能: 跌倒事件通知的查询与已读标记（替代萤石APP推送）
# 对应文档: 萤石平台/通知链路与消息推送调研报告.md §二（管理端通知收件箱）
"""
import logging
from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

notifications_bp = Blueprint('notifications', __name__)


@notifications_bp.route('/notifications', methods=['GET'])
def list_notifications():
    """
    查询管理端通知列表（最新在前）

    GET /api/notifications?limit=50
    返回: {"code": 200, "items": [{id, created_at, title, body, extras, read}, ...]}
    """
    try:
        from app.services.notification_inbox import list_notifications
        limit = request.args.get('limit', default=50, type=int)
        limit = max(1, min(limit, 200))
        items = list_notifications(limit=limit)
        return jsonify({"code": 200, "items": items, "total": len(items)}), 200
    except Exception as e:
        logger.error(f"[Notifications] 查询失败: {e}")
        return jsonify({"code": 500, "msg": str(e)}), 500


@notifications_bp.route('/notifications/<message_id>/read', methods=['POST'])
def mark_read(message_id: str):
    """
    标记通知已读

    POST /api/notifications/<message_id>/read
    返回: {"code": 200, "marked": bool}
    """
    try:
        from app.services.notification_inbox import mark_read
        marked = mark_read(message_id)
        if not marked:
            return jsonify({"code": 404, "msg": "通知不存在"}), 404
        return jsonify({"code": 200, "marked": True}), 200
    except Exception as e:
        logger.error(f"[Notifications] 标记已读失败: {e}")
        return jsonify({"code": 500, "msg": str(e)}), 500
