"""
三级级联跌倒检测管线 — World AV 模型版本

    Stage 1: YOLO11n 人形检测 → 无人跳过后续
    Stage 2: WorldAVRuntime (V-JEPA2 + Audio + Reliability Gate) → P_final
    Stage 3: SerialPoseMedicalAnalyzer → 着地部位 + 风险等级

备选降级：WorldAV 不可用时自动降级到 KD-Stride-2.
"""

from __future__ import annotations

import io
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from app.inference.inference_config import (
    WEIGHTS, FALL_THRESHOLD, EMA_ALPHA, MIN_CONSECUTIVE_HITS, COOLDOWN_FRAMES,
    DECAY_ON_MISSING, AUDIO_SAMPLE_RATE, AUDIO_DURATION,
    POSE_LOOKBACK_FRAMES, POSE_POST_FRAMES, POSE_CONFIDENCE_THRESHOLD, POSE_FPS,
    VJEPA2_CONTEXT_FRAMES, DEVICE_SERIAL, TEST_PHONE,
    WORDPOSE_V2_THRESHOLD, WORDPOSE_V2_MIN_CONSECUTIVE, WORDPOSE_V2_EMA_ALPHA,
    WORDPOSE_V2_REQUIRED_HISTORY, WORDPOSE_V2_INFERENCE_HZ,
    WORDPOSE_V2_IMAGE_SIZE,
    SPATIAL_V5_THRESHOLD,
)
from app.inference.person_detector import detect_persons
from app.inference.temporal_smoothing import TemporalFallSmoother, TemporalSmoothingConfig
from app.inference.serial_pose_medical import SerialPoseMedicalAnalyzer
from app.inference.pose_backends import YoloPoseEstimator, PoseDetection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports for heavy GPU modules — loaded on first use
# ---------------------------------------------------------------------------
_world_av_runtime = None
_world_av_config = None
_world_av_lock = threading.Lock()

# World-Pose v2.2 AV residual (mode='worldpose')
_worldpose_runtime = None
_worldpose_lock = threading.Lock()

# Spatial v5 + Temporal Residual（轻量部署模型）
_spatial_v5_runtime = None
_spatial_v5_lock = threading.Lock()

# ---- 异步推理 worker（worldpose/worldav 重推理与采集线程解耦）----
# 采集线程只做 YOLO+画框；~950ms 的 v2.2 推理在 worker 线程执行, 结果非阻塞消费。
# 这消除了"每 3 帧阻塞 ~950ms → 标注帧 2.7fps + 每秒冻结"的卡顿; 预热/模型加载也
# 在 worker 内阻塞, 不再冻住采集线程 → 冷启动灰屏窗口消失。
_infer_thread: threading.Thread | None = None
_infer_request: dict | None = None        # 最新待推理窗口快照（worker 只取最新, 丢堆积）
_infer_result: dict | None = None          # 最新完成结果
_infer_result_seq: int = 0                 # 完成序号（worker 写, 采集线程比大小）
_infer_seq_seen: int = 0                   # 采集线程已消费序号
_infer_cond = threading.Condition()
_model_hz: float = 4.0
_next_model_submit_at: float = 0.0
_model_submit_count: int = 0
_model_complete_count: int = 0
_dropped_stale_windows: int = 0
_pose_cache_frames: int = 0
_pose_cache_hits: int = 0
_last_frame_timestamp: float = 0.0
_model_window_started_at: float = 0.0

# 当前模式: 'kd' / 'worldav' / 'worldpose' / 'spatial_v5'
_mode: str = "kd"

# Fallback to KD-Stride-2
_use_fallback = False
_fallback_orchestrator = None

# YOLO 预检开关: True = 检测到人才推理(省算力), False = 直接整帧检测(不丢帧)
_yolo_gate: bool = True


def set_mode(mode: str = "kd", yolo_gate: bool = True):
    """切换检测模式。

    Args:
        mode: 'kd' (KD-Stride-2) / 'worldav' (V-JEPA2+Audio) / 'worldpose' (World-Pose v2.2 AV residual)
        yolo_gate: True = YOLO 先识别人像再推理; False = 直接检测
    """
    global _use_fallback, _yolo_gate, _mode
    from app.inference.temporal_smoothing import TemporalFallSmoother, TemporalSmoothingConfig
    mode = (mode or "kd").lower()
    _mode = mode
    if mode in ("kd", "student", "kd-stride-2"):
        _use_fallback = True
        _init_fallback()
        _smoother = TemporalFallSmoother(TemporalSmoothingConfig(
            threshold=0.45, ema_alpha=0.5, min_consecutive=2,
            cooldown_frames=30, decay_on_missing=0.90,
        ))
    elif mode in ("worldpose", "v2", "worldpose_v2"):
        # v2.2: 阈值在 _init_worldpose 从 ckpt 最终确定; 先用 ckpt/env 值占位
        _use_fallback = False
        thr = WORDPOSE_V2_THRESHOLD or 0.7444
        _smoother = TemporalFallSmoother(TemporalSmoothingConfig(
            threshold=thr, ema_alpha=WORDPOSE_V2_EMA_ALPHA,
            min_consecutive=WORDPOSE_V2_MIN_CONSECUTIVE,
            cooldown_frames=30, decay_on_missing=0.90,
        ))
    elif mode in ("spatial_v5", "v5", "spatial"):
        _use_fallback = False
        _smoother = TemporalFallSmoother(TemporalSmoothingConfig(
            threshold=SPATIAL_V5_THRESHOLD, ema_alpha=0.45,
            min_consecutive=2, cooldown_frames=30, decay_on_missing=0.90,
        ))
    else:  # worldav
        _use_fallback = False
        _smoother = TemporalFallSmoother(TemporalSmoothingConfig(
            threshold=0.76, ema_alpha=0.35, min_consecutive=3,
            cooldown_frames=30, decay_on_missing=0.90,
        ))
    _yolo_gate = bool(yolo_gate)
    logger.info(f"Pipeline mode: model={mode} yolo_gate={_yolo_gate}")

# ---------------------------------------------------------------------------
# Global pipeline state
# ---------------------------------------------------------------------------

# Frame buffer for WorldAV (context_frames + future_frames)
_frame_buffer: deque[np.ndarray] = deque(maxlen=64)
# Spatial v5 Pose cache. Each decoded frame is scored once; overlapping
# detector windows reuse these arrays instead of rescanning the same 32 frames.
_spatial_pose_buffer: deque[tuple[int, np.ndarray, np.ndarray]] = deque(maxlen=64)
# [2026-08-13] 与 _frame_buffer 对齐的预缩放 image_size² BGR 帧（worldpose 编码输入,
# 省去每次推理 32× 720p→384 resize 的 CPU 瓶颈; 与 runtime 内 cv2.resize bit-identical）
_frame_buffer_small: deque[np.ndarray] = deque(maxlen=64)
# Audio buffer (raw PCM float32 fragments)
_audio_buffer: deque[np.ndarray] = deque(maxlen=128)
_audio_sample_rate: int = AUDIO_SAMPLE_RATE
_audio_stream_expected: bool = False  # 采集线程已启动且输入确认有音轨

# Stage 1
_last_persons: list[dict] = []
_last_yolo_ms: float = 0.0
_last_frame_w: int = 0
_last_frame_h: int = 0

# Stage 2
# KD 学生模型阈值（比 WorldAV 低: KD 峰值 ~0.69，检测"人横躺"结果状态）
KD_THRESHOLD = 0.50

_smoother = TemporalFallSmoother(TemporalSmoothingConfig(
    threshold=KD_THRESHOLD,  # 动态: KD 模式用 0.5; WorldAV 模式在 alert 判断时用 FALL_THRESHOLD
    ema_alpha=EMA_ALPHA,
    min_consecutive=MIN_CONSECUTIVE_HITS,
    cooldown_frames=COOLDOWN_FRAMES,
    decay_on_missing=DECAY_ON_MISSING,
))
_last_world_av_result: dict[str, Any] = {}
_last_world_av_ms: float = 0.0
_fall_alert_active: bool = False

# Stage 3
_pose_analyzer: SerialPoseMedicalAnalyzer | None = None
_pose_result: dict[str, Any] | None = None
_last_pose_frame_idx: int = 0
_frame_seq: int = 0

# V5 event bridge. 事件槽位由归档层原子认领；这里仅防止同一推理结果重复启动 worker。
_v5_triggered: bool = False
_event_bridge_lock = threading.Lock()
_event_bridge_inflight: bool = False

# 与最后一份模型结果绑定的人物证据，不能被异步返回时的当前帧覆盖。
_last_person_gate_source: str = "current_frame"
_last_person_evidence_frame: int = 0
_last_suppression_reason: str = ""

# v2.2 原始连续命中（匹配 CLI: max(raw_consecutive, smooth_consecutive) >= min_consecutive）
# 快速跌倒时原始概率可能比 EMA 先爬过阈值，这条路径避免漏报
_raw_consecutive: int = 0

# [2026-08-13 用户决策] 跌倒都较短促 + 异步窗口滑得快 → 单次超门限即告警。
# 加冷却时间防止同一跌倒(连续命中间有回落)重复建事件。
_ALERT_COOLDOWN_S = 20
_last_alert_ts: float = 0.0

# [2026-08-13] 重推理已移到异步 worker（_inference_worker）, 不再在采集线程内联节流。
# KD fallback 轻量(~34ms)仍内联每帧推理。旧的 INFER_EVERY_N_FRAMES 帧节流逻辑已移除。

# (历史) 音频静音门限已废弃: 真实静音波形必须传给模型，全零 mel 是分布外输入
# AUDIO_SILENCE_RMS = 0.001

# Annotated output
_annotated_jpeg: bytes | None = None
_annotated_jpeg_bytes: bytes | None = None  # [2026-08-13] 缩小后的原始 JPEG 字节, 供 annotated-frame 直接取
_last_raw_frame: np.ndarray | None = None
_spatial_v5_pose: np.ndarray | None = None
_spatial_v5_future_pose: np.ndarray | None = None
_spatial_v5_future_visibility: np.ndarray | None = None

# Thread safety
_state_lock = threading.Lock()

# Config
PERSON_HISTORY_MAX = 50


def _init_world_av():
    """Lazy-init WorldAVRuntime on first call."""
    global _world_av_runtime, _world_av_config
    if _world_av_runtime is not None:
        return True
    with _world_av_lock:
        if _world_av_runtime is not None:
            return True
        try:
            from app.inference.world_av_runtime import WorldAVRuntime, WorldAVRuntimeConfig
            _world_av_config = WorldAVRuntimeConfig()  # 所有默认值来自 inference_config
            _world_av_runtime = WorldAVRuntime(_world_av_config)
            logger.info("WorldAVRuntime initialized (V-JEPA2 ViT-L + Audio + Gate)")
            return True
        except Exception as e:
            logger.error(f"WorldAV init failed: {e}, falling back to KD-Stride-2")
            global _use_fallback
            _use_fallback = True
            return False


def _init_worldpose():
    """Lazy-init World-Pose v2.2 AV residual runtime."""
    global _worldpose_runtime, _use_fallback
    if _worldpose_runtime is not None:
        return True
    with _worldpose_lock:
        if _worldpose_runtime is not None:
            return True
        try:
            from app.inference.worldpose_v2.world_pose_v2_runtime import (
                WorldPoseV2Runtime,
                WorldPoseV2RuntimeConfig,
            )
            cfg = WorldPoseV2RuntimeConfig(
                inference_hz=WORDPOSE_V2_INFERENCE_HZ,
                threshold=WORDPOSE_V2_THRESHOLD,
            )
            _worldpose_runtime = WorldPoseV2Runtime(cfg)
            # 用 ckpt 的真实 decision_threshold 覆盖 smoother 阈值
            _smoother.config.threshold = _worldpose_runtime.threshold
            logger.info(f"WorldPoseV2Runtime initialized (threshold={_worldpose_runtime.threshold:.4f})")
            return True
        except Exception as e:
            logger.error(f"WorldPose v2.2 init failed: {e}, falling back to KD-Stride-2")
            _use_fallback = True
            return False


def _init_spatial_v5():
    """Lazy-init the self-contained Spatial v5 runtime."""
    global _spatial_v5_runtime
    if _spatial_v5_runtime is not None:
        return True
    with _spatial_v5_lock:
        if _spatial_v5_runtime is not None:
            return True
        try:
            from app.inference.spatial_v5_runtime import get_runtime
            _spatial_v5_runtime = get_runtime()
            logger.info(
                "Spatial v5 runtime initialized (MC3 + Temporal Residual, threshold=%.6f)",
                _spatial_v5_runtime.threshold,
            )
            return True
        except Exception as e:
            logger.error(f"Spatial v5 init failed: {e}, falling back to KD-Stride-2")
            return False


def _run_spatial_v5(
    frames: list[np.ndarray],
    pose: np.ndarray | None = None,
    boxes: np.ndarray | None = None,
) -> dict[str, Any] | None:
    """Run the new self-contained Spatial v5 model on a causal frame window."""
    global _use_fallback, _spatial_v5_pose, _spatial_v5_future_pose, _spatial_v5_future_visibility
    if not _init_spatial_v5():
        _use_fallback = True
        return _run_fallback(frames)
    try:
        t0 = time.perf_counter()
        audio = _get_audio_window(duration=AUDIO_DURATION)
        if pose is not None and boxes is not None:
            result = _spatial_v5_runtime.predict_from_pose(frames, pose, boxes, audio=audio)
        else:
            # Compatibility path for callers outside the live stream adapter.
            result = _spatial_v5_runtime.predict(frames, audio=audio)
        result["_inference_ms"] = (time.perf_counter() - t0) * 1000
        result.setdefault("threshold", SPATIAL_V5_THRESHOLD)
        _spatial_v5_pose = result.get("skeleton_pose")
        _spatial_v5_future_pose = result.get("future_pose")
        _spatial_v5_future_visibility = result.get("future_visibility")
        return result
    except Exception as e:
        logger.error(f"Spatial v5 inference error: {e}")
        _use_fallback = True
        return _run_fallback(frames)


def _run_worldpose(frames: list[np.ndarray], small_frames: list[np.ndarray] | None = None) -> dict[str, Any] | None:
    """World-Pose v2.2 AV residual inference on a causal window."""
    global _use_fallback
    if not _init_worldpose():
        return _run_fallback(frames)
    try:
        t0 = time.perf_counter()
        audio = _get_audio_window(duration=AUDIO_DURATION)
        waveform, sr = audio if audio else (None, None)
        result = _worldpose_runtime.predict(frames, waveform=waveform, sample_rate=sr, small_frames=small_frames)
        result["_inference_ms"] = (time.perf_counter() - t0) * 1000
        return result
    except Exception as e:
        logger.error(f"WorldPose v2.2 inference error: {e}")
        _use_fallback = True
        return _run_fallback(frames)


def _init_fallback():
    """Init KD-Stride-2 fallback."""
    global _fallback_orchestrator
    if _fallback_orchestrator is not None:
        return
    logger.info("Using KD-Stride-2 fallback for fall detection")


def _init_pose(fps: float = POSE_FPS, cached: bool = False):
    """Lazy-init Stage 3 analyzer.

    Spatial v5 already owns a live COCO-17 pose cache, so its medical analyzer
    is constructed without a second detector. Other legacy modes keep the
    original detector-backed path.
    """
    global _pose_analyzer
    if _pose_analyzer is not None:
        return
    try:
        pose = None if cached else YoloPoseEstimator(WEIGHTS["yolo11n_pose"])
        _pose_analyzer = SerialPoseMedicalAnalyzer(
            pose_estimator=pose,
            fps=fps,
            lookback_frames=POSE_LOOKBACK_FRAMES,
            post_frames=POSE_POST_FRAMES,
        )
        logger.info("SerialPoseMedicalAnalyzer initialized (cached=%s)", cached)
    except Exception as e:
        logger.error(f"Pose backend init failed: {e}")


# ---------------------------------------------------------------------------
# Audio capture
# ---------------------------------------------------------------------------

def push_audio(waveform: np.ndarray, sample_rate: int | None = None):
    """Feed audio samples from external capture thread."""
    global _audio_sample_rate
    if sample_rate is not None:
        _audio_sample_rate = sample_rate
    _audio_buffer.append(np.asarray(waveform, dtype=np.float32))


def set_audio_stream_expected(enabled: bool):
    """标记音频采集线程状态，让前端在首个 PCM 块到达前也能显示音频已就绪。"""
    global _audio_stream_expected
    _audio_stream_expected = bool(enabled)


def _get_audio_window(duration: float = 3.0) -> tuple[np.ndarray, int] | None:
    """Collect the last `duration` seconds of audio for the model.

    注意: 不再做静音过滤！真实静音波形必须传给模型——全零 mel 是
    分布外输入（训练数据永远有真实音频），会导致模型输出退化到
    final_probs≈0.5（概率恒 0/恒 50% 的根源）。
    只有音频流完全不存在（buffer 空）才返回 None。
    """
    sr = _audio_sample_rate
    needed = int(duration * sr)
    if not _audio_buffer or needed <= 0:
        return None
    collected = np.concatenate(list(_audio_buffer))
    if collected.size < needed:
        return collected, sr
    return collected[-needed:], sr


# ---------------------------------------------------------------------------
# Frame processing
# ---------------------------------------------------------------------------

def push_frame(frame: np.ndarray):
    """Feed a BGR frame into the pipeline."""
    _frame_buffer.append(frame)
    if _mode in ("worldpose", "v2", "worldpose_v2"):
        try:
            _frame_buffer_small.append(
                cv2.resize(frame, (WORDPOSE_V2_IMAGE_SIZE, WORDPOSE_V2_IMAGE_SIZE))
            )
        except Exception:
            pass


def _get_frames_for_world_av(n: int = 32) -> list[np.ndarray] | None:
    """Get the last n frames for WorldAV inference."""
    if len(_frame_buffer) < n:
        return None
    return list(_frame_buffer)[-n:]


def _get_small_frames_for_world_av(n: int = 32) -> list[np.ndarray] | None:
    """[2026-08-13] 取最近 n 张预缩放帧（与 _get_frames_for_world_av 对齐）。"""
    if len(_frame_buffer_small) < n:
        return None
    return list(_frame_buffer_small)[-n:]


def _persons_from_pose_box(box: np.ndarray | None, frame_shape: tuple[int, ...]) -> list[dict]:
    """Convert one cached normalized Pose box to the legacy person schema."""
    if box is None:
        return []
    item = np.asarray(box, dtype=np.float32).reshape(-1)
    if item.size < 6 or float(item[4]) < 0.12 or float(item[2]) <= 0 or float(item[3]) <= 0:
        return []
    h, w = frame_shape[:2]
    cx, cy, bw, bh = [float(v) for v in item[:4]]
    x1 = int(np.clip((cx - bw / 2.0) * w, 0, w))
    y1 = int(np.clip((cy - bh / 2.0) * h, 0, h))
    x2 = int(np.clip((cx + bw / 2.0) * w, 0, w))
    y2 = int(np.clip((cy + bh / 2.0) * h, 0, h))
    if x2 - x1 < 30 or y2 - y1 < 60:
        return []
    return [{
        "bbox": (x1, y1, x2, y2),
        "confidence": float(item[4]),
        "area": int((x2 - x1) * (y2 - y1)),
    }]


def _spatial_pose_window(n: int) -> tuple[np.ndarray, np.ndarray] | None:
    """Return the cached pose/box arrays aligned with the latest frame window."""
    if len(_spatial_pose_buffer) < n:
        return None
    rows = list(_spatial_pose_buffer)[-n:]
    pose = np.stack([np.asarray(row[1], dtype=np.float32) for row in rows])
    boxes = np.stack([np.asarray(row[2], dtype=np.float32) for row in rows])
    return pose, boxes


# ---------------------------------------------------------------------------
# Stage 2: WorldAV inference
# ---------------------------------------------------------------------------

def _run_world_av(
    frames: list[np.ndarray],
    small_frames: list[np.ndarray] | None = None,
    pose: np.ndarray | None = None,
    boxes: np.ndarray | None = None,
) -> dict[str, Any] | None:
    """Run WorldAV model on a window of frames."""
    global _use_fallback
    if _mode in ("spatial_v5", "v5", "spatial"):
        return _run_spatial_v5(frames, pose=pose, boxes=boxes)
    if _mode in ("worldpose", "v2", "worldpose_v2"):
        return _run_worldpose(frames, small_frames)
    if _use_fallback:
        return _run_fallback(frames)
    if not _init_world_av():
        return _run_fallback(frames)
    try:
        t0 = time.perf_counter()
        audio = _get_audio_window()
        waveform, sr = audio if audio else (None, None)
        result = _world_av_runtime.predict(frames, waveform=waveform, sample_rate=sr)
        result["_inference_ms"] = (time.perf_counter() - t0) * 1000
        return result
    except Exception as e:
        logger.error(f"WorldAV inference error: {e}")
        _use_fallback = True
        return _run_fallback(frames)


def _submit_inference(
    frames: list[np.ndarray],
    small_frames: list[np.ndarray] | None = None,
    pose: np.ndarray | None = None,
    boxes: np.ndarray | None = None,
    person_evidence: list[dict] | None = None,
    person_evidence_frame: int = 0,
):
    """[2026-08-13] 非阻塞提交当前窗口给异步 worker（采集线程每帧调用）。

    worker 每次只取最新请求（丢弃堆积的旧窗口）；重推理约 ~1s，由 worker 自行节流，
    采集线程不等待 → 标注帧/预览不再被推理冻结。
    """
    global _infer_thread, _infer_request, _model_submit_count, _dropped_stale_windows, _model_window_started_at
    with _infer_cond:
        if not _model_window_started_at:
            _model_window_started_at = time.time()
        if _infer_request is not None:
            _dropped_stale_windows += 1
        _infer_request = {
            "frames": list(frames),
            "small_frames": list(small_frames) if small_frames is not None else None,
            "pose": None if pose is None else np.asarray(pose, dtype=np.float32).copy(),
            "boxes": None if boxes is None else np.asarray(boxes, dtype=np.float32).copy(),
            # 人物证据属于提交帧/模型窗口，不属于 worker 返回时的当前帧。
            "person_evidence": [dict(item) for item in (person_evidence or [])],
            "person_evidence_frame": int(person_evidence_frame or 0),
        }
        _model_submit_count += 1
        if _infer_thread is None or not _infer_thread.is_alive():
            _infer_thread = threading.Thread(target=_inference_worker, daemon=True, name="worldpose-infer")
            _infer_thread.start()
        _infer_cond.notify()


def _inference_worker():
    """后台推理循环：取最新请求 → 推理 → 存结果 + 递增序号（供采集线程非阻塞消费）。"""
    global _infer_request, _infer_result, _infer_result_seq, _model_complete_count
    while True:
        with _infer_cond:
            while _infer_request is None:
                _infer_cond.wait()
            req = _infer_request
            _infer_request = None
        try:
            av_result = _run_world_av(
                req["frames"], req.get("small_frames"),
                pose=req.get("pose"), boxes=req.get("boxes"),
            )
        except Exception as e:
            logger.error(f"async inference error: {e}")
            av_result = None
        if av_result:
            # 将本次推理提交帧的人物证据一并返回，避免异步结果与当前画面错位。
            av_result["_person_evidence"] = {
                "present": bool(req.get("person_evidence")),
                "persons": req.get("person_evidence") or [],
                "frame": int(req.get("person_evidence_frame") or 0),
                "source": "inference_submit_frame",
            }
            with _infer_cond:
                _infer_result = av_result
                _infer_result_seq += 1
                _model_complete_count += 1


def _consume_inference_result() -> dict[str, Any] | None:
    """[2026-08-13] 采集线程取最新完成结果；无新结果返回 None（不阻塞）。"""
    global _infer_seq_seen
    with _infer_cond:
        if _infer_result_seq > _infer_seq_seen:
            av_result = _infer_result
            _infer_seq_seen = _infer_result_seq
            return av_result
    return None


def _run_fallback(frames: list[np.ndarray]) -> dict[str, Any] | None:
    """KD-Stride-2 fallback inference (主检测备用模型).

    输入 (T,H,W,C) BGR 帧 → (1,T,C,H,W) 归一化 → sigmoid 概率。
    直接推理（不依赖 orchestrator，其 infer() 存在 device 不匹配问题）。
    """
    _init_fallback()
    try:
        import torch
        from app.inference.fall_detector import load_model
        model, device = load_model()
        t0 = time.perf_counter()
        # 取最新 32 帧 → 224x224
        window = list(frames)[-32:]
        resized = [cv2.resize(f, (224, 224)) for f in window]
        arr = np.stack(resized).astype(np.float32) / 255.0          # (T,H,W,C)
        tensor = torch.from_numpy(arr.transpose(0, 3, 1, 2)).unsqueeze(0).to(device)  # (1,T,C,H,W)
        with torch.no_grad():
            prob = torch.sigmoid(model(tensor)).item()
        return {
            "final_probability": float(prob),
            "visual_probability": float(prob),
            "audio_probability": 0,
            "av_probability": 0,
            "gate": 0,
            "cross_gate": 0,
            "selector_gate": 0,
            "match_score": 0,
            "impact_probability": 0,
            "audio_available": False,
            "status": "ok (kd)",
            "_inference_ms": (time.perf_counter() - t0) * 1000,
        }
    except Exception as e:
        logger.error(f"KD fallback error: {e}")
        return None


# ---------------------------------------------------------------------------
# Stage 3: Pose medical analysis
# ---------------------------------------------------------------------------

def _trigger_pose(frame_idx: int):
    """Begin post-alarm pose analysis."""
    global _pose_result
    if _mode in ("spatial_v5", "v5", "spatial") and _spatial_pose_buffer:
        _init_pose(POSE_FPS, cached=True)
        sequence = []
        for cached_idx, pose, _box in list(_spatial_pose_buffer)[-POSE_LOOKBACK_FRAMES:]:
            sequence.append((cached_idx, pose[:, :2], pose[:, 2]))
        if _pose_analyzer is not None:
            _pose_analyzer.begin_from_arrays(sequence, trigger_frame=frame_idx)
            _pose_result = _pose_analyzer.warning.to_dict()
        return
    _init_pose(POSE_FPS)
    if _pose_analyzer is None:
        return
    # Collect buffered frames for lookback
    lookback = min(75, len(_frame_buffer))
    buffered = [
        (frame_idx - lookback + i, _frame_buffer[-lookback + i])
        for i in range(lookback)
    ]
    _pose_analyzer.begin(buffered, trigger_frame=frame_idx)
    _pose_result = _pose_analyzer.warning.to_dict()


def _update_pose(frame_idx: int, frame: np.ndarray):
    """Continue pose analysis post-trigger."""
    global _pose_result
    if _pose_analyzer is None or not _pose_analyzer.active or _pose_analyzer.finished:
        return
    if _mode in ("spatial_v5", "v5", "spatial") and _spatial_pose_buffer:
        cached_idx, pose, _box = _spatial_pose_buffer[-1]
        if cached_idx == frame_idx:
            _pose_analyzer.update_from_arrays(frame_idx, pose[:, :2], pose[:, 2])
    else:
        _pose_analyzer.update(frame_idx, frame)
    _pose_result = _pose_analyzer.warning.to_dict()


# ---------------------------------------------------------------------------
# V5 event bridge
# ---------------------------------------------------------------------------

def _on_fall_alert(pose_result: dict[str, Any] | None, av_result: dict[str, Any]):
    """Queue one event workflow; the archive atomically rejects concurrent events."""
    global _v5_triggered, _event_bridge_inflight
    from app.services.fall_event_archive import workflow_is_active
    if workflow_is_active():
        return False
    with _event_bridge_lock:
        if _event_bridge_inflight:
            return False
        _event_bridge_inflight = True
    # The capture thread can receive another completed result while the event is
    # being written. The archive layer remains the final atomic gate.
    _v5_triggered = True
    try:
        from app.services.fall_orchestrator import process_fall_event
        from app.services.fall_event_simulator import FallEventSimulation
        from app.inference.inference_config import RISK_LEVEL_MAP
        import uuid
        from datetime import datetime, timezone

        # 映射风险等级
        risk = pose_result.get("risk_level", "medium") if pose_result else "medium"
        v5_risk = RISK_LEVEL_MAP.get(risk, "III")

        now = datetime.now(timezone.utc)
        p_fall = av_result.get("final_probability", 0)

        event = FallEventSimulation(
            event_id=f"spatialv5_{int(now.timestamp())}",
            scenario_key="spatial_v5_real",
            scenario_name="Spatial v5 实时检测",
            description=f"Spatial v5 检测到跌倒 P={p_fall:.3f}",
            fall_detected=True,
            detection_confidence=p_fall,
            detection_latency_ms=av_result.get("_inference_ms", 0),
            touch_ground_part=pose_result.get("first_contact_part", "unknown") if pose_result else "unknown",
            # first_side 表示左右侧关键点谁先触地（也可能 simultaneous），并不是
            # 前/后/左/右的跌倒方向。真实管线当前不输出可靠方向，保持为空。
            fall_direction="",
            # impact_score 是 0-100 的姿态启发式评分，并非经尺度标定的 m/s 速度，
            # 不再冒充物理冲击速度写入事件。
            impact_velocity=0,
            body_tilt_angle=0,
            center_of_mass_velocity=0,
            # 事件分级按首次着地部位映射 I/II/III；pose 内部评分只用于模型诊断。
            risk_level="",
            timestamp=now.isoformat(),
            location="卧室",
            device_serial=DEVICE_SERIAL,
            video_window_frames=32,
            video_window_duration_s=3.2,
        )
        def _event_worker():
            global _event_bridge_inflight
            try:
                process_fall_event(
                    event,
                    inquiry_first=_mode in ("spatial_v5", "v5", "spatial"),
                )
                # 录像编码和骨骼落盘继续在事件 worker 后台完成。
                _finish_event_assets(event.event_id, av_result.get("skeleton_pose"))
                logger.info(f"V5 event triggered: {event.event_id} risk={v5_risk}")
            except Exception as exc:
                from app.services.fall_event_archive import get_event, update_event
                record = get_event(event.event_id)
                if record:
                    update_event(event.event_id, status="workflow_failed")
                logger.error(f"V5 event worker failed: {event.event_id}: {exc}")
            finally:
                with _event_bridge_lock:
                    _event_bridge_inflight = False

        threading.Thread(target=_event_worker, daemon=True, name="fall-event-worker").start()
        return True
    except Exception as e:
        with _event_bridge_lock:
            _event_bridge_inflight = False
        logger.error(f"V5 event bridge failed: {e}")
        return False


def _finish_event_assets(event_id: str, skeleton_pose) -> None:
    """后台写入事件录像和骨骼序列，不阻塞采集/问询链路。"""
    try:
        clip = _dump_fall_clip(event_id)
        if clip:
            from app.services.fall_event_archive import update_event
            update_event(event_id, video_clip=clip)
            logger.info(f"FALL clip (buffer): {event_id} → {clip}")
    except Exception as e:
        logger.warning(f"FALL clip dump failed: {e}")
    try:
        _persist_skeleton(event_id, skeleton_pose)
    except Exception as e:
        logger.warning(f"FALL skeleton persist failed: {e}")


def _persist_skeleton(event_id: str, pose) -> None:
    """把 v2.2 推理的 COCO-17 骨骼序列转成 payload 持久化到事件。

    pose: [T,17,3] 归一化 xy + 置信度。payload 对齐算法平台 skeleton_gen 的 schema
    （keypoints/confidences 嵌套列表），origin='real' 区分合成骨骼。
    """
    if pose is None or not hasattr(pose, "__len__"):
        return
    import numpy as np
    pose_arr = np.asarray(pose, dtype=np.float32)
    if pose_arr.ndim != 3 or pose_arr.shape[1] != 17:
        return
    T = int(pose_arr.shape[0])
    seq = {
        "origin": "real",
        "format": "coco17",
        "n_frames": T,
        "fps": 16,  # v2.2 模型采样契约
        "coords": "normalized",
        "keypoints": [[[round(float(x), 4), round(float(y), 4)]
                       for x, y, _ in frame] for frame in pose_arr],
        "confidences": [[round(float(c), 4) for _, _, c in frame] for frame in pose_arr],
    }
    from app.services.fall_event_archive import update_event
    update_event(event_id, skeleton_sequence=seq)
    logger.info(f"FALL skeleton saved: {event_id} ({T}帧)")


def _dump_fall_clip(event_id: str) -> str:
    """把当前管线帧缓冲（含跌倒帧）直写为视频片段。

    不使用第二条 RTSP 连接（cv2 并发读 RTSP 会触发 libavcodec/libavformat
    原生 segfault，2026-08-13 dmesg 实锤），从本管线已捕获的帧写 mp4v 再转 H.264。
    """
    frames = list(_frame_buffer)
    if len(frames) < 8:
        return ""
    try:
        import cv2 as _cv2
        from pathlib import Path
        from app.services.capture_assets import _transcode_h264, CLIPS_DIR

        CLIPS_DIR.mkdir(parents=True, exist_ok=True)
        out = CLIPS_DIR / f"fall_{event_id}.mp4"
        h, w = frames[-1].shape[:2]
        writer = _cv2.VideoWriter(str(out), _cv2.VideoWriter_fourcc(*"mp4v"), 10, (w, h))
        for f in frames:
            writer.write(f)
        writer.release()
        if not out.exists() or out.stat().st_size < 10_000:
            try:
                out.unlink()
            except Exception:
                pass
            return ""
        # 转 H.264（浏览器/小程序可播）
        tmp = out.with_suffix(".tmp.mp4")
        if _transcode_h264(str(out), str(tmp)):
            try:
                import os
                os.replace(tmp, out)
            except Exception:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except Exception:
                    pass
        return str(out)
    except Exception as e:
        logger.warning(f"_dump_fall_clip failed: {e}")
        return ""


# ---------------------------------------------------------------------------
# Main pipeline entry point
# ---------------------------------------------------------------------------

def process_frame(bgr_frame: np.ndarray) -> dict[str, Any]:
    """
    Three-stage pipeline entry point. Called for each incoming frame.

    Returns dict with keys:
        persons, yolo_ms, fall_prob, world_av_ms,
        alert_active, alert_level, pose_result, annotated_jpeg_b64
    """
    global _last_persons, _last_yolo_ms, _last_world_av_result
    global _last_world_av_ms, _fall_alert_active, _last_pose_frame_idx, _frame_seq
    global _last_person_gate_source, _last_person_evidence_frame, _last_suppression_reason
    global _last_raw_frame, _annotated_jpeg, _annotated_jpeg_bytes, _last_frame_w, _last_frame_h
    global _raw_consecutive, _last_alert_ts, _next_model_submit_at
    global _pose_cache_frames, _pose_cache_hits, _last_frame_timestamp

    result: dict[str, Any] = {
        "persons": [],
        "yolo_ms": 0.0,
        "fall_prob": 0.0,
        "world_av_ms": 0.0,
        "alert_active": False,
        "alert_level": "none",
        "pose_result": None,
        "annotated_jpeg_b64": None,
        "fallback_mode": _use_fallback,
        "model_mode": _mode,
        "threshold": SPATIAL_V5_THRESHOLD if _mode in ("spatial_v5", "v5", "spatial") else _smoother.config.threshold,
    }

    _frame_seq += 1
    current_frame_idx = _frame_seq
    _last_frame_timestamp = time.time()
    _last_raw_frame = bgr_frame
    push_frame(bgr_frame)
    h, w = bgr_frame.shape[:2]

    # ---- Stage 1: Person/Pose cache ----
    # Spatial v5's Pose model already returns the primary person box. Reusing
    # it removes a second per-frame YOLO detector and keeps the box aligned
    # with the 32-frame model window. Legacy modes retain their old detector.
    t0 = time.perf_counter()
    if _mode in ("spatial_v5", "v5", "spatial"):
        try:
            if not _init_spatial_v5():
                raise RuntimeError("Spatial v5 unavailable")
            current_pose, current_box = _spatial_v5_runtime.pose_for_frame(bgr_frame)
            _spatial_pose_buffer.append((current_frame_idx, current_pose, current_box))
            _pose_cache_frames += 1
            persons = _persons_from_pose_box(current_box, bgr_frame.shape)
        except Exception as exc:
            logger.warning("Spatial v5 Pose cache frame failed: %s", exc)
            _spatial_pose_buffer.append((current_frame_idx, np.zeros((17, 3), np.float32), np.zeros(6, np.float32)))
            persons = []
    else:
        persons = detect_persons(bgr_frame, conf=0.35)
    yolo_ms = (time.perf_counter() - t0) * 1000
    with _state_lock:
        _last_persons = persons
        _last_yolo_ms = yolo_ms
        _last_frame_w = w
        _last_frame_h = h
    result["persons"] = persons
    result["yolo_ms"] = round(yolo_ms, 1)

    # ---- Stage 2: Fall detection ----
    # YOLO 预检模式: 无人时跳过推理（省算力）。注意跌倒瞬间 YOLO 可能短暂漏检，
    # 若需要不丢帧可切换 yolo_gate=False（直接检测）。
    n_history = (
        max(WORDPOSE_V2_REQUIRED_HISTORY, VJEPA2_CONTEXT_FRAMES)
        if _mode in ("worldpose", "v2", "worldpose_v2")
        else VJEPA2_CONTEXT_FRAMES
    )
    frames = _get_frames_for_world_av(n_history)
    # [2026-08-13] 异步解耦: worldpose/worldav 重推理在 worker 线程, 采集线程每帧提交、
    # 非阻塞消费最新结果 → 标注帧不再被 ~950ms 推理冻结; KD fallback 轻量仍内联每帧。
    av_result = None
    if frames and (persons or not _yolo_gate):
        if not _use_fallback:
            pose_window = _spatial_pose_window(n_history) if _mode in ("spatial_v5", "v5", "spatial") else None
            now = time.monotonic()
            if now >= _next_model_submit_at:
                _submit_inference(
                    frames,
                    _get_small_frames_for_world_av(n_history),
                    pose=pose_window[0] if pose_window else None,
                    boxes=pose_window[1] if pose_window else None,
                    person_evidence=persons,
                    person_evidence_frame=current_frame_idx,
                )
                if pose_window:
                    _pose_cache_hits += len(pose_window[0])
                _next_model_submit_at = now + 1.0 / _model_hz
            av_result = _consume_inference_result()
        else:
            av_result = _run_world_av(frames)
        if av_result:
            with _state_lock:
                _last_world_av_result = av_result
                _last_world_av_ms = av_result.get("_inference_ms", 0)
            result["world_av_ms"] = round(_last_world_av_ms, 1)

            p_fall = av_result.get("final_probability", 0)
            # 注意返回顺序: (smoothed, pred, hit_count) — pred 才是告警信号
            # (smoother 内部已处理 min_consecutive + cooldown)
            smoothed, smooth_pred, hit_count = _smoother.update(p_fall)
            result["fall_prob"] = round(smoothed, 4)
            result["raw_prob"] = round(p_fall, 4)
            # 给每个人附上最新跌倒概率（前端单人卡片显示）
            for p in persons:
                p["fall_prob"] = round(smoothed, 4)
            result["hit_count"] = hit_count

            # v2.2 原始命中: [2026-08-13 用户决策] 跌倒短促, 单次超门限即告警(异步下窗口滑得快,
            # 连续2次易漏); _raw_consecutive 仍记录供前端显示。EMA smoother 路径仍要求连续命中兜底。
            _raw_consecutive = _raw_consecutive + 1 if p_fall >= _smoother.config.threshold else 0
            raw_alert = p_fall >= _smoother.config.threshold
            result["raw_hit_count"] = _raw_consecutive

            # Include gate metrics for frontend display
            result["p_visual"] = round(av_result.get("visual_probability", 0), 4)
            result["p_av"] = round(av_result.get("av_probability", 0), 4)
            result["p_world"] = round(av_result.get("world_probability", 0), 4)
            result["p_pose"] = round(av_result.get("pose_probability", 0), 4)
            result["p_future_warning"] = round(av_result.get("future_warning_probability", 0), 4)
            result["warning"] = bool(av_result.get("warning", False))
            result["gate"] = round(av_result.get("gate", 0), 4)
            result["audio_available"] = av_result.get("audio_available", False)

            # 异步结果必须使用“推理提交帧”的人物证据；当前显示帧只负责画框。
            person_evidence = av_result.get("_person_evidence")
            if isinstance(person_evidence, dict):
                gate_persons = list(person_evidence.get("persons") or [])
                gate_person_present = bool(person_evidence.get("present"))
                gate_source = str(person_evidence.get("source") or "inference_submit_frame")
                gate_frame = int(person_evidence.get("frame") or 0)
            else:
                gate_persons = list(persons)
                gate_person_present = bool(gate_persons)
                gate_source = "current_frame"
                gate_frame = current_frame_idx
            _last_person_gate_source = gate_source
            _last_person_evidence_frame = gate_frame
            result["person_gate_source"] = gate_source
            result["person_evidence_frame"] = gate_frame
            result["person_evidence_count"] = len(gate_persons)

            from app.services.fall_event_archive import get_active_workflow
            active_workflow = get_active_workflow()
            active_event_id = active_workflow.event_id if active_workflow else ""
            if active_workflow:
                _last_suppression_reason = f"active_workflow:{active_event_id}"
            else:
                _last_suppression_reason = ""

            # Alert logic: smooth_pred 是 smoother 的告警信号; raw_alert 覆盖快速跌倒。
            if smooth_pred or raw_alert:
                if gate_person_present:
                    if not _fall_alert_active:
                        _fall_alert_active = True
                        logger.info(
                            f"FALL ALERT: p={smoothed:.3f} raw={p_fall:.3f} "
                            f"hits={hit_count} raw_hits={_raw_consecutive} "
                            f"persons={len(gate_persons)} source={gate_source} frame={gate_frame}"
                        )
                        _last_pose_frame_idx = current_frame_idx
                        _trigger_pose(_last_pose_frame_idx)
                    _update_pose(current_frame_idx, bgr_frame)
                    if active_workflow:
                        _last_suppression_reason = f"active_workflow:{active_event_id}"
                    elif _on_fall_alert(_pose_result, av_result):
                        _last_suppression_reason = ""
                else:
                    _last_suppression_reason = "person_gate_failed"
                    logger.warning(
                        f"FALL prob 高但无人(人物证据缺失), 不报警: p={p_fall:.3f} "
                        f"smooth={smoothed:.3f} hits={hit_count}/{_raw_consecutive} "
                        f"source={gate_source} frame={gate_frame}"
                    )
            else:
                if _fall_alert_active:
                    _fall_alert_active = False
                _v5_triggered = False
                _last_suppression_reason = ""

    # 每帧回填最新已知概率（异步模式两帧推理之间前端概率保持显示一致）
    if av_result is None and _last_world_av_result:
        result["fall_prob"] = round(_last_world_av_result.get("final_probability", 0), 4)

    # Alert status
    result["alert_active"] = _fall_alert_active
    if _pose_result:
        result["pose_result"] = dict(_pose_result)
        result["alert_level"] = _pose_result.get("risk_level", "none")

    # 推理结果可能在本帧的异步消费阶段刚刚更新；再用当前帧重绘一次，
    # 让骨骼/未来骨骼/概率面板尽量与画面同步，同时保留 Stage 1 的低延迟帧。
    try:
        latest = _last_world_av_result if _last_world_av_result else None
        _annotated_jpeg = _draw_overlay(bgr_frame, persons, latest)
        _annotated_jpeg_bytes = _encode_jpeg_bytes(_annotated_jpeg, max_width=960, quality=80)
    except Exception:
        pass
    result["annotated_jpeg_b64"] = None  # 兼容旧键; 前端已走 annotated-frame 直接取字节
    try:
        from app.services.fall_event_archive import get_active_workflow
        active = get_active_workflow()
        result["active_event_id"] = active.event_id if active else ""
        result["workflow_active"] = bool(active)
        result["suppression_reason"] = _last_suppression_reason
    except Exception:
        result["active_event_id"] = ""
        result["workflow_active"] = False
        result["suppression_reason"] = _last_suppression_reason

    return result


# ---------------------------------------------------------------------------
# Overlay drawing
# ---------------------------------------------------------------------------

_COCO_EDGES = ((5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12),
               (11, 12), (11, 13), (13, 15), (12, 14), (14, 16),
               (0, 1), (0, 2), (1, 3), (2, 4))


def _draw_pose_lines(frame: np.ndarray, pose: np.ndarray | None,
                     visibility: np.ndarray | None, color: tuple[int, int, int],
                     point_radius: int = 4, thickness: int = 2) -> None:
    """在原始 BGR 帧上绘制归一化 COCO-17 骨骼。"""
    if pose is None:
        return
    arr = np.asarray(pose)
    if arr.ndim == 3:
        arr = arr[-1]
    if arr.ndim != 2 or arr.shape[1] < 2:
        return
    h, w = frame.shape[:2]
    visible = np.asarray(visibility).reshape(-1) if visibility is not None else None
    points: dict[int, tuple[int, int]] = {}
    for index, item in enumerate(arr[:17]):
        if visible is not None and index < len(visible) and float(visible[index]) < 0.20:
            continue
        confidence = float(item[2]) if item.shape[0] > 2 else 1.0
        if confidence < 0.20:
            continue
        x, y = float(item[0]), float(item[1])
        if not (0 <= x <= 1.2 and 0 <= y <= 1.2):
            continue
        points[index] = (int(np.clip(x, 0, 1) * w), int(np.clip(y, 0, 1) * h))
    for a, b in _COCO_EDGES:
        if a in points and b in points:
            cv2.line(frame, points[a], points[b], color, thickness, cv2.LINE_AA)
    for point in points.values():
        cv2.circle(frame, point, point_radius, color, -1, cv2.LINE_AA)

def _draw_overlay(
    frame: np.ndarray,
    persons: list[dict],
    av_result: dict[str, Any] | None,
) -> np.ndarray:
    """Draw person bboxes + fall probability + gate info on frame."""
    result = frame.copy()
    h, w = result.shape[:2]

    # Draw person bboxes
    for i, p in enumerate(persons):
        x1, y1, x2, y2 = p["bbox"]
        conf = p.get("confidence", 0)
        fp = p.get("fall_prob", None)

        if fp is not None:
            r = min(255, int(fp * 300))
            g = max(0, int(255 - fp * 300))
            color = (0, g, r)
            label = f"P#{i} fall={fp:.2f}"
        else:
            color = (0, 255, 0)
            label = f"P#{i} conf={conf:.2f}"

        cv2.rectangle(result, (x1, y1), (x2, y2), color, 2)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)
        cv2.rectangle(result, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
        cv2.putText(result, label, (x1 + 2, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)

    # Draw world AV status panel
    if av_result:
        p = av_result.get("final_probability", 0)
        gate = av_result.get("gate", 0)
        status = "ALERT" if _fall_alert_active else "OK"
        color = (0, 0, 255) if _fall_alert_active else (0, 255, 100)
        if _mode in ("spatial_v5", "v5", "spatial"):
            lines = [
                f"Spatial v5 | p_final={p:.3f} threshold={av_result.get('threshold', SPATIAL_V5_THRESHOLD):.6f}",
                f"p_future_warning={av_result.get('future_warning_probability', 0):.3f} gate={gate:.3f} [{status}]",
            ]
            _draw_pose_lines(result, _spatial_v5_pose, None, (255, 190, 30), 4, 2)
            _draw_pose_lines(result, _spatial_v5_future_pose, _spatial_v5_future_visibility,
                             (30, 150, 255), 3, 2)
        else:
            lines = [f"WorldAV | P={p:.3f} Gate={gate:.3f} [{status}]"]
        if _pose_result and _pose_result.get("active"):
            lines.append(
                f"Contact: {_pose_result.get('first_contact_part', '?')} "
                f"({_pose_result.get('first_side', '?')}) "
                f"Risk: {_pose_result.get('risk_level', '?')}"
            )
        panel_h = 28 + 22 * len(lines)
        panel_w = min(w - 20, 580)
        x0, y0 = 10, 10
        overlay = result.copy()
        cv2.rectangle(overlay, (x0, y0), (x0 + panel_w, y0 + panel_h), (12, 18, 28), -1)
        cv2.addWeighted(overlay, 0.75, result, 0.25, 0, result)
        for idx, line in enumerate(lines):
            cv2.putText(result, line, (x0 + 10, y0 + 22 + idx * 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)

    return result


def _encode_b64(frame: np.ndarray | None) -> str | None:
    if frame is None:
        return None
    _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return "data:image/jpeg;base64," + __import__("base64").b64encode(jpeg.tobytes()).decode()


def _encode_jpeg_bytes(frame: np.ndarray | None, max_width: int = 0, quality: int = 80) -> bytes | None:
    """Encode one annotated frame with bounded resize and configurable quality."""
    if frame is None:
        return None
    if max_width > 0:
        h, w = frame.shape[:2]
        if w > max_width:
            frame = cv2.resize(frame, (max_width, int(h * max_width / w)))
    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, int(np.clip(quality, 40, 95))])
    return jpeg.tobytes() if ok else None


# ---------------------------------------------------------------------------
# Query helpers (for API endpoints)
# ---------------------------------------------------------------------------

def get_annotated_frame() -> bytes | None:
    """Return latest annotated frame as JPEG bytes."""
    return _annotated_jpeg_bytes


def get_pipeline_status() -> dict[str, Any]:
    """Return full pipeline status for API.

    Contains both new WorldAV fields and legacy fields (person_details,
    last_persons, yolo_time_ms) for frontend compatibility.
    """
    with _state_lock:
        persons = list(_last_persons)
        av = dict(_last_world_av_result)
    fall_prob = round(av.get("final_probability", 0), 4)

    # 兼容旧版前端: person_details (每人 bbox/conf/fall_prob/crop)
    details = []
    frame = _last_raw_frame
    for i, p in enumerate(persons):
        crop_b64 = None
        if frame is not None:
            try:
                h, w = frame.shape[:2]
                x1, y1, x2, y2 = p["bbox"]
                x1c, y1c = max(0, x1 - 10), max(0, y1 - 10)
                x2c, y2c = min(w, x2 + 10), min(h, y2 + 10)
                crop = frame[y1c:y2c, x1c:x2c]
                if crop.size > 0:
                    _, jpeg = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 60])
                    crop_b64 = "data:image/jpeg;base64," + __import__("base64").b64encode(jpeg.tobytes()).decode()
            except Exception:
                pass
        details.append({
            "idx": i,
            "bbox": p.get("bbox"),
            "conf": p.get("confidence", 0),
            "fall_prob": p.get("fall_prob", fall_prob if fall_prob > 0 else None),
            "crop_b64": crop_b64,
        })

    return {
        "persons_count": len(persons),
        "last_persons": len(persons),
        "person_details": details,
        "yolo_time_ms": round(_last_yolo_ms, 1),
        "yolo_ms": round(_last_yolo_ms, 1),
        "world_av_ms": round(_last_world_av_ms, 1),
        "fall_prob": fall_prob,
        "p_visual": round(av.get("visual_probability", 0), 4),
        "p_av": round(av.get("av_probability", 0), 4),
        "gate": round(av.get("gate", 0), 4),
        "audio_available": bool(av.get("audio_available", False) or _audio_stream_expected),
        "audio_stream_expected": bool(_audio_stream_expected),
        "alert_active": _fall_alert_active,
        "fallback_mode": _use_fallback,
        "model_mode": (
            "kd" if _use_fallback
            else ("spatial_v5" if _mode in ("spatial_v5", "v5", "spatial")
                  else ("worldpose" if _mode in ("worldpose", "v2", "worldpose_v2") else "worldav"))
        ),
        "yolo_gate": _yolo_gate,
        "pose_result": dict(_pose_result) if _pose_result else None,
        "buffer_frames": len(_frame_buffer),
        # World-Pose v2.2 附加字段
        "world_probability": round(av.get("world_probability", 0), 4),
        "world_base_probability": round(av.get("world_base_probability", 0), 4),
        "p_candidate": round(av.get("p_candidate", 0), 4),
        "p_warning": round(av.get("p_warning", 0), 4),
        "domain_reliability": round(av.get("domain_reliability", 0), 4),
        "pose_probability": round(av.get("pose_probability", 0), 4),
        "pose_reliability": round(av.get("pose_reliability", 0), 4),
        "crop_gate": round(av.get("crop_gate", 0), 4),
        "motion_risk": round(av.get("motion_risk", 0), 4),
        "detector_threshold": av.get(
            "threshold",
            SPATIAL_V5_THRESHOLD if _mode in ("spatial_v5", "v5", "spatial") else 0.0,
        ),
        "threshold": av.get(
            "threshold",
            SPATIAL_V5_THRESHOLD if _mode in ("spatial_v5", "v5", "spatial") else _smoother.config.threshold,
        ),
        "p_final": fall_prob,
        "p_world": round(av.get("world_probability", 0), 4),
        "p_pose": round(av.get("pose_probability", 0), 4),
        "p_future_warning": round(av.get("future_warning_probability", 0), 4),
        "warning": bool(av.get("warning", False)),
        "alarm": bool(_fall_alert_active),
        "inference_ms": round(_last_world_av_ms, 1),
        "model_hz": _model_hz,
        "actual_model_hz": round(_model_complete_count / max(time.time() - _model_window_started_at, 1e-3), 2)
        if _model_complete_count and _model_window_started_at else 0.0,
        "pose_cache_frames": _pose_cache_frames,
        "pose_cache_hit_rate": round(min(1.0, _pose_cache_hits / max(_pose_cache_frames, 1)), 3),
        "dropped_stale_windows": _dropped_stale_windows,
        "last_frame_timestamp": _last_frame_timestamp,
        "warmup_ready": bool(_spatial_v5_runtime is not None and getattr(_spatial_v5_runtime, "_warm", True))
        if _mode in ("spatial_v5", "v5", "spatial") else True,
        "active_event_id": (
            __import__("app.services.fall_event_archive", fromlist=["get_active_workflow"])
            .get_active_workflow().event_id
            if __import__("app.services.fall_event_archive", fromlist=["get_active_workflow"]).get_active_workflow()
            else ""
        ),
        "workflow_active": (
            __import__("app.services.fall_event_archive", fromlist=["workflow_is_active"])
            .workflow_is_active()
        ),
        "person_gate_source": _last_person_gate_source,
        "person_evidence_frame": _last_person_evidence_frame,
        "suppression_reason": _last_suppression_reason,
    }


def get_tracking_snapshot() -> tuple[list[dict], int, int] | None:
    """供 PTZ 追踪线程使用的人员位置快照。

    Returns:
        (persons, frame_w, frame_h) — persons 按面积降序，bbox 为全帧像素坐标。
        pipeline 尚未处理任何帧时返回 None。
    """
    with _state_lock:
        if _last_frame_w <= 0 or _last_frame_h <= 0:
            return None
        return list(_last_persons), _last_frame_w, _last_frame_h


def reset_pipeline():
    """Reset all pipeline state."""
    global _fall_alert_active, _pose_result, _pose_analyzer, _v5_triggered, _raw_consecutive
    global _infer_request, _infer_result, _infer_result_seq, _infer_seq_seen, _last_alert_ts, _next_model_submit_at
    global _spatial_v5_pose, _spatial_v5_future_pose, _spatial_v5_future_visibility, _frame_seq
    global _model_submit_count, _model_complete_count, _dropped_stale_windows, _pose_cache_frames, _pose_cache_hits, _last_frame_timestamp, _model_window_started_at
    global _event_bridge_inflight, _last_person_gate_source, _last_person_evidence_frame, _last_suppression_reason
    _frame_buffer.clear()
    _spatial_pose_buffer.clear()
    _frame_buffer_small.clear()
    _audio_buffer.clear()
    _smoother.reset()
    _fall_alert_active = False
    _v5_triggered = False
    _raw_consecutive = 0
    _spatial_v5_pose = None
    _spatial_v5_future_pose = None
    _spatial_v5_future_visibility = None
    _last_alert_ts = 0.0
    _next_model_submit_at = 0.0
    _frame_seq = 0
    _model_submit_count = 0
    _model_complete_count = 0
    _dropped_stale_windows = 0
    _pose_cache_frames = 0
    _pose_cache_hits = 0
    _last_frame_timestamp = 0.0
    _model_window_started_at = 0.0
    _pose_result = None
    _pose_analyzer = None
    _event_bridge_inflight = False
    _last_person_gate_source = "current_frame"
    _last_person_evidence_frame = 0
    _last_suppression_reason = ""
    with _infer_cond:
        _infer_request = None
        _infer_result = None
        _infer_result_seq = 0
        _infer_seq_seen = 0
    logger.info("WorldAV pipeline reset")


def is_available() -> bool:
    """Check if WorldAV is available (not in fallback)."""
    return not _use_fallback and _init_world_av() is True
