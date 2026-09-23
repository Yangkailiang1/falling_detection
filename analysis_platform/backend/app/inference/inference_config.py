"""
推理管线统一配置 — 所有路径/阈值/URL 集中管理

使用方式:
    from app.inference.inference_config import WEIGHTS, FALL_THRESHOLD, RTSP_URL
"""

import os
from pathlib import Path

# ============================================================================
# 基础目录 (全部以本文件位置为基准推算，消除硬编码绝对路径)
# ============================================================================
# 本文件: .../analysis_platform/backend/app/inference/inference_config.py
INFERENCE_DIR = Path(__file__).resolve().parent                    # .../inference/
MODELS_DIR = Path(os.getenv("INFERENCE_MODELS_DIR", str(INFERENCE_DIR / "models")))
FALLING_ROOT = INFERENCE_DIR.parent.parent.parent.parent           # .../falling/
# 录制版位于 workspace/video_recording_version 下；新模型包保留在
# workspace/Fall_detect_wordmodel-main，避免重复复制 258MB 主权重。
WORKSPACE_ROOT = FALLING_ROOT
SPATIAL_V5_ROOT = Path(os.getenv(
    "SPATIAL_V5_ROOT",
    str(WORKSPACE_ROOT / "Fall_detect_wordmodel-main" / "spatial_v5_deployment"),
))

# ============================================================================
# 模型权重
# ============================================================================
WEIGHTS = {
    "vjepa2_vitl":       str(MODELS_DIR / "vjepa2_1_vitl_dist_vitG_384.pt"),
    "world_av_fusion":   str(MODELS_DIR / "av_rawaug_longview_gmd_ofsyn_omnifall.pth"),
    "yolo11n":           str(MODELS_DIR / "yolo11n.pt"),
    "yolo11n_pose":      str(MODELS_DIR / "yolo11n-pose.pt"),
    "student_best":      str(MODELS_DIR / "student_best.pt"),
    "audio_cnn":         str(MODELS_DIR / "audio_fall_cnn.pth"),
    "audio_transformer": str(MODELS_DIR / "audio_fall_transformer.pth"),
    # World-Pose v2.2 AV residual (同事 2026-08-13 更新)
    "worldpose_v2":       str(MODELS_DIR / "world_pose_audio_residual_v2_2_gmd_full.pth"),
    "world_token_adapter": str(MODELS_DIR / "world_token_adapter_v1.pth"),
}

# ============================================================================
# 外部依赖仓库
# ============================================================================
VJEPA2_REPO_PATH = os.getenv(
    "VJEPA2_REPO_PATH",
    str(FALLING_ROOT / "third_party" / "vjepa2"),
)

# ============================================================================
# V-JEPA2 视觉编码器参数
# ============================================================================
VJEPA2_IMAGE_SIZE = 384
VJEPA2_CONTEXT_FRAMES = 32
VJEPA2_FUTURE_FRAMES = 16
VJEPA2_HISTORY_SIZE = 3
VJEPA2_USE_FUTURE_PREDICTION = True
VJEPA2_VARIANT = "vjepa2_1_vit_large_384"
VJEPA2_FREEZE_ENCODER = True
VJEPA2_FREEZE_PREDICTOR = True
VJEPA2_USE_FP16 = True

# ============================================================================
# World-Pose v2.2 AV residual 参数 (同事 2026-08-13 更新, mode='worldpose')
# ============================================================================
WORDPOSE_V2_SAMPLE_FPS = 16.0                          # 训练采样契约
WORDPOSE_V2_INFERENCE_HZ = float(os.getenv("WORDPOSE_INFERENCE_HZ", "3.0"))
WORDPOSE_V2_CAPTURE_FPS = 10.0                         # 与下方 FRAME_INTERVAL=1/10 一致
WORDPOSE_V2_THRESHOLD = float(os.getenv("WORDPOSE_THRESHOLD", "0.0"))   # 0 → ckpt decision_threshold
SPATIAL_V5_THRESHOLD = float(os.getenv("SPATIAL_V5_THRESHOLD", "0.669911"))
WORDPOSE_V2_MIN_CONSECUTIVE = int(os.getenv("WORDPOSE_MIN_CONSECUTIVE", "2"))
WORDPOSE_V2_EMA_ALPHA = 0.45
WORDPOSE_V2_IMAGE_SIZE = 384                            # V-JEPA2 编码输入尺寸; 管线预缩放缓冲用
# 模型窗口 = (context_frames-1)/sample_fps 秒; 实时以 capture_fps 采样 → 取最近多少原始帧
WORDPOSE_V2_REQUIRED_HISTORY = max(
    2,
    int(round((VJEPA2_CONTEXT_FRAMES - 1) / WORDPOSE_V2_SAMPLE_FPS * WORDPOSE_V2_CAPTURE_FPS)) + 1,
)

# ============================================================================
# 跌倒检测参数
# ============================================================================
FALL_THRESHOLD = 0.76
EMA_ALPHA = 0.35
MIN_CONSECUTIVE_HITS = 3
COOLDOWN_FRAMES = 30
DECAY_ON_MISSING = 0.90

# ============================================================================
# 音频参数
# ============================================================================
AUDIO_SAMPLE_RATE = 22050
AUDIO_DURATION = 3.0

# ============================================================================
# 姿态分析参数
# ============================================================================
POSE_LOOKBACK_FRAMES = 75
POSE_POST_FRAMES = 45
POSE_CONFIDENCE_THRESHOLD = 0.30
POSE_FPS = 25.0

# ============================================================================
# 摄像头 (必须通过环境变量 RTSP_URL 配置, 含密码 — 不硬编码在代码)
# ============================================================================
# .env: RTSP_URL=rtsp://admin:密码@CAMERA_IP:554/h264/ch1/sub/av_stream
RTSP_URL = os.getenv(
    "RTSP_URL",
    "rtsp://admin:***@CAMERA_IP:554/h264/ch1/sub/av_stream",  # 占位, 必须覆盖
)
FRAME_INTERVAL = 1.0 / 10  # 10fps 采样

# ============================================================================
# 设备与联系人 (可通过 .env 覆盖)
# ============================================================================
DEVICE_SERIAL = os.getenv("DEVICE_SERIAL", "CHANGE_ME_DEVICE_SERIAL")   # camera model used in development
TEST_PHONE = os.getenv("TEST_PHONE", "CHANGE_ME_PHONE")       # 通知测试号码

# ============================================================================
# 风险等级映射 (SerialPoseMedicalAnalyzer → V5 事件系统)
# ============================================================================
RISK_LEVEL_MAP = {
    "critical": "I",
    "high": "II",
    "medium": "III",
}
