"""
两级跌倒检测管线 — YOLO人形预检 + KD-Stride-2 跌倒检测

    720p帧 → YOLO11n (75ms) → 人框标注
        ├─ 无人 → 跳过跌倒
        └─ 有人 → 每人bbox裁剪 → KD-Stride-2(250ms) → P_i(fall)
                   → 画框 + 标注概率 → 保存标注帧(JPEG)
"""

import io
import time
import base64
import logging
import threading
from collections import deque

import cv2
import numpy as np

from app.inference.person_detector import detect_persons
from app.inference.orchestrator import add_frame_bytes, infer

logger = logging.getLogger(__name__)

# 全局状态
_last_persons = []
_last_yolo_time = 0.0
_last_fall_predictions = {}
_lock = threading.Lock()

# 最新标注帧（JPEG bytes，线程安全）
_annotated_frame_jpeg = None
_last_frame = None  # 原始 BGR 帧，用于裁剪单人图
_person_history = deque(maxlen=50)  # 每人历史概率


def _draw_bboxes(frame, persons):
    """在帧上绘制人形框和概率标签"""
    result = frame.copy()
    for i, p in enumerate(persons):
        x1, y1, x2, y2 = p['bbox']
        conf = p.get('confidence', 0)
        fp = p.get('fall_prob', None)

        # 颜色：越危险越红
        if fp is not None:
            r = min(255, int(fp * 300))
            g = max(0, int(255 - fp * 300))
            color = (0, g, r)  # BGR
            label = f"P#{i} fall={fp:.2f}"
        else:
            color = (0, 255, 0)
            label = f"P#{i} conf={conf:.2f}"

        cv2.rectangle(result, (x1, y1), (x2, y2), color, 2)
        # 标签背景
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)
        cv2.rectangle(result, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
        cv2.putText(result, label, (x1 + 2, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
    return result


def process_frame(bgr_frame, run_fall=True):
    """
    处理一帧，画框 + 标注概率，返回标注帧 JPEG bytes。
    """
    global _last_persons, _last_yolo_time, _last_fall_predictions
    t0 = time.time()

    persons = detect_persons(bgr_frame, conf=0.35)
    yolo_ms = (time.time() - t0) * 1000

    global _last_frame
    _last_frame = bgr_frame  # 保存原始帧用于裁剪单人图

    with _lock:
        _last_persons = persons
        _last_yolo_time = yolo_ms

    if persons and run_fall:
        t1 = time.time()
        h, w = bgr_frame.shape[:2]
        for i, p in enumerate(persons):
            x1, y1, x2, y2 = p['bbox']
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if x2 - x1 < 30 or y2 - y1 < 60:
                continue
            crop = bgr_frame[y1:y2, x1:x2]
            _, jpeg = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 60])
            add_frame_bytes(jpeg.tobytes())
            inf = infer()
            if inf.get('ready'):
                p['fall_prob'] = inf.get('probability', 0)
                _last_fall_predictions[i] = p['fall_prob']

    # 画框 + 编码
    annotated = _draw_bboxes(bgr_frame, persons)
    _, jpeg_bytes = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 70])
    global _annotated_frame_jpeg
    _annotated_frame_jpeg = jpeg_bytes.tobytes()

    return {
        'persons': persons,
        'yolo_ms': yolo_ms,
        'fall_predictions': dict(_last_fall_predictions),
    }


def get_annotated_frame():
    """返回最新标注帧 (JPEG bytes)，无则返回 None"""
    return _annotated_frame_jpeg


def process_frame_worldav(bgr_frame) -> dict:
    """
    使用 WorldAV 三级管线处理一帧（Stage 1+2+3）。
    当 WorldAV 不可用时自动降级到 KD-Stride-2。

    Returns 与 process_frame() 兼容的 dict。
    """
    from app.inference.world_av_pipeline import process_frame as wp_process
    global _last_persons, _last_yolo_time, _last_fall_predictions
    global _last_frame, _annotated_frame_jpeg

    _last_frame = bgr_frame
    result = wp_process(bgr_frame)

    with _lock:
        _last_persons = result.get("persons", [])
        _last_yolo_time = result.get("yolo_ms", 0)
        if result.get("fall_prob", 0) > 0:
            for i, p in enumerate(_last_persons):
                p["fall_prob"] = result.get("fall_prob", 0)
                _last_fall_predictions[i] = p.get("fall_prob", 0)

    # [2026-08-13] 直接从 world_av_pipeline 取缩小后的原始 JPEG 字节(去掉每帧 base64 编解码往返)
    try:
        from app.inference.world_av_pipeline import get_annotated_frame as _wp_anno
        _jpeg = _wp_anno()
        if _jpeg:
            _annotated_frame_jpeg = _jpeg
    except Exception:
        pass

    return result


def get_pipeline_status():
    with _lock:
        persons = list(_last_persons)
    details = []
    for i, p in enumerate(persons):
        x1, y1, x2, y2 = p['bbox']
        crop_b64 = None
        try:
            if _last_frame is not None:
                h, w = _last_frame.shape[:2]
                x1c = max(0, x1-10); y1c = max(0, y1-10)
                x2c = min(w, x2+10); y2c = min(h, y2+10)
                crop = _last_frame[y1c:y2c, x1c:x2c]
                _, jpeg = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 60])
                crop_b64 = f'data:image/jpeg;base64,{base64.b64encode(jpeg.tobytes()).decode()}'
        except Exception:
            pass
        details.append({
            'idx': i, 'bbox': p['bbox'], 'conf': p.get('confidence', 0),
            'fall_prob': p.get('fall_prob'), 'crop_b64': crop_b64,
        })
    return {
        'last_persons': len(persons),
        'yolo_time_ms': round(_last_yolo_time, 1),
        'person_details': details,
    }
