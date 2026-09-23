"""
实时跌倒检测推理 API

POST /api/inference/frame  — 上传一帧，添加到滑动窗口
POST /api/inference/detect — 对当前窗口推理，返回 P(fall)
GET  /api/inference/status — 返回推理器状态
POST /api/inference/reset — 清空缓冲区
"""

import time
import base64
from flask import Blueprint, request, Response
from app.utils.response import success_response, error_response
from app.inference import orchestrator

inference_bp = Blueprint('inference', __name__)


@inference_bp.route('/inference/frame', methods=['POST'])
def add_frame():
    """接收一帧图像（base64 JPEG），添加到滑动窗口"""
    try:
        body = request.get_json() or {}
        data = body.get('image', '')  # base64 编码的 JPEG

        if ',' in data:
            data = data.split(',', 1)[1]  # 去掉 data:image/jpeg;base64, 前缀

        image_bytes = base64.b64decode(data)
        orchestrator.add_frame(image_bytes)

        return success_response({
            'buffer_size': orchestrator.get_status()['buffer_size'],
            'added': True,
        })

    except Exception as e:
        return error_response(f'帧添加失败: {str(e)}', 400)


@inference_bp.route('/inference/detect', methods=['POST'])
def detect():
    """对当前滑动窗口推理"""
    try:
        t0 = time.time()
        result = orchestrator.infer()
        result['total_ms'] = round((time.time() - t0) * 1000, 1)
        return success_response(result)

    except Exception as e:
        return error_response(f'推理失败: {str(e)}', 500)


@inference_bp.route('/inference/detect-with-frame', methods=['POST'])
def detect_with_frame():
    """接收一帧 + 立即推理（简化接口，适合前端调用）"""
    try:
        body = request.get_json() or {}
        data = body.get('image', '')

        if ',' in data:
            data = data.split(',', 1)[1]

        image_bytes = base64.b64decode(data)
        orchestrator.add_frame(image_bytes)

        result = orchestrator.infer()
        return success_response(result)

    except Exception as e:
        return error_response(f'推理失败: {str(e)}', 500)


@inference_bp.route('/inference/status', methods=['GET'])
def status():
    """推理器状态"""
    return success_response(orchestrator.get_status())


@inference_bp.route('/inference/reset', methods=['POST'])
def reset():
    """清空推理缓冲区"""
    orchestrator.reset()
    return success_response({'reset': True})


@inference_bp.route('/inference/threshold', methods=['POST'])
def set_threshold():
    """设置跌倒检测阈值"""
    body = request.get_json() or {}
    threshold = body.get('threshold', 0.5)
    orchestrator.set_threshold(float(threshold))
    return success_response({'threshold': orchestrator.get_status()['threshold']})


# === RTSP 直连视频捕获（低延迟） ===

@inference_bp.route('/inference/capture/start', methods=['POST'])
def start_capture():
    """启动 RTSP 视频流捕获 + 连续推理。

    Body:
        mode: 'spatial_v5'(演示默认) / 'kd' / 'worldav' / 'worldpose'
        yolo_gate: bool, True = YOLO 先识别人像再推理(默认), False = 直接检测
    """
    from app.inference import video_capture
    body = request.get_json(silent=True) or {}
    mode = body.get('mode', 'spatial_v5')
    yolo_gate = body.get('yolo_gate', True)
    video_capture.start_capture(mode=mode, yolo_gate=yolo_gate)
    return success_response({'capture': 'started', 'mode': mode, 'yolo_gate': yolo_gate})


@inference_bp.route('/inference/source', methods=['GET'])
def capture_source():
    """返回当前采集源类型；不泄露 RTSP 凭据或本地绝对路径。"""
    from app.inference.inference_config import RTSP_URL
    is_file = not RTSP_URL.startswith(('rtsp://', 'http://', 'https://'))
    return success_response({
        'kind': 'file' if is_file else 'camera',
        'label': '测试视频源' if is_file else '摄像头视频源',
    })


@inference_bp.route('/inference/capture/stop', methods=['POST'])
def stop_capture():
    """停止 RTSP 捕获"""
    from app.inference import video_capture
    video_capture.stop_capture()
    return success_response({'capture': 'stopped'})


@inference_bp.route('/inference/capture/play-once', methods=['POST'])
def play_capture_once():
    """录制演示专用：从文件开头运行一次真实检测，结束后自动待机。"""
    from app.inference import video_capture
    body = request.get_json(silent=True) or {}
    mode = body.get('mode', 'spatial_v5')
    yolo_gate = body.get('yolo_gate', False)
    if video_capture.is_capturing():
        video_capture.stop_capture()
    video_capture.start_capture(mode=mode, yolo_gate=yolo_gate, one_shot=True)
    return success_response({
        'capture': 'started',
        'mode': mode,
        'yolo_gate': yolo_gate,
        'one_shot': True,
    })


@inference_bp.route('/inference/live-prob', methods=['GET'])
def live_prob():
    """前端轮询用：返回最新 P(fall) + 人形检测 + 推理器全量状态 + WorldAV 指标"""
    status = orchestrator.get_status()
    try:
        result = orchestrator.infer()
        if result.get('ready'):
            status.update(result)
    except Exception:
        pass

    # 附加管线状态（优先 WorldAV，降级到 KD-Stride-2）
    try:
        from app.inference.detection_pipeline import get_pipeline_status
        pipe = get_pipeline_status()
        status.update({
            'person_count': pipe['last_persons'],
            'yolo_time_ms': pipe['yolo_time_ms'],
            'pipeline_active': True,
        })
    except Exception:
        status['pipeline_active'] = False

    # 附加 WorldAV 特定指标
    try:
        from app.inference.world_av_pipeline import get_pipeline_status as wp_status
        wp = wp_status()
        status.update({
            'world_av_available': True,
            'p_fall': wp.get('fall_prob', 0),
            'model_mode': wp.get('model_mode', 'spatial_v5'),
            'threshold': wp.get('detector_threshold', 0.669911),
            'p_final': wp.get('p_final', wp.get('fall_prob', 0)),
            'p_visual': wp.get('p_visual', 0),
            'p_av': wp.get('p_av', 0),
            'p_world': wp.get('p_world', wp.get('world_probability', 0)),
            'p_pose': wp.get('p_pose', wp.get('pose_probability', 0)),
            'p_future_warning': wp.get('p_future_warning', 0),
            'warning': wp.get('warning', False),
            'alarm': wp.get('alarm', wp.get('alert_active', False)),
            'inference_ms': wp.get('inference_ms', wp.get('world_av_ms', 0)),
            'warmup_ready': wp.get('warmup_ready', False),
            'gate': wp.get('gate', 0),
            'audio_available': wp.get('audio_available', False),
            'alert_active': wp.get('alert_active', False),
            'fallback_mode': wp.get('fallback_mode', False),
            'pose_result': wp.get('pose_result'),
            'model_hz': wp.get('model_hz', 4.0),
            'actual_model_hz': wp.get('actual_model_hz', 0.0),
            'pose_cache_hit_rate': wp.get('pose_cache_hit_rate', 0.0),
            'dropped_stale_windows': wp.get('dropped_stale_windows', 0),
            'last_frame_timestamp': wp.get('last_frame_timestamp', 0.0),
        })
    except Exception:
        status['world_av_available'] = False

    # capture 运行状态（前端刷新后用于恢复 LIVE 状态）
    try:
        from app.inference import video_capture
        status['capture_running'] = video_capture.is_capturing()
        status['one_shot'] = video_capture.is_one_shot()
    except Exception:
        status['capture_running'] = False

    return success_response(status)


@inference_bp.route('/inference/pipeline/status', methods=['GET'])
def pipeline_status():
    """两级/三级检测管线专用状态（含 WorldAV 指标 + capture 运行状态）"""
    try:
        from app.inference.world_av_pipeline import get_pipeline_status as wp_status
        from app.inference import video_capture
        status = wp_status()
        status['capture_running'] = video_capture.is_capturing()
        status['one_shot'] = video_capture.is_one_shot()
        # 云台自动追踪状态（前端开关/方向显示）
        try:
            from app.inference.ptz_tracker import tracking_status
            status['tracking'] = tracking_status()
        except Exception:
            status['tracking'] = {'running': False}
        return success_response(status)
    except Exception:
        try:
            from app.inference.detection_pipeline import get_pipeline_status
            return success_response(get_pipeline_status())
        except Exception as e:
            return error_response(str(e), 500)


@inference_bp.route('/inference/worldav/status', methods=['GET'])
def worldav_status():
    """WorldAV 模型状态（是否可用、当前模式等）"""
    try:
        from app.inference.world_av_pipeline import is_available, get_pipeline_status as wp_status
        status = wp_status()
        status['world_av_available'] = True
        status['mode'] = 'worldav' if is_available() else 'kd-fallback'
        return success_response(status)
    except Exception as e:
        return success_response({
            'world_av_available': False,
            'mode': 'kd-fallback',
            'error': str(e),
        })


_PLACEHOLDER_JPEG: bytes | None = None


def _get_placeholder_jpeg() -> bytes:
    """[2026-08-13] 灰底占位 JPEG(640×360, 带等待文案), 无标注帧时返回, 避免前端纯灰屏。"""
    global _PLACEHOLDER_JPEG
    if _PLACEHOLDER_JPEG is None:
        import cv2
        import numpy as np
        img = np.full((360, 640, 3), (46, 39, 35), dtype=np.uint8)  # 深灰 BGR
        cv2.putText(img, "waiting for detection frame...", (34, 180),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (205, 205, 205), 2, cv2.LINE_AA)
        ok, jpeg = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        _PLACEHOLDER_JPEG = jpeg.tobytes() if ok else b''
    return _PLACEHOLDER_JPEG


@inference_bp.route('/inference/annotated-frame', methods=['GET'])
def annotated_frame():
    """返回最新标注帧（带人形框+跌倒概率的 JPEG base64）"""
    try:
        from app.inference.detection_pipeline import get_annotated_frame
        jpeg = get_annotated_frame()
        if jpeg is None:
            # [2026-08-13] 无帧时返回占位图而非 404 —— 前端 `annotatedImage` 恒有值, 消除灰屏
            jpeg = _get_placeholder_jpeg()
        b64 = base64.b64encode(jpeg).decode()
        return success_response({'image': f'data:image/jpeg;base64,{b64}'})
    except Exception as e:
        return error_response(str(e), 500)


@inference_bp.route('/inference/annotated-frame.jpg', methods=['GET'])
def annotated_frame_jpeg():
    """Return the latest annotated frame as binary JPEG for the live view.

    The legacy JSON/Base64 endpoint remains available for existing panels; the
    binary path avoids a second Base64 expansion and is the only source used by
    the demo monitor.
    """
    try:
        from app.inference.detection_pipeline import get_annotated_frame
        jpeg = get_annotated_frame() or _get_placeholder_jpeg()
        return Response(jpeg, mimetype='image/jpeg', headers={"Cache-Control": "no-store"})
    except Exception as e:
        return error_response(str(e), 500)


@inference_bp.route('/inference/raw-frame', methods=['GET'])
def raw_frame():
    """返回检测管线刚读取的原始帧，用于让录制页面与真实推理严格同步。"""
    try:
        import cv2
        from app.inference import world_av_pipeline
        frame = world_av_pipeline._last_raw_frame
        if frame is None:
            jpeg = _get_placeholder_jpeg()
        else:
            ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
            jpeg = encoded.tobytes() if ok else _get_placeholder_jpeg()
        b64 = base64.b64encode(jpeg).decode()
        return success_response({'image': f'data:image/jpeg;base64,{b64}'})
    except Exception as e:
        return error_response(str(e), 500)
