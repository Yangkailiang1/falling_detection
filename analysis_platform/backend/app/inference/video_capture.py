"""
RTSP 视频流捕获 + 两级/三级检测管线

支持两种模式:
  - kd: YOLO11n 人形检测 → KD-Stride-2 跌倒检测
  - spatial_v5 (演示默认): Spatial v5 + Temporal Residual → World-Pose/音频融合
  - worldav:  YOLO11n 人形检测 → WorldAV (V-JEPA2+Audio+Gate) → Pose 着地分析

    720p帧 → YOLO11n 人形检测 (75ms)
        ├─ 无人 → 跳过跌倒检测
        └─ 有人 → WorldAV/KD-Stride-2 跌倒检测
"""

import cv2
import os
import time
import select
import shutil
import threading
import logging
import subprocess
import numpy as np

from app.inference.inference_config import RTSP_URL, FRAME_INTERVAL

logger = logging.getLogger(__name__)

# ffmpeg 查找顺序: 环境变量 → 系统 PATH
def _find_ffmpeg() -> str:
    configured = os.environ.get("FFMPEG_PATH", "")
    if configured and os.path.exists(configured):
        return configured
    return shutil.which("ffmpeg") or "ffmpeg"

_capture_running = False
_capture_thread = None
_audio_thread = None
_cap = None
_frame_interval = FRAME_INTERVAL
_pipeline_enabled = True
_one_shot = False

# Pipeline mode: 'kd', 'worldav', 'worldpose' or 'spatial_v5'
_mode = 'spatial_v5'


def _init_rtsp():
    """Open RTSP video stream (cv2, 带超时防止阻塞)。"""
    cap = cv2.VideoCapture(RTSP_URL)
    try:
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass
    if not cap.isOpened():
        logger.error('Failed to open RTSP stream')
        return None
    return cap


def _probe_stream_size(fallback=(1280, 720)):
    """用 ffprobe 探测 RTSP 分辨率（ffmpeg 子进程解码前需要知道尺寸）。"""
    if not RTSP_URL.startswith('rtsp://'):
        cap = cv2.VideoCapture(RTSP_URL)
        try:
            if cap.isOpened():
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                if w > 0 and h > 0:
                    return w, h
        finally:
            cap.release()
    exe = _find_ffmpeg()
    probe = exe.replace('ffmpeg', 'ffprobe')
    if not os.path.exists(probe):
        probe = shutil.which('ffprobe') or ''
    if probe:
        try:
            import json as _json
            r = subprocess.run(
                [probe, '-v', 'error', '-select_streams', 'v:0',
                 '-show_entries', 'stream=width,height',
                 '-of', 'json', RTSP_URL],
                capture_output=True, timeout=10, text=True)
            if r.returncode == 0:
                d = _json.loads(r.stdout)
                w = d['streams'][0]['width']
                h = d['streams'][0]['height']
                if w and h:
                    return int(w), int(h)
        except Exception as e:
            logger.warning(f'ffprobe 失败({e}), 用默认 {fallback}')
    return fallback


def _audio_capture_loop():
    """Capture audio from RTSP stream via ffmpeg subprocess and feed to pipeline."""
    global _capture_running
    logger.info('Audio capture thread started')
    # Use ffmpeg to extract PCM audio from RTSP
    cmd = [_find_ffmpeg(), '-loglevel', 'error']
    if RTSP_URL.startswith('rtsp://'):
        cmd += ['-rtsp_transport', 'tcp']
    else:
        cmd += ['-re']
        if not _one_shot:
            cmd += ['-stream_loop', '-1']
    # 输出裸 PCM，避免把 WAV 头 44 字节误当作音频样本。
    cmd += ['-i', RTSP_URL, '-vn', '-acodec', 'pcm_s16le',
            '-ar', '22050', '-ac', '1', '-f', 's16le', 'pipe:1']
    retries = 0
    while _capture_running:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            chunk_size = 22050 * 2  # 1 second of 16-bit mono @ 22kHz
            while _capture_running and proc.poll() is None:
                raw = proc.stdout.read(chunk_size)
                if not raw:
                    break
                try:
                    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                    from app.inference.world_av_pipeline import push_audio
                    push_audio(audio, sample_rate=22050)
                except Exception as e:
                    logger.warning(f'Audio push error: {e}')
            proc.terminate()
            if _one_shot and not RTSP_URL.startswith('rtsp://'):
                break
        except Exception as e:
            logger.error(f'Audio capture error (retry {retries}): {e}')
            retries += 1
            if retries > 5:
                logger.error('Audio capture failed too many times, giving up')
                break
            time.sleep(2)
    logger.info('Audio capture thread stopped')


def _capture_loop():
    """Main video capture loop.

    RTSP 解码走 ffmpeg 子进程 → rawvideo 管道（cv2.VideoCapture 直接读 RTSP 在
    摄像头 HEVC 流不稳定时会在主进程触发 libavcodec/libavformat 原生 segfault，
    dmesg 实证 signal 11 [2026-08-13]）。ffmpeg 子进程崩溃只影响子进程，重启即可，
    不杀后端。
    """
    global _capture_running
    w, h = _probe_stream_size()
    frame_bytes = w * h * 3
    logger.info(f'ffmpeg capture 启动 ({w}x{h}, fps={_frame_interval*1000:.0f}ms)')
    while _capture_running:
        proc = None
        try:
            exe = _find_ffmpeg()
            cmd = [exe, '-loglevel', 'error']
            if RTSP_URL.startswith('rtsp://'):
                cmd += ['-rtsp_transport', 'tcp']
            else:
                # 本地文件作为真实检测输入时按原始时间轴读取，避免瞬间跑完整段视频。
                cmd += ['-re']
                if not _one_shot:
                    cmd += ['-stream_loop', '-1']
            cmd += ['-i', RTSP_URL, '-an', '-sn',
                    '-vf', f'fps={1.0/_frame_interval:.2f}',
                    '-f', 'rawvideo', '-pix_fmt', 'bgr24', 'pipe:1']
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            while _capture_running:
                # [2026-08-13] 帧精确读取: 管道 read 可能短读(部分字节), 累加到整帧再处理。
                # 旧代码一次 read(frame_bytes) 遇到短读就误判"流异常" → 每 ~2min 假重连,
                # 期间无新帧 → 跌倒漏检(17:28 实测)。另加 5s 无数据超时防 ffmpeg 静默卡死。
                raw = b''
                while _capture_running and len(raw) < frame_bytes:
                    if not select.select([proc.stdout], [], [], 5.0)[0]:
                        logger.warning('ffmpeg capture 无数据超时(5s), 重连...')
                        raw = b''
                        break
                    chunk = proc.stdout.read(frame_bytes - len(raw))
                    if not chunk:
                        break  # EOF / 流结束
                    raw += chunk
                if len(raw) < frame_bytes:
                    if _one_shot and not RTSP_URL.startswith('rtsp://'):
                        logger.info('演示视频播放完成，采集回到待机状态')
                        _capture_running = False
                    else:
                        logger.warning('ffmpeg capture 流异常, 重连...')
                    break
                frame = np.frombuffer(raw[:frame_bytes], np.uint8).reshape(h, w, 3)
                if _pipeline_enabled:
                    try:
                        from app.inference.detection_pipeline import process_frame_worldav
                        process_frame_worldav(frame)
                    except Exception as e:
                        logger.error(f'Pipeline error: {e}')
                else:
                    _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
                    from app.inference.orchestrator import add_frame_bytes
                    add_frame_bytes(jpeg.tobytes())
        except Exception as e:
            logger.error(f'ffmpeg capture error: {e}')
        finally:
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
        if _capture_running:
            time.sleep(1)  # 重连间隔 [2026-08-13] 2s→1s, 缩短漏检窗口
    logger.info('Capture loop stopped')


def start_capture(mode: str = 'kd', yolo_gate: bool = True, one_shot: bool | None = None):
    """
    Start RTSP capture with pipeline.

    Args:
        mode: 'kd' (default, KD-Stride-2) or 'worldav' (V-JEPA2+Audio).
        yolo_gate: True = YOLO 先识别人像再推理; False = 直接检测.
    """
    global _capture_running, _capture_thread, _audio_thread, _mode, _one_shot
    if _capture_running:
        return
    _mode = mode
    _one_shot = (os.getenv('DEMO_ONE_SHOT', '0') == '1') if one_shot is None else bool(one_shot)
    from app.inference.orchestrator import reset
    reset()
    # 设置推理引擎模式 (模型 + YOLO 门控)
    from app.inference import world_av_pipeline
    world_av_pipeline.set_mode(mode=mode, yolo_gate=yolo_gate)
    world_av_pipeline.reset_pipeline()
    # 演示视频已确认带 AAC 音轨；先标记音频通道预期可用，避免首个 PCM 块
    # 到达前前端误显示 OFF。实际波形仍由下方 ffmpeg 线程喂入模型。
    _audio_enabled = os.getenv("RTSP_AUDIO_ENABLED", "1") != "0"
    # 当前演示 MP4 已确认包含 AAC 单声道音轨；即使启动脚本环境变量未传入，
    # 也必须启动解码线程并将音频状态显示为 ON。
    _local_demo_audio = RTSP_URL.lower().endswith(('.mp4', '.mov', '.mkv'))
    _audio_expected = (
        RTSP_URL.startswith('rtsp://') or _local_demo_audio or os.getenv('DEMO_AUDIO_FROM_VIDEO', '0') == '1'
    ) and mode in ('worldav', 'worldpose', 'v2', 'worldpose_v2', 'spatial_v5', 'v5', 'spatial')
    world_av_pipeline.set_audio_stream_expected(_audio_expected)
    _capture_running = True
    _capture_thread = threading.Thread(target=_capture_loop, daemon=True)
    _capture_thread.start()
    # 本地文件/测试源(无音轨)不启动音频线程; 仅 RTSP 输入喂音频给 AV 融合门控
    # [2026-08-13] RTSP_AUDIO_ENABLED=0 可关闭第二条 RTSP 音频连接——
    # C6C 并发 RTSP 会话会导致视频流周期性被挤断(重连漏检)。默认保留音频以验证根因。
    if (_mode in ('worldav', 'worldpose', 'v2', 'worldpose_v2', 'spatial_v5', 'v5', 'spatial')
            and (RTSP_URL.startswith('rtsp://') or _local_demo_audio or os.getenv('DEMO_AUDIO_FROM_VIDEO', '0') == '1')
            and (_audio_enabled or _local_demo_audio)):
        _audio_thread = threading.Thread(target=_audio_capture_loop, daemon=True)
        _audio_thread.start()
    source_kind = 'RTSP' if RTSP_URL.startswith('rtsp://') else 'file-stream'
    run_kind = 'one-shot' if _one_shot else 'continuous'
    logger.info(f'{source_kind}+pipeline starting ({_mode} mode, {run_kind}) — {_frame_interval*1000:.0f}ms sampling')


def stop_capture():
    """Stop RTSP capture and all threads.

    join 超时取 8s: WorldAV 单次推理可达 2s（冷启动），RTSP read 也可能阻塞，
    太短的 join 会让推理线程残留 → GPU 停止后仍持续占用。
    """
    global _capture_running, _capture_thread, _audio_thread
    _capture_running = False
    try:
        from app.inference import world_av_pipeline
        world_av_pipeline.set_audio_stream_expected(False)
    except Exception:
        pass
    if _capture_thread:
        _capture_thread.join(timeout=8)
        _capture_thread = None
    if _audio_thread:
        _audio_thread.join(timeout=8)
        _audio_thread = None
    logger.info('RTSP capture stopped')


def is_capturing() -> bool:
    """Return True if capture loop is currently running."""
    return _capture_running


def is_one_shot() -> bool:
    return _one_shot
