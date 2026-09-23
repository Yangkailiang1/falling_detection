"""
YOLO11n 人形检测器 — 轻量本地人脸+人体检测

YOLO 为什么比 KD-Stride-2 快？
  - 单帧 CNN 直通（无时序建模/无 GRU/无滑动窗口）
  - 2.6M 参数 vs KD 4.1M + 32 帧 × CNN
  - 专为检测优化（anchor-free, NMS, 高效 backbone）
"""

import logging
import numpy as np
from ultralytics import YOLO
from app.inference.inference_config import WEIGHTS

logger = logging.getLogger(__name__)

# 全局单例
_model = None


def get_model():
    global _model
    if _model is None:
        _model = YOLO(WEIGHTS["yolo11n"])
        # 预热
        dummy = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        _model(dummy, classes=[0], verbose=False)
        logger.info(f'YOLO11n loaded ({sum(p.numel() for p in _model.model.parameters()):,} params)')
    return _model


def detect_persons(frame: np.ndarray, conf: float = 0.4):
    """
    检测画面中的人体。

    Args:
        frame: (H, W, 3) BGR numpy array
        conf:  置信度阈值

    Returns:
        [
            {'bbox': (x1,y1,x2,y2), 'confidence': float, 'area': int},
            ...
        ]
        无人返回空列表。
    """
    model = get_model()
    results = model(frame, classes=[0], conf=conf, verbose=False)

    persons = []
    if len(results) > 0 and results[0].boxes is not None:
        boxes = results[0].boxes
        for i in range(len(boxes)):
            xyxy = boxes.xyxy[i].cpu().numpy()
            # 跳过框太小的人形（可能是远处或遮挡）
            w, h = xyxy[2] - xyxy[0], xyxy[3] - xyxy[1]
            if w < 30 or h < 60:
                continue
            persons.append({
                'bbox': (int(xyxy[0]), int(xyxy[1]), int(xyxy[2]), int(xyxy[3])),
                'confidence': float(boxes.conf[i]),
                'area': int(w * h),
            })

    # 按面积降序（主要人物在前）
    persons.sort(key=lambda p: p['area'], reverse=True)
    return persons
