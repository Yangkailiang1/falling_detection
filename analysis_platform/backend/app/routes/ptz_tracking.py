"""
YOLO 驱动的云台自动追踪 API

POST /api/ptz-tracking/start   — 启动自动追踪
POST /api/ptz-tracking/stop    — 停止追踪并停住云台
GET  /api/ptz-tracking/status  — 追踪状态
"""

from flask import Blueprint, request
from app.utils.response import success_response, error_response
from app.inference.ptz_tracker import start_tracking, stop_tracking, tracking_status
from app.inference.inference_config import DEVICE_SERIAL, TEST_PHONE

ptz_tracking_bp = Blueprint('ptz_tracking', __name__)


@ptz_tracking_bp.route('/ptz-tracking/start', methods=['POST'])
def start():
    """启动基于 YOLO 人形位置的云台自动追踪"""
    try:
        # silent=True: 无 JSON body 时返回 None 而不是抛 415
        body = request.get_json(silent=True) or {}
        device_serial = body.get('device_serial', DEVICE_SERIAL)
        started = start_tracking(device_serial)
        status = tracking_status()
        status['just_started'] = started
        return success_response(status)
    except Exception as e:
        return error_response(f'追踪启动失败: {str(e)}', 500)


@ptz_tracking_bp.route('/ptz-tracking/stop', methods=['POST'])
def stop():
    """停止自动追踪（并立即停住云台）"""
    try:
        stopped = stop_tracking()
        status = tracking_status()
        status['just_stopped'] = stopped
        return success_response(status)
    except Exception as e:
        return error_response(f'追踪停止失败: {str(e)}', 500)


@ptz_tracking_bp.route('/ptz-tracking/status', methods=['GET'])
def status():
    """查询自动追踪状态"""
    try:
        return success_response(tracking_status())
    except Exception as e:
        return error_response(str(e), 500)
