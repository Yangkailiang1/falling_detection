"""
实时推理编排器 — CNN 特征缓存 + 滑动窗口

优化策略（~30x 提速）:
  MobileNetV3 逐帧处理（无时序依赖）→ 缓存 32 帧 CNN 特征
  新帧到达时仅计算 1 帧 CNN，分类时直接复用缓存特征 + BiGRU
  → 推理延迟从 ~880ms 降至 ~30ms

流水线:
    摄像头帧 → 预处理 → MobileNetV3(1帧) → 特征缓存(32帧)
                                              ↓
                                          BiGRU + Fusion → P(fall)
"""

import io
import time
import base64
import logging
import threading
from collections import deque

import torch
import numpy as np
from PIL import Image
from torchvision import transforms

from app.inference.fall_detector import (
    load_model, INPUT_FRAMES, INPUT_SIZE,
    extract_cnn_features, classify_from_features,
)

logger = logging.getLogger(__name__)

# ImageNet 预处理
_preprocess = transforms.Compose([
    transforms.Resize((INPUT_SIZE, INPUT_SIZE)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406],
                         std=[0.229, 0.224, 0.225]),
])

# 特征缓存（CNN 输出，保留在 GPU/MPS 上）
_feature_cache = deque(maxlen=INPUT_FRAMES)  # 每个元素: (1, 576) tensor on device
_frame_buffer = deque(maxlen=INPUT_FRAMES)    # 原始 tensor（用于首次填充）

_threshold = 0.5
_last_prob = 0.0
_last_inference_time = 0.0
_lock = threading.Lock()
_inference_count = 0
_model_loaded = False


def _ensure_model():
    global _model_loaded
    if not _model_loaded:
        load_model()
        _model_loaded = True


def preprocess_frame(image_bytes: bytes) -> torch.Tensor:
    """JPEG → ImageNet 归一化 tensor"""
    img = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    return _preprocess(img)  # (3, 224, 224)


def add_frame_bytes(image_bytes):
    """添加 JPEG 字节串帧到缓冲区（用于 RTSP 视频捕获）"""
    _ensure_model()
    tensor = preprocess_frame(image_bytes)
    with _lock:
        _frame_buffer.append(tensor)


def add_frame(image_bytes_or_path):
    """
    添加一帧到滑动窗口（仅预处理，不跑 CNN）。

    Args:
        image_bytes_or_path: JPEG 字节串 或 图片文件路径
    """
    _ensure_model()

    if isinstance(image_bytes_or_path, str):
        with open(image_bytes_or_path, 'rb') as f:
            image_bytes = f.read()
    else:
        image_bytes = image_bytes_or_path

    tensor = preprocess_frame(image_bytes)
    with _lock:
        _frame_buffer.append(tensor)


def infer() -> dict:
    """
    对当前滑动窗口执行推理。

    首次 32 帧：全量推理（32 帧 CNN + BiGRU）
    之后每帧：增量推理（1 帧 CNN + 缓存复用 + BiGRU）

    Returns:
        {probability, is_fall, buffer_size, inference_time_ms, ready, cached}
    """
    global _last_prob, _last_inference_time, _inference_count

    _ensure_model()

    with _lock:
        buf_size = len(_frame_buffer)
        feat_size = len(_feature_cache)
        cold = feat_size < INPUT_FRAMES

        if cold and buf_size < INPUT_FRAMES:
            return {
                'probability': 0.0, 'is_fall': False,
                'buffer_size': buf_size, 'inference_time_ms': 0,
                'ready': False, 'cached': False,
            }
        if not cold and buf_size == 0:
            # 热路径无新帧，返回上次结果
            return {
                'probability': round(_last_prob, 4),
                'is_fall': _last_prob >= _threshold,
                'buffer_size': 0, 'inference_time_ms': round(_last_inference_time, 1),
                'ready': True, 'cached': True,
            }

        new_tensors = list(_frame_buffer)
        _frame_buffer.clear()

    t0 = time.time()

    if cold:
        # === 冷启动: 全量推理 ===
        batch = torch.stack(new_tensors[-INPUT_FRAMES:]).unsqueeze(0)
        model, device = load_model()
        with torch.no_grad():
            frames = batch.squeeze(0).to(device)
            features = model.video_encoder._cnn(frames)
            for i in range(features.shape[0]):
                with _lock:
                    _feature_cache.append(features[i:i+1])
            logits = model(batch)
            prob = torch.sigmoid(logits).item()
        elapsed = (time.time() - t0) * 1000
        _last_prob = prob; _last_inference_time = elapsed
        _inference_count += 1
        return {
            'probability': round(prob, 4), 'is_fall': prob >= _threshold,
            'buffer_size': buf_size, 'inference_time_ms': round(elapsed, 1),
            'ready': True, 'cached': False,
        }
    else:
        # === 热路径: 增量推理 ===
        for tensor in new_tensors:
            feat = extract_cnn_features(tensor)
            with _lock:
                _feature_cache.append(feat)
        with _lock:
            stacked = torch.cat(list(_feature_cache), dim=0).unsqueeze(0)
        prob = classify_from_features(stacked)
        elapsed = (time.time() - t0) * 1000
        _last_prob = prob; _last_inference_time = elapsed
        _inference_count += 1
        return {
            'probability': round(prob, 4), 'is_fall': prob >= _threshold,
            'buffer_size': buf_size, 'inference_time_ms': round(elapsed, 1),
            'ready': True, 'cached': True,
        }


def get_status() -> dict:
    with _lock:
        buf = len(_frame_buffer)
        feat = len(_feature_cache)
    return {
        'buffer_size': buf,
        'feature_cache_size': feat,
        'buffer_full': feat >= INPUT_FRAMES,
        'last_probability': round(_last_prob, 4),
        'last_inference_time_ms': round(_last_inference_time, 1),
        'threshold': _threshold,
        'total_inferences': _inference_count,
        'ready': feat >= INPUT_FRAMES,
    }


def set_threshold(value: float):
    global _threshold
    _threshold = max(0.0, min(1.0, value))


def reset():
    global _last_prob, _inference_count
    with _lock:
        _frame_buffer.clear()
        _feature_cache.clear()
    _last_prob = 0.0
    _inference_count = 0
    logger.info('Orchestrator reset — buffers cleared')
