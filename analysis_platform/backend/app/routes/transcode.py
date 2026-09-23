"""
# 对应文档: CLAUDE.md 七 - 转码路由
# 功能: 提供 ffmpeg 转码后的本地 HLS 文件 + 转码控制
"""
import os
from flask import Blueprint, send_from_directory, request, Response, make_response
from app.utils.response import success_response, error_response
from app.services.transcoder import StreamTranscoder

transcode_bp = Blueprint('transcode', __name__)


@transcode_bp.route('/transcode/<device_serial>/<path:filename>')
def serve_hls(device_serial, filename):
    """提供转码后的 HLS 文件（.m3u8 和 .ts 片段）"""
    hls_dir = StreamTranscoder.get_hls_path(device_serial)
    if not hls_dir:
        return Response(
            "Transcoder not started. Please call POST /api/transcode/{}/start first.".format(
                device_serial
            ),
            status=404,
            content_type='text/plain'
        )

    # 根据文件扩展名设置正确的 Content-Type
    ext = os.path.splitext(filename)[1].lower()
    content_types = {
        '.m3u8': 'application/vnd.apple.mpegurl',
        '.ts':   'video/mp2t',
    }
    mimetype = content_types.get(ext, None)

    response = make_response(send_from_directory(hls_dir, filename))
    if mimetype:
        response.headers['Content-Type'] = mimetype

    # 设置 CORS 头（虽然 Vite 代理处理了同源，但加一层保护）
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'

    return response


@transcode_bp.route('/transcode/<device_serial>/start', methods=['POST'])
def start_transcode(device_serial):
    """启动转码"""
    try:
        result = StreamTranscoder.start(device_serial)
        return success_response(result)
    except Exception as e:
        return error_response(str(e), 500)


@transcode_bp.route('/transcode/<device_serial>/stop', methods=['POST'])
def stop_transcode(device_serial):
    """停止转码"""
    StreamTranscoder.stop(device_serial)
    return success_response({'device_serial': device_serial, 'status': 'stopped'})


@transcode_bp.route('/transcode/<device_serial>/status', methods=['GET'])
def transcode_status(device_serial):
    """查询转码状态"""
    return success_response(StreamTranscoder.status(device_serial))
