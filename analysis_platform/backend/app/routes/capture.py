"""
# 对应文档: 二.V2.0 - 抓拍路由
# 功能: 设备抓拍API端点
"""
from flask import Blueprint, request
from app.utils.response import success_response, error_response
from app.services import ezviz_capture
from app.utils.exceptions import EzvizAPIError

capture_bp = Blueprint('capture', __name__)


# [开发文档 三.3.3 - POST /api/capture/:deviceSerial]
# 触发设备抓拍
@capture_bp.route('/capture/<device_serial>', methods=['POST'])
def trigger_capture(device_serial):
    """
    触发指定设备拍摄一张图片
    - 对应文档章节: 三.3.3 - 抓拍API
    - 请求体: {'channel_no': 1}
    """
    try:
        body = request.get_json() or {}
        channel = body.get('channel_no', 1)
        result = ezviz_capture.capture_device(device_serial, channel)
        return success_response(result, "抓拍成功")
    except EzvizAPIError as e:
        return error_response(str(e), 502)


# [开发文档 二.V2.0 - 批量抓拍]
@capture_bp.route('/capture/batch', methods=['POST'])
def batch_capture():
    """
    批量触发多设备抓拍
    - 对应文档章节: 二.V3.0 - 抓拍管理页面
    - 请求体: {'device_serials': ['xxx', 'yyy']}
    """
    try:
        body = request.get_json() or {}
        seriels = body.get('device_serials', [])
        if not seriels:
            return error_response("请提供设备序列号列表", 400)
        results = ezviz_capture.batch_capture(seriels)
        return success_response(results)
    except EzvizAPIError as e:
        return error_response(str(e), 502)
