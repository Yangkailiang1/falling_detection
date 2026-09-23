"""常驻 Spatial v5 + Temporal Residual 推理适配层。

该模块复用发布包的正式 streaming runner，只把它包装成现有 Flask
检测管线所需的 ``predict(frames, audio)`` 接口。模型和 YOLO 只在首次
初始化时加载，后续窗口复用同一实例。
"""
from __future__ import annotations

import sys
import threading
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from app.inference.inference_config import FALLING_ROOT


class SpatialV5Runtime:
    """Spatial v5 deployment package 的进程内运行时。"""

    def __init__(self, device: str | None = None):
        import torch

        self.torch = torch
        workspace = Path(FALLING_ROOT)
        self.deployment_root = workspace / "Fall_detect_wordmodel-main" / "spatial_v5_deployment"
        self.deployment_dir = self.deployment_root / "deployment_world_pose_av_v3"
        self.script_dir = self.deployment_root / "scripts"
        if not self.script_dir.is_dir():
            raise FileNotFoundError(f"Spatial v5 package not found: {self.script_dir}")
        for path in (self.deployment_root, self.deployment_dir, self.script_dir):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))

        import run_arch_jepa_spatial_streaming as spatial_runner
        self.runner = spatial_runner
        self.streaming = spatial_runner.streaming
        self.base = self.streaming.base
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        args = SimpleNamespace(
            model_path=str(self.deployment_dir / "models" / "v5_temporal_residual_full_v1.pth"),
            device=self.device,
        )
        (self.checkpoint_path, self.checkpoint, _warning_path, _warning_checkpoint,
         self.visual_config, self.model, self.encoder) = spatial_runner.load_spatial_models(args)

        from ultralytics import YOLO
        self.pose_model = YOLO(str(self.deployment_dir / "models" / "yolo11n-pose.pt"))
        self.pose_device = self.device
        self.threshold = float(self.checkpoint.get("decision_threshold", 0.669911))
        self.warning_threshold = float(self.checkpoint.get("warning_threshold", 0.7160))
        self.context_frames = int(self.visual_config.context_frames)
        self.sample_fps = 16.0
        self.history: deque = deque(maxlen=3)
        self.long_tokens = None
        self.long_valid = None
        self._warm = False
        self._lock = threading.Lock()
        # 音频梅尔特征提取在当前环境约需 2~3 秒，不能放在 4 Hz 模型
        # worker 的临界路径里。后台只保留最新窗口，推理复用最近一次特征。
        self._audio_lock = threading.Lock()
        self._audio_mel = None
        self._audio_refreshing = False
        self._audio_generation = 0
        self._audio_applied_generation = 0
        self._audio_last_submit = 0.0

    def warmup(self) -> None:
        """用无事件的占位窗口预热 CUDA/算子；只执行一次。"""
        if self._warm:
            return
        with self._lock:
            if self._warm:
                return
            frames = [np.zeros((384, 384, 3), dtype=np.uint8) for _ in range(self.context_frames)]
            pose = np.zeros((self.context_frames, 17, 3), dtype=np.float32)
            boxes = np.zeros((self.context_frames, 6), dtype=np.float32)
            boxes[:, :4] = (0.5, 0.5, 0.5, 0.8)
            try:
                self._predict_window(frames, pose, boxes, None)
            except Exception:
                # 预热失败不吞掉真正推理错误；下一次 predict 会重试并记录日志。
                pass
            self._warm = True

    def _predict_window(self, frames, pose, boxes, audio):
        torch = self.torch
        full = self.streaming.frames_to_tensor(frames, self.device)
        crop_frames = self.base.person_crop_frames(frames, boxes)
        crop = self.streaming.frames_to_tensor(crop_frames, self.device)
        with torch.inference_mode():
            combined = self.encoder.extract_token_bundle(torch.cat((full, crop), dim=0))
            bundle = self.streaming.split_bundle(combined, 0)
            crop_bundle = self.streaming.split_bundle(combined, 1)
            pose_tensor = torch.from_numpy(pose).unsqueeze(0).to(self.device)
            box_tensor = torch.from_numpy(boxes).unsqueeze(0).to(self.device)
            if hasattr(self.encoder, "refine_token_bundle"):
                bundle = self.encoder.refine_token_bundle(bundle, pose_tensor, box_tensor)
            context_tokens, context_valid = self.streaming.build_history(
                self.history, bundle["world_tokens"].detach(), 3, self.device
            )
            if audio is not None:
                self.submit_audio(audio)
            with self._audio_lock:
                mel = None if self._audio_mel is None else self._audio_mel.copy()
            audio_ready = mel is not None
            if mel is None:
                mel = np.zeros((128, 130), dtype=np.float32)
            audio_valid = bool(audio is not None and audio_ready)
            audio_tensor = torch.from_numpy(mel).unsqueeze(0).to(self.device)
            audio_valid_tensor = torch.tensor([float(audio_valid)], dtype=torch.float32, device=self.device)
            outputs = self.model(
                bundle["world_tokens"], bundle["current_temporal_tokens"],
                bundle["future_temporal_tokens"], pose_tensor, box_tensor,
                None, None, audio_tensor, audio_valid_tensor,
                context_tokens, context_valid, self.long_tokens, self.long_valid,
                crop_bundle["world_tokens"],
            )
        self.history.append(bundle["world_tokens"].detach())
        p_final = float(outputs["final_probs"][0, 1])
        p_visual = float(torch.sigmoid(outputs["visual_logit"])[0])
        p_candidate = float(outputs["candidate_logits"].softmax(1)[0, 1])
        p_world = float(outputs["world_logits"].softmax(1)[0, 1])
        p_pose = float(outputs["pose_logits"].softmax(1)[0, 1])
        p_future = float(
            outputs["future_pose_led_logits"].softmax(1)[0, 1]
        ) if outputs.get("future_pose_led_logits") is not None else 0.0
        future_pose = outputs["pose_future_coordinates"][0].detach().cpu().numpy()
        future_visibility = torch.sigmoid(outputs["pose_future_visibility_logits"][0]).detach().cpu().numpy()
        return {
            "final_probability": p_final,
            "visual_probability": p_visual,
            "av_probability": p_final,
            "candidate_probability": p_candidate,
            "world_probability": p_world,
            "pose_probability": p_pose,
            "future_warning_probability": p_future,
            "warning": bool(p_future >= self.warning_threshold),
            "gate": float(outputs["utility_gate"][0]),
            "audio_available": bool(audio_valid),
            "pose_reliability": float(outputs["pose_reliability"][0]),
            "pose_quality": tuple(float(x) for x in outputs["pose_quality"][0].detach().cpu().tolist()),
            "skeleton_pose": pose.copy(),
            "future_pose": future_pose,
            "future_visibility": future_visibility,
            "threshold": self.threshold,
            "warning_threshold": self.warning_threshold,
            "status": "ok (spatial_v5)",
        }

    def submit_audio(self, audio) -> None:
        """Queue the newest waveform for background mel extraction.

        Only one extractor is active at a time.  The video/model worker never
        waits for this CPU-heavy operation; until the first result is ready it
        uses a zero mel with ``audio_valid=False`` and then seamlessly adopts
        the latest completed feature on the next window.
        """
        if audio is None:
            return
        waveform, sample_rate = audio
        waveform = np.asarray(waveform, dtype=np.float32).copy()
        with self._audio_lock:
            self._audio_generation += 1
            generation = self._audio_generation
            self._audio_pending = (waveform, int(sample_rate), generation)
            now = time.monotonic()
            if self._audio_refreshing or now - self._audio_last_submit < 1.0:
                return
            self._audio_refreshing = True
            self._audio_last_submit = now
        threading.Thread(target=self._refresh_audio_mel, daemon=True,
                         name="spatial-v5-audio-mel").start()

    def _refresh_audio_mel(self) -> None:
        try:
            with self._audio_lock:
                pending = getattr(self, "_audio_pending", None)
            if pending is None:
                return
            waveform, sample_rate, generation = pending
            mel = self.base.waveform_to_mel(
                waveform, int(sample_rate),
                self.base.AudioFallConfig(sample_rate=int(sample_rate), target_duration=3.0),
            )
            mel = np.asarray(mel, dtype=np.float32)
            if mel.ndim == 3 and mel.shape[0] == 1:
                mel = mel[0]
            with self._audio_lock:
                self._audio_mel = mel
                self._audio_applied_generation = generation
        except Exception:
            pass
        finally:
            with self._audio_lock:
                self._audio_refreshing = False

    def predict(self, frames, audio=None) -> dict:
        """对最近窗口做一次 Spatial v5 推理。"""
        if len(frames) < 2:
            return {}
        items = list(frames)
        positions = np.linspace(0, len(items) - 1, self.context_frames).round().astype(np.int64)
        sampled = [items[int(i)] for i in positions]
        pose, boxes = self.base.pose_for_frames(self.pose_model, sampled, self.pose_device)
        return self._predict_window(sampled, pose, boxes, audio)

    def pose_for_frame(self, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Run the packaged Pose model once for one decoded frame.

        The streaming adapter uses this method to build an incremental pose
        ring buffer.  Keeping the Pose call outside ``predict`` prevents every
        overlapping 32-frame detector window from recomputing the same poses.
        """
        pose, boxes = self.base.pose_for_frames(self.pose_model, [frame], self.pose_device)
        return pose[0], boxes[0]

    def predict_from_pose(
        self,
        frames,
        pose: np.ndarray,
        boxes: np.ndarray,
        audio=None,
    ) -> dict:
        """Run the v5 model with a pose sequence supplied by the stream cache."""
        if len(frames) < 2:
            return {}
        items = list(frames)
        positions = np.linspace(0, len(items) - 1, self.context_frames).round().astype(np.int64)
        sampled = [items[int(i)] for i in positions]
        pose_arr = np.asarray(pose, dtype=np.float32)
        box_arr = np.asarray(boxes, dtype=np.float32)
        if pose_arr.ndim == 2:
            pose_arr = pose_arr[None, ...]
        if box_arr.ndim == 1:
            box_arr = box_arr[None, ...]
        pose_idx = np.clip(positions, 0, max(len(pose_arr) - 1, 0))
        box_idx = np.clip(positions, 0, max(len(box_arr) - 1, 0))
        sampled_pose = pose_arr[pose_idx]
        sampled_boxes = box_arr[box_idx]
        return self._predict_window(sampled, sampled_pose, sampled_boxes, audio)


_runtime: SpatialV5Runtime | None = None
_lock = threading.Lock()


def get_runtime() -> SpatialV5Runtime:
    global _runtime
    if _runtime is None:
        with _lock:
            if _runtime is None:
                _runtime = SpatialV5Runtime()
    return _runtime


