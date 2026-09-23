"""
# 对应文档: 二.V2.0 - 云台控制路由
# 功能: 云台控制API端点
"""
from flask import Blueprint, request
from app.utils.response import success_response, error_response
from app.services import ezviz_ptz
from app.utils.exceptions import EzvizAPIError

ptz_bp = Blueprint('ptz', __name__)


# [开发文档 三.3.3 - POST /api/ptz/:deviceSerial/start]
# 开始云台转动
@ptz_bp.route('/ptz/<device_serial>/start', methods=['POST'])
def start_ptz(device_serial):
    """
    开始云台向指定方向转动
    - 对应文档章节: 三.3.3 - 云台控制开始API
    - 请求体: {'direction': 'up'/'down'/'left'/'right', 'speed': 2}
    """
    try:
        body = request.get_json() or {}
        direction = body.get('direction', 'up')
        speed = body.get('speed', 2)
        result = ezviz_ptz.start_ptz_move(device_serial, direction, speed)
        return success_response(result)
    except ValueError as e:
        return error_response(str(e), 400)
    except EzvizAPIError as e:
        return error_response(str(e), 502)


# [开发文档 三.3.3 - POST /api/ptz/:deviceSerial/stop]
# 停止云台转动
@ptz_bp.route('/ptz/<device_serial>/stop', methods=['POST'])
def stop_ptz(device_serial):
    """
    停止云台转动
    - 对应文档章节: 三.3.3 - 云台控制停止API
    """
    try:
        result = ezviz_ptz.stop_ptz_move(device_serial)
        return success_response(result)
    except EzvizAPIError as e:
        return error_response(str(e), 502)


# [开发文档 二.V2.0 - 快捷云台控制]
# 转动指定时间后自动停止
@ptz_bp.route('/ptz/<device_serial>/quick', methods=['POST'])
def quick_ptz(device_serial):
    """
    快捷云台控制：转动指定毫秒后自动停止
    - 对应文档章节: 二.V3.0 - 云台控制面板
    - 请求体: {'direction': 'up', 'duration_ms': 500, 'speed': 3}
    """
    try:
        body = request.get_json() or {}
        direction = body.get('direction', 'up')
        duration = body.get('duration_ms', 500)
        speed = body.get('speed', 3)
        result = ezviz_ptz.quick_ptz_move(device_serial, direction, duration, speed)
        return success_response(result)
    except ValueError as e:
        return error_response(str(e), 400)
    except EzvizAPIError as e:
        return error_response(str(e), 502)


# [开发文档 二.V2.0 - 云台能力查询]
@ptz_bp.route('/ptz/<device_serial>/ability', methods=['GET'])
def ptz_ability(device_serial):
    """
    查询设备的云台控制能力
    - 对应文档章节: 二.V2.0 - 设备能力集
    """
    try:
        ability = ezviz_ptz.get_ptz_ability(device_serial)
        return success_response(ability)
    except EzvizAPIError as e:
        return error_response(str(e), 502)
