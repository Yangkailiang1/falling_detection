"""
跌倒检测模型 — KD-Stride-2 蒸馏学生模型

架构: MobileNetV3-Small(frozen) → LayerNorm → 2-layer BiGRU → Fusion MLP → Classifier
输入: (B, 32, 3, 224, 224)  输出: P(fall) ∈ [0, 1]
模型: student_best.pt (40MB, 4.1M params, F1=0.8917)
"""

import logging
import torch
import torch.nn as nn
from torchvision import models

logger = logging.getLogger(__name__)

from app.inference.inference_config import WEIGHTS
MODEL_PATH = WEIGHTS["student_best"]
INPUT_FRAMES = 32  # 滑动窗口帧数
INPUT_SIZE = 224    # 帧尺寸


class CNNBackbone(nn.Module):
    """包装器: 将 MobileNetV3 features+avgpool 暴露为具名子模块，匹配训练代码的 checkpoint 命名"""
    def __init__(self):
        super().__init__()
        cnn = models.mobilenet_v3_small(weights=None)
        self.features = cnn.features
        self.avgpool = cnn.avgpool       # AdaptiveAvgPool2d((1,1))

    def forward(self, x):
        x = self.features(x)
        x = self.avgpool(x)
        x = torch.flatten(x, 1)
        return x


class VideoEncoder(nn.Module):
    """MobileNetV3-Small + BiGRU 时空编码器"""

    def __init__(self, cnn_out_dim=576, gru_hidden=256, gru_layers=2):
        super().__init__()
        # ---- 帧级 CNN 特征提取 (frozen) ----
        self._cnn = CNNBackbone()

        # ---- 时序编码 ----
        self._gru_norm = nn.LayerNorm(cnn_out_dim)
        self._gru = nn.GRU(
            cnn_out_dim, gru_hidden,
            num_layers=gru_layers,
            bidirectional=True,
            batch_first=True,
        )
        self._proj = nn.Linear(gru_hidden * 2, gru_hidden * 2)

    def forward(self, x):
        """
        Args:
            x: (B, T, C, H, W)
        Returns:
            (B, 512)  pooled + projected feature
        """
        B, T, C, H, W = x.shape
        # per-frame CNN
        x = x.view(B * T, C, H, W)     # (B*T, C, H, W)
        x = self._cnn(x)               # CNNBackbone: features → avgpool → flatten → (B*T, 576)
        x = x.view(B, T, -1)           # (B, T, 576)

        # temporal
        x = self._gru_norm(x)
        x, _ = self._gru(x)             # (B, T, 1024)
        x = x.mean(dim=1)               # (B, 1024)
        x = self._proj(x)               # (B, 512)
        return x


class FallDetector(nn.Module):
    """完整跌倒检测模型"""

    def __init__(self):
        super().__init__()
        self.video_encoder = VideoEncoder()

        self.fusion = nn.Sequential(
            nn.Linear(512, 512),
            nn.BatchNorm1d(512),
            nn.ReLU(inplace=True),
            nn.Identity(),            # placeholder for Dropout (无参数)
            nn.Linear(512, 256),
            nn.BatchNorm1d(256),
        )

        self.classifier = nn.Linear(256, 1)

    def forward(self, x):
        """
        Args:
            x: (B, T, C, H, W)
        Returns:
            logits (B, 1) — apply torch.sigmoid to get probability
        """
        feat = self.video_encoder(x)       # (B, 512)
        feat = self.fusion(feat)           # (B, 256)
        return self.classifier(feat)       # (B, 1)


# === 加载和使用 ==============================================================

_fall_detector = None
_device = None


def _get_device():
    global _device
    if _device is None:
        if torch.cuda.is_available():
            _device = torch.device('cuda')
        elif torch.backends.mps.is_available():
            # MPS 在 Flask 多线程环境下可能冲突，默认用 CPU
            _device = torch.device('cpu')
            logger.info('MPS available but using CPU to avoid multi-thread conflicts')
        else:
            _device = torch.device('cpu')
    return _device


def load_model(model_path=None):
    """加载学生模型，返回 (model, device)"""
    global _fall_detector
    if _fall_detector is not None:
        return _fall_detector, _get_device()

    path = model_path or MODEL_PATH
    device = _get_device()
    logger.info(f'Loading fall detector from {path} on {device}...')

    ckpt = torch.load(path, map_location='cpu', weights_only=False)
    model = FallDetector()
    model.load_state_dict(ckpt['model_state_dict'], strict=False)
    model.to(device)
    model.eval()

    _fall_detector = model
    logger.info(f'Fall detector loaded — F1={ckpt.get("best_f1", "?")}')
    return model, device


def predict(video_frames: torch.Tensor) -> float:
    """
    对一段视频帧进行跌倒预测。

    Args:
        video_frames: shape (1, 32, 3, 224, 224) 的归一化帧
                      值域 [0, 1]，由 ImageNet 标准预处理

    Returns:
        P(fall) ∈ [0, 1]
    """
    model, device = load_model()
    with torch.no_grad():
        logits = model(video_frames.to(device))
        return torch.sigmoid(logits).item()


def predict_batch(video_frames: torch.Tensor) -> list:
    """
    批量预测。

    Args:
        video_frames: (B, 32, 3, 224, 224)

    Returns:
        list[float] 概率列表
    """
    model, device = load_model()
    with torch.no_grad():
        logits = model(video_frames.to(device))
        probs = torch.sigmoid(logits).squeeze(-1)
        return probs.cpu().tolist()


# === 特征缓存优化：CNN 特征提取 ==============================================

def extract_cnn_features(frame_tensor: torch.Tensor) -> torch.Tensor:
    """
    提取单帧的 CNN 特征（用于缓存）。

    Args:
        frame_tensor: (1, 3, 224, 224) 已预处理的帧

    Returns:
        (1, 576) CNN 特征向量（在 model device 上）
    """
    model, device = load_model()
    with torch.no_grad():
        x = frame_tensor.to(device).unsqueeze(1)  # (1, 1, 3, 224, 224)
        x = x.squeeze(1)                           # (1, 3, 224, 224)
        x = x.unsqueeze(0)                         # (1, 1, 3, 224, 224)
        x = model.video_encoder._cnn(x.squeeze(1))  # CNN: (1, 576)
        return x                                   # kept on device


def classify_from_features(feature_stack: torch.Tensor) -> float:
    """
    从缓存的 32 个 CNN 特征直接分类（跳过 CNN 前向）。

    Args:
        feature_stack: (1, 32, 576) 在 device 上的 CNN 特征

    Returns:
        P(fall) ∈ [0, 1]
    """
    model, device = load_model()
    with torch.no_grad():
        x = model.video_encoder._gru_norm(feature_stack)
        x, _ = model.video_encoder._gru(x)
        x = x.mean(dim=1)
        x = model.video_encoder._proj(x)
        x = model.fusion(x)
        logits = model.classifier(x)
        return torch.sigmoid(logits).item()
