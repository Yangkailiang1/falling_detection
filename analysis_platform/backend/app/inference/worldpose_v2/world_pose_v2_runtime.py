"""World-Pose v2.2 AV residual — live RTSP runtime.

Port of ``Fall_detect_wordmodel-main/runtime/run_world_pose_av_v4.py`` inference
block into a stateless per-call predictor that ``world_av_pipeline`` drives.

Key contract notes:
- ``extract_token_bundle`` (NOT ``extract_tokens``) returns
  ``world_tokens`` / ``current_temporal_tokens`` / ``future_temporal_tokens``,
  which map to ``WorldPoseFutureConfig.current_world_dim=1024`` and
  ``future_world_dim=1664``.
- No internal EMA here — the platform ``TemporalFallSmoother`` owns persistence.
- The v2.2 model consumes YOLO-Pose skeleton + person crop internally; the
  platform Stage-3 ``SerialPoseMedicalAnalyzer`` is a separate post-alarm stage.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import torch

# 让 `surveillance.*` 绝对导入解析到本子包（worldpose_v2/surveillance/）
_SURV = Path(__file__).resolve().parent
if str(_SURV) not in sys.path:
    sys.path.insert(0, str(_SURV))

from surveillance.audio_fall_detector import AudioFallConfig, waveform_to_mel  # noqa: E402
from surveillance.vjepa2_world_visual_encoder import (  # noqa: E402
    VJEPA2WorldVisualConfig,
    VJEPA2WorldVisualEncoder,
)
from surveillance.world_pose_audio_residual_fusion import WorldPoseAudioResidualFusion  # noqa: E402
from surveillance.world_pose_future_fusion import WorldPoseFutureConfig  # noqa: E402


SKELETON = ((5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12),
            (11, 13), (13, 15), (12, 14), (14, 16), (0, 1), (0, 2), (1, 3), (2, 4))


@dataclass
class WorldPoseV2RuntimeConfig:
    ckpt_path: str = ""                 # 默认从 inference_config.WEIGHTS["worldpose_v2"]
    token_adapter_path: str = ""        # WEIGHTS["world_token_adapter"]
    vjepa_checkpoint: str = ""          # WEIGHTS["vjepa2_vitl"]
    vjepa_repo: str = ""                # VJEPA2_REPO_PATH (third_party/vjepa2)
    pose_model_path: str = ""           # WEIGHTS["yolo11n_pose"]
    device: str = "cuda" if torch.cuda.is_available() else "cpu"
    pose_device: str = ""
    sample_fps: float = 16.0            # 训练采样契约
    inference_hz: float = 3.0
    capture_fps: float = 10.0           # analysis_platform 的 10fps 采样
    threshold: float = 0.0              # 0 → 用 ckpt["decision_threshold"] (0.7444)
    audio_sample_rate: int = 22050
    audio_duration: float = 3.0
    context_frames: int = 32
    future_frames: int = 16
    image_size: int = 384


def _pose_for_frames(pose_model, frames, pose_device):
    """YOLO11n-Pose: (pose[17,3], box[6]) per frame — 与 CLI pose_for_frames 一致."""
    results = pose_model.predict(list(frames), imgsz=640, device=pose_device, verbose=False, stream=False)
    pose = np.zeros((len(frames), 17, 3), dtype=np.float32)
    boxes = np.zeros((len(frames), 6), dtype=np.float32)
    for i, (frame, result) in enumerate(zip(frames, results)):
        if result.keypoints is None or result.boxes is None or len(result.boxes) == 0:
            continue
        h, w = frame.shape[:2]
        xyxy = result.boxes.xyxy.detach().cpu().numpy()
        conf = result.boxes.conf.detach().cpu().numpy()
        area = np.maximum(0, xyxy[:, 2] - xyxy[:, 0]) * np.maximum(0, xyxy[:, 3] - xyxy[:, 1])
        j = int(np.argmax(conf * np.sqrt(np.maximum(area, 1.0))))
        xy = result.keypoints.xyn[j].detach().cpu().numpy().astype(np.float32)
        kc = result.keypoints.conf[j].detach().cpu().numpy().astype(np.float32)
        n = min(17, len(xy))
        pose[i, :n, :2] = np.clip(xy[:n, :2], 0, 1)
        pose[i, :n, 2] = np.clip(kc[:n], 0, 1)
        x1, y1, x2, y2 = xyxy[j]
        visible = np.clip(min(x2, w) - max(x1, 0), 0, w) * np.clip(min(y2, h) - max(y1, 0), 0, h)
        boxes[i] = [(x1 + x2) / (2 * w), (y1 + y2) / (2 * h), (x2 - x1) / w, (y2 - y1) / h,
                    conf[j], visible / max((x2 - x1) * (y2 - y1), 1)]
    return pose, boxes


def _person_crop_frames(frames, boxes, scale=1.35):
    """v4 训练用的人像居中 crop（person_crop_frames，scale=1.35）。"""
    crops = []
    previous = None
    for frame, box in zip(frames, np.asarray(boxes, dtype=np.float32)):
        h, w = frame.shape[:2]
        valid = (box.shape[0] >= 6 and box[4] >= 0.12 and box[5] >= 0.20
                 and box[2] >= 0.025 and box[3] >= 0.025)
        current = box[:4].copy() if valid else previous
        if current is None:
            current = np.asarray((0.5, 0.5, 1.0, 1.0), dtype=np.float32)
        if valid:
            previous = current.copy()
        cx, cy, bw, bh = [float(v) for v in current]
        half_w = max(bw * scale * 0.5, 0.12)
        half_h = max(bh * scale * 0.5, 0.12)
        x1 = int(round(np.clip(cx - half_w, 0.0, 1.0) * w))
        y1 = int(round(np.clip(cy - half_h, 0.0, 1.0) * h))
        x2 = int(round(np.clip(cx + half_w, 0.0, 1.0) * w))
        y2 = int(round(np.clip(cy + half_h, 0.0, 1.0) * h))
        crop = frame[max(0, y1):max(y1 + 2, y2), max(0, x1):max(x1 + 2, x2)]
        crops.append(crop if crop.size else frame)
    return crops


def _pose_motion_risk(pose, boxes):
    """CLI pose_motion_risk — 跌倒运动代理分数."""
    try:
        # pose: (T,17,3) 归一化; 用髋部(11,12)中心的速度 + 躯干倾角
        if pose.shape[0] < 2:
            return 0.0
        hip = (pose[:, 11, :2] + pose[:, 12, :2]) / 2.0
        velocity = np.linalg.norm(np.diff(hip, axis=0), axis=1).max()
        shoulder = (pose[:, 5, :2] + pose[:, 6, :2]) / 2.0
        torso = shoulder - hip
        tilt = np.abs(torso[:, 0]) / np.maximum(np.linalg.norm(torso, axis=1), 1e-6)
        return float(min(1.0, 0.5 * velocity + tilt.mean()))
    except Exception:
        return 0.0


class WorldPoseV2Runtime:
    """V2.2 AV 残差融合模型的实时预测器（无状态 per-call）。"""

    def __init__(self, config: WorldPoseV2RuntimeConfig | None = None):
        self.config = config or WorldPoseV2RuntimeConfig()

        # ---- 默认路径来自 inference_config ----
        if not self.config.ckpt_path:
            from app.inference.inference_config import WEIGHTS, VJEPA2_REPO_PATH
            self.config.ckpt_path = WEIGHTS["worldpose_v2"]
            self.config.token_adapter_path = WEIGHTS["world_token_adapter"]
            self.config.vjepa_checkpoint = WEIGHTS["vjepa2_vitl"]
            self.config.vjepa_repo = VJEPA2_REPO_PATH
            self.config.pose_model_path = WEIGHTS["yolo11n_pose"]
            self.config.capture_fps = 1.0 / 0.1  # FRAME_INTERVAL

        ckpt = torch.load(self.config.ckpt_path, map_location="cpu", weights_only=False)
        visual_cfg = WorldPoseFutureConfig(**ckpt["visual_config"])
        self.model = WorldPoseAudioResidualFusion(visual_cfg, freeze_visual=True, freeze_audio=True)
        self.model = self.model.to(self.config.device).eval()
        self.model.load_state_dict(ckpt["model_state_dict"], strict=True)

        vjepa_cfg = VJEPA2WorldVisualConfig(
            repo_path=self.config.vjepa_repo,
            checkpoint_path=self.config.vjepa_checkpoint,
            token_adapter_path=self.config.token_adapter_path,
            require_token_adapter=True,
            context_frames=visual_cfg.context_frames,
            future_frames=visual_cfg.future_frames,
            image_size=self.config.image_size,
        )
        self.encoder = VJEPA2WorldVisualEncoder(vjepa_cfg, load_weights=True).to(self.config.device).eval()

        from ultralytics import YOLO
        self.pose_model = YOLO(self.config.pose_model_path)
        self.pose_device = self.config.pose_device or self.config.device

        self.threshold = (
            float(ckpt.get("decision_threshold", 0.7444))
            if self.config.threshold == 0
            else float(self.config.threshold)
        )
        self.context_seconds = (visual_cfg.context_frames - 1) / self.config.sample_fps
        self.required_history = max(2, int(round(self.context_seconds * self.config.capture_fps)) + 1)

    @torch.no_grad()
    def predict(self, frames: list[np.ndarray], waveform=None, sample_rate=None,
                small_frames: list[np.ndarray] | None = None) -> dict:
        """对因果窗口推理，返回与 ``_on_fall_alert`` 兼容的结果 dict。

        Args:
            frames: 原始 BGR 帧（> required_history 张，内部只取末窗口并重采样到 32）。
            waveform: 过去 3s 原始 PCM float32；None 表示无音频（门控强制关闭）。
            sample_rate: 音频采样率。
            small_frames: [2026-08-13] 与 frames 对齐的预缩放 image_size² BGR 帧。
                传入时整帧编码直接用它（跳过每推理 32× 720p→384 resize 的 CPU 瓶颈，
                管线内 cv2.resize 结果 bit-identical）；缺省走原 resize 路径（向后兼容）。
        """
        raw_history = list(frames)[-self.required_history:]
        idx = np.linspace(0, len(raw_history) - 1, self.config.context_frames).round().astype(int)
        sampled = [raw_history[i] for i in idx]

        pose, box = _pose_for_frames(self.pose_model, sampled, self.pose_device)

        if small_frames is not None:
            small_history = list(small_frames)[-self.required_history:]
            small_sampled = [small_history[i] for i in idx]
            rgb = [cv2.cvtColor(x, cv2.COLOR_BGR2RGB) for x in small_sampled]
        else:
            rgb = [cv2.cvtColor(cv2.resize(x, (self.config.image_size, self.config.image_size)), cv2.COLOR_BGR2RGB)
                   for x in sampled]
        image = torch.from_numpy(np.asarray(rgb, dtype=np.uint8)).permute(0, 3, 1, 2).unsqueeze(0).to(self.config.device)
        bundle = self.encoder.extract_token_bundle(image)

        crops = _person_crop_frames(sampled, box)
        crop_rgb = [cv2.cvtColor(cv2.resize(x, (self.config.image_size, self.config.image_size)), cv2.COLOR_BGR2RGB)
                    for x in crops]
        crop_image = torch.from_numpy(np.asarray(crop_rgb, dtype=np.uint8)).permute(0, 3, 1, 2).unsqueeze(0).to(self.config.device)
        crop_bundle = self.encoder.extract_token_bundle(crop_image)

        mel, valid = self._audio_mel(waveform, sample_rate)
        audio = torch.from_numpy(mel).unsqueeze(0).to(self.config.device)
        valid_t = torch.tensor([float(valid)], device=self.config.device)
        pt = torch.from_numpy(pose).unsqueeze(0).to(self.config.device)
        bt = torch.from_numpy(box).unsqueeze(0).to(self.config.device)

        out = self.model(
            bundle["world_tokens"], bundle["current_temporal_tokens"], bundle["future_temporal_tokens"],
            pt, bt, None, None, audio, valid_t, None, None, None, None, crop_bundle["world_tokens"],
        )

        p_final = float(out["final_probs"][0, 1])
        p_visual = float(torch.sigmoid(out["visual_logit"])[0])
        gate = float(out["utility_gate"][0])
        return {
            "final_probability": p_final,
            "visual_probability": p_visual,
            "av_probability": p_final,
            "gate": gate,
            "audio_available": bool(valid),
            # 真实 COCO-17 骨骼序列 [32,17,3]（归一化 xy + 置信度），供事件持久化 → 算法平台 [2026-08-13]
            "skeleton_pose": pose,
            "world_probability": float(out["world_logits"].softmax(1)[0, 1]),
            "world_base_probability": float(out["world_base_logits"].softmax(1)[0, 1]),
            "p_candidate": float(out["candidate_logits"].softmax(1)[0, 1]),
            "p_warning": float(out.get("warning_probs", out["world_logits"].softmax(1))[0, 1]),
            "domain_reliability": float(out.get("domain_reliability", torch.ones(1, device=out["world_logits"].device))[0]),
            "pose_probability": float(out["pose_logits"].softmax(1)[0, 1]),
            "pose_reliability": float(out["pose_reliability"][0]),
            "pose_rescue": float(out.get("pose_rescue", torch.zeros(1, device=out["pose_logits"].device))[0]),
            "pose_rescue_effect": float(out.get("pose_rescue_effect", torch.zeros(1, device=out["pose_logits"].device))[0]),
            "pose_quality": tuple(float(x) for x in out["pose_quality"][0].detach().cpu().tolist()),
            "crop_gate": float(out["crop_gate"][0]) if out.get("crop_gate") is not None else 0.0,
            "motion_risk": _pose_motion_risk(pose, box),
            "threshold": self.threshold,
            "_inference_ms": 0.0,  # 调用方填充
        }

    def _audio_mel(self, waveform, sample_rate):
        """3s 音频窗口 → [1,128,~126] 归一化 mel；无音频返回全零 + valid=False。"""
        if waveform is None or sample_rate is None or len(np.asarray(waveform)) == 0:
            return np.zeros((1, 128, 130), dtype=np.float32), False
        sr = int(sample_rate)
        target = int(round(self.config.audio_duration * sr))
        clip = np.asarray(waveform, dtype=np.float32)[-target:]
        if clip.size < target:
            clip = np.pad(clip, (0, target - clip.size))
        mel = waveform_to_mel(clip, sr, AudioFallConfig(sample_rate=sr, target_duration=self.config.audio_duration))
        return mel, True
