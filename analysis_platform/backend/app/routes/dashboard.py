"""
# 对应文档: 四.V4.0 - 仪表盘路由
# 功能: 系统概览统计API端点
"""
from flask import Blueprint
from app.utils.response import success_response, error_response
from app.services import ezviz_device, ezviz_alarm
from app.utils.exceptions import EzvizAPIError
from config import Config

dashboard_bp = Blueprint('dashboard', __name__)


# [开发文档 三.3.3 - GET /api/dashboard/stats]
# 仪表盘统计数据
@dashboard_bp.route('/dashboard/stats', methods=['GET'])
def get_stats():
    """
    获取仪表盘统计数据
    - 对应文档章节: 四.V4.0 - 仪表盘统计数据
    - 返回:
        {'total_devices': int, 'online_devices': int, 'offline_devices': int,
         'today_alarms': int, 'captures_today': int, 'system': {...}}
    """
    try:
        # 设备统计
        try:
            devices = ezviz_device.get_device_list(0, 100)
            device_list = devices.get('list', [])
            total = devices.get('total', len(device_list))
            online = sum(1 for d in device_list if d.get('status', 0) == 1)
        except Exception:
            total = 0
            online = 0
            device_list = []

        # 告警统计
        try:
            alarms = ezviz_alarm.get_alarms_by_days(1)
            alarm_total = alarms.get('total', 0)
        except Exception:
            alarm_total = 0

        # 系统信息
        from app.services.ezviz_auth import token_manager
        result = {
            'total_devices': total,
            'online_devices': online,
            'offline_devices': total - online,
            'today_alarms': alarm_total,
            'devices': device_list[:5],  # 前5个设备
            'model_name': Config.EZS_CHAT_MODEL,
            'api_configured': bool(Config.EZS_APP_KEY),
            'token_valid': token_manager.is_valid()
        }
        return success_response(result)
    except Exception as e:
        return error_response(str(e))
