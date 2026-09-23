"""
告警素材采集 — 独立 RTSP 抓帧 + 视频片段录制

与推理管线解耦: 无论管线是否运行, 都能从 RTSP 抓帧/录视频。
视频用 ffmpeg -c copy (H.264 直接复制, 快); 抓帧用 cv2 短连接。
"""

import logging
import os
import subprocess
import threading
import time
from pathlib import Path

import cv2

from app.inference.inference_config import RTSP_URL

logger = logging.getLogger(__name__)

# 素材输出目录
CLIPS_DIR = Path(__file__).resolve().parents[2] / "data" / "clips"  # backend/data/clips/
CAPTURES_DIR = Path(__file__).resolve().parents[2] / "data" / "captures"  # backend/data/captures/


def persist_capture_pic(event_id: str, pic_url: str, timeout: float = 6.0) -> str:
    """
    下载萤石抓拍/告警图片 URL 并持久化为本地文件 backend/data/captures/<event_id>.jpg。

    **为什么**: 萤石 capture 返回的 picUrl 是带 Expires 签名的 URL, 约 24h 后过期(HTTP 403),
    导致小程序/网页端图片加载失败。下载到本地后不再依赖云端签名 URL。

    Returns:
        本地文件绝对路径; 下载失败返回 ""
    """
    if not pic_url:
        return ""
    try:
        import requests
        resp = requests.get(pic_url, timeout=timeout)
        if resp.status_code != 200:
            logger.warning(f"[Capture] 抓拍图下载失败 HTTP {resp.status_code}: {pic_url[:80]}")
            return ""
        CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        out_path = CAPTURES_DIR / f"{event_id}.jpg"
        out_path.write_bytes(resp.content)
        logger.info(f"[Capture] ✅ 抓拍图已持久化: {out_path} ({len(resp.content)//1024}KB)")
        return str(out_path)
    except Exception as e:
        logger.warning(f"[Capture] 抓拍图持久化异常: {e}")
        return ""


def _find_ffmpeg() -> str:
    """ffmpeg 查找: imageio-ffmpeg 自带(含 libx264, 首选) → conda 环境 → 系统 PATH。

    **为什么优先 imageio**: conda ffmpeg 4.3 无 libx264 且 libopenh264 版本不匹配,
    H.264 转码不可用; imageio-ffmpeg 自带静态 ffmpeg 7.x 含 libx264。
    """
    candidates = []
    try:
        import imageio_ffmpeg
        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    candidates += [
        "",
        os.environ.get("FFMPEG_PATH", ""),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    import shutil
    return shutil.which("ffmpeg") or "ffmpeg"


def _transcode_h264(src: str, dst: str, timeout: float = 60) -> bool:
    """
    把 mp4v/mpeg4 编码的视频转码为浏览器可播放的 H.264 (mp4, faststart)。

    **为什么**: cv2 VideoWriter 录的是 mpeg4(Part 2) 编码, 现代浏览器 <video> 不支持
    (只支持 H.264/VP9/AV1), 导致网页端放不出视频; 而企业微信/微信播放器兼容性好。
    统一转 H.264 后网页端 + 小程序端都能播放。
    """
    ffmpeg = _find_ffmpeg()
    try:
        cmd = [
            ffmpeg, "-loglevel", "error", "-y",
            "-i", src,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-movflags", "+faststart",
            dst,
        ]
        subprocess.run(cmd, capture_output=True, timeout=timeout)
        return os.path.exists(dst) and os.path.getsize(dst) > 50_000
    except Exception as e:
        logger.warning(f"[Capture] H.264 转码失败: {e}")
        return False


def _video_valid(path) -> bool:
    """校验视频文件可读（moov atom 存在、有帧），损坏/残缺返回 False。"""
    if not path or not os.path.exists(path):
        return False
    try:
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            cap.release()
            return False
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap.release()
        return n > 0
    except Exception:
        return False


def record_clip(seconds: int = 12, prefix: str = "fall") -> str | None:
    """
    录制 RTSP 视频片段。

    用 cv2 录制（与推理管线同源, 避免 ffmpeg 第二路连接与管线冲突卡死）。
    优先 ffmpeg -c copy（快且质量好），ffmpeg 失败时降级 cv2 VideoWriter。

    Returns:
        视频文件绝对路径; 失败返回 None
    """
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CLIPS_DIR / f"{prefix}_{int(time.time())}.mp4"

    # ---- 方案1: ffmpeg -c copy (H.264 直接复制, 快) ----
    ffmpeg = _find_ffmpeg()
    cmd = [
        ffmpeg, "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-i", RTSP_URL,
        "-t", str(seconds),
        "-c", "copy",
        "-y", str(out_path),
    ]
    try:
        # ffmpeg 快速失败(5s): 该摄像头 RTSP 对 ffmpeg 连接不友好(常卡住),
        # 超时后立即降级 cv2 录制
        proc = subprocess.run(cmd, capture_output=True, timeout=5)
        if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 50_000:
            logger.info(f"[Capture] ✅ 视频片段录制完成 (ffmpeg): {out_path} ({out_path.stat().st_size//1024}KB)")
            return str(out_path)
        logger.warning(f"[Capture] ffmpeg 录制失败: {proc.stderr.decode()[:150]}")
    except Exception as e:
        logger.warning(f"[Capture] ffmpeg 录制异常: {e}")
    # 失败清理
    if out_path.exists():
        try:
            out_path.unlink()
        except Exception:
            pass

    # ---- 方案2（废弃）: cv2 降级会再开一条 RTSP 连接, 与主推理管线并发读 RTSP
    # 触发 libavcodec/libavformat 原生 segfault（2026-08-13 dmesg: signal 11 实锤）。
    # 录像改由 world_av_pipeline 在告警瞬间从帧缓冲直写（无新连接, 无崩溃风险）。
    logger.warning("[Capture] ffmpeg 失败且禁用 cv2 降级(避免 RTSP 并发崩溃); 视频由管线缓冲录制")
    if out_path.exists():
        try:
            out_path.unlink()
        except Exception:
            pass
    return None


def capture_frames(count: int = 3, interval: float = 2.0, timeout_total: float = 12.0) -> list[bytes]:
    """
    独立 RTSP 抓帧 N 张, 每张间隔 interval 秒。

    **硬超时保护**: RTSP 不可达时 cv2.VideoCapture 可能无限阻塞,
    整个抓帧放在子线程, 超时 timeout_total 秒放弃（返回已抓到的）。

    Returns:
        JPEG bytes 列表 (可能少于 count, 失败/超时为空)
    """
    frames: list[bytes] = []

    def _worker():
        try:
            cap = cv2.VideoCapture(RTSP_URL)
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 4000)
            if not cap.isOpened():
                logger.warning("[Capture] RTSP 打开失败, 无法抓帧")
                return
            for i in range(count):
                ret, frame = cap.read()
                if ret:
                    _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    frames.append(jpeg.tobytes())
                if i < count - 1 and interval > 0:
                    time.sleep(interval)
            cap.release()
        except Exception as e:
            logger.warning(f"[Capture] 抓帧异常: {e}")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout_total)  # 硬超时: RTSP 卡死时不被拖住
    if len(frames) < count:
        logger.warning(f"[Capture] 抓帧超时/部分失败: {len(frames)}/{count} 张")
    else:
        logger.info(f"[Capture] ✅ 抓帧 {len(frames)}/{count} 张")
    return frames
