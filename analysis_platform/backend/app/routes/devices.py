"""
# 对应文档: 二.V2.0 - 设备管理路由
# 功能: 设备列表、详情、状态查询 + 视频编码配置API端点
"""
from flask import Blueprint, request
from app.utils.response import success_response, error_response, paginated_response
from app.services import ezviz_device
from app.services.ezviz_video_config import set_video_encode, set_substream_fall_detection
from app.utils.exceptions import DeviceNotFoundError, EzvizAPIError

devices_bp = Blueprint('devices', __name__)


# [开发文档 三.3.3 - GET /api/devices]
# 获取设备列表（分页）
@devices_bp.route('/devices', methods=['GET'])
def get_devices():
    """
    获取账号下所有设备列表
    - 对应文档章节: 三.3.3 - 设备列表API
    - 参数: page(query)页码, page_size(query)每页条数
    - 返回: 分页的设备列表
    """
    try:
        page = request.args.get('page', 0, type=int)
        page_size = request.args.get('page_size', 20, type=int)
        result = ezviz_device.get_device_list(page, page_size)
        return paginated_response(
            result.get('list', []),
            result.get('total', 0),
            page, page_size
        )
    except EzvizAPIError as e:
        return error_response(str(e), 502)


# [开发文档 三.3.3 - GET /api/devices/:deviceSerial]
# 获取设备详情
@devices_bp.route('/devices/<device_serial>', methods=['GET'])
def get_device_detail(device_serial):
    """
    获取指定设备的详细信息
    - 对应文档章节: 三.3.3 - 设备详情API
    """
    try:
        info = ezviz_device.get_device_info(device_serial)
        return success_response(info)
    except DeviceNotFoundError as e:
        return error_response(str(e), 404)
    except EzvizAPIError as e:
        return error_response(str(e), 502)


# [开发文档 三.3.3 - GET /api/devices/:deviceSerial/status]
# 获取设备在线状态
@devices_bp.route('/devices/<device_serial>/status', methods=['GET'])
def get_device_status(device_serial):
    """
    获取指定设备的在线状态
    - 对应文档章节: 三.3.3 - 设备状态API
    """
    try:
        status = ezviz_device.get_device_status(device_serial)
        return success_response(status)
    except EzvizAPIError as e:
        return error_response(str(e), 502)


# 获取设备能力集
@devices_bp.route('/devices/<device_serial>/ability', methods=['GET'])
def get_device_ability(device_serial):
    """
    获取设备支持的功能能力集
    - 对应文档章节: 二.V2.0 - 设备能力集
    """
    try:
        ability = ezviz_device.get_device_ability(device_serial)
        return success_response(ability)
    except EzvizAPIError as e:
        return error_response(str(e), 502)


# [新增] 视频编码参数配置 — POST /api/devices/<serial>/video-config
@devices_bp.route('/devices/<device_serial>/video-config', methods=['POST'])
def set_video_config(device_serial):
    """
    设置设备视频编码参数（主码流/子码流分辨率、帧率、码率）
    - 萤石API: POST /api/lapp/device/video/encode/set
    - 请求体:
        {
          "stream_type": 2,       // 1=主码流, 2=子码流（默认2）
          "resolution": "720p",   // '720p'|'1080p'
          "frame_rate": 20,       // 2|4|6|8|10|12|15|16|18|20|22 或 'full'
          "bit_rate": 1024,       // Kbps: 256|384|512|768|1024|1536|2048
          "encode_type": 1        // 1=H.264, 2=H.265（默认1）
        }
    """
    try:
        body = request.get_json(silent=True) or {}
        result = set_video_encode(
            device_serial=device_serial,
            stream_type_in=body.get('stream_type', 2),
            resolution=body.get('resolution', '720p'),
            frame_rate=body.get('frame_rate', 20),
            bit_rate=body.get('bit_rate', 1024),
            encode_type=body.get('encode_type', 1),
        )
        return success_response({
            'message': '视频编码参数已设置',
            'config': body,
            'raw_response': result
        })
    except EzvizAPIError as e:
        return error_response(str(e), 502)


# [新增] 一键跌倒检测优化配置 — POST /api/devices/<serial>/video-config/fall-detect
@devices_bp.route('/devices/<device_serial>/video-config/fall-detect', methods=['POST'])
def set_fall_detect_config(device_serial):
    """
    一键设置子码流为跌倒检测最优参数: 720p@20fps H.264 1Mbps
    """
    try:
        result = set_substream_fall_detection(device_serial)
        return success_response({
            'message': '子码流已优化为跌倒检测参数: 720p@20fps H.264 1Mbps',
            'raw_response': result
        })
    except EzvizAPIError as e:
        return error_response(str(e), 502)
