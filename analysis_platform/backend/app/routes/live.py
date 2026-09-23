"""
# 对应文档: 二.V2.0 - 直播路由
# 功能: 获取摄像头直播流地址API端点 + MJPEG代理推流
"""
from flask import Blueprint, request, Response
from app.utils.response import success_response, error_response
from app.services import ezviz_live
from app.services.stream_relay import StreamRelay
from app.utils.exceptions import EzvizAPIError

live_bp = Blueprint('live', __name__)


# [开发文档 三.3.3 - GET /api/live/:deviceSerial/address]
# 获取直播流地址
@live_bp.route('/live/<device_serial>/address', methods=['GET'])
def get_live_address(device_serial):
    """
    获取指定设备的直播流地址（默认子码流H264）
    - 对应文档章节: 三.3.3 - 直播地址API
    - 参数: protocol(query)协议, quality(query)清晰度, support_h265(query)H265支持
    """
    try:
        protocol = request.args.get('protocol', None, type=int)
        quality = request.args.get('quality', 2, type=int)
        support_h265 = request.args.get('support_h265', 0, type=int)
        result = ezviz_live.get_live_address(
            device_serial, protocol, quality=quality, support_h265=support_h265
        )
        return success_response(result)
    except EzvizAPIError as e:
        return error_response(str(e), 502)


# [开发文档 三.3.3 - GET /api/live/:deviceSerial/channels]
# 获取所有协议的直播地址
@live_bp.route('/live/<device_serial>/channels', methods=['GET'])
def get_all_addresses(device_serial):
    """
    获取设备所有可用协议的直播地址
    - 对应文档章节: 二.V2.0 - 多协议直播
    """
    try:
        results = ezviz_live.get_all_live_addresses(device_serial)
        return success_response(results)
    except EzvizAPIError as e:
        return error_response(str(e), 502)


# [开发文档 二.V2.0 - 直播通道列表]
@live_bp.route('/live/channels', methods=['GET'])
def get_channels():
    """
    获取直播通道列表
    - 对应文档章节: 二.V2.0 - 直播通道查询
    """
    try:
        device_serial = request.args.get('deviceSerial', None)
        channels = ezviz_live.get_live_channels(device_serial)
        return success_response(channels)
    except EzvizAPIError as e:
        return error_response(str(e), 502)


# ===== 流代理（MJPEG）— 解决并发观看人数限制 =====

@live_bp.route('/live/<device_serial>/stream', methods=['GET'])
def stream_mjpeg(device_serial):
    """
    MJPEG 代理推流 — 后端1个连接从云端取流，转推到前端
    - 对应文档: CLAUDE.md 七 - 流代理
    - 多个浏览器连此端点只占1个云端观看名额
    """
    StreamRelay.start(device_serial)
    return Response(
        _mjpeg_generator(device_serial),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )


def _mjpeg_generator(device_serial):
    """MJPEG帧流生成器 — 持续产出JPEG帧"""
    import time
    while True:
        frame = StreamRelay.get_frame(device_serial)
        if frame is not None:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        else:
            time.sleep(0.1)


@live_bp.route('/live/<device_serial>/stream/start', methods=['POST'])
def start_stream_relay(device_serial):
    """手动启动流代理"""
    StreamRelay.start(device_serial)
    return success_response({'status': 'started', 'device_serial': device_serial})


@live_bp.route('/live/<device_serial>/stream/stop', methods=['POST'])
def stop_stream_relay(device_serial):
    """手动停止流代理"""
    StreamRelay.stop(device_serial)
    return success_response({'status': 'stopped', 'device_serial': device_serial})


@live_bp.route('/live/<device_serial>/stream/status', methods=['GET'])
def stream_relay_status(device_serial):
    """查询流代理状态"""
    return success_response(StreamRelay.status(device_serial))
