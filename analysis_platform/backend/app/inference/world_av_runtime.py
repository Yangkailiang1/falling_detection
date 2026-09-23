"""Runtime for the accepted V-JEPA2/audio gated fall detector.

The world model is the visual encoder used by the trained AV detector.  Pose
is deliberately absent here: it is a serial, post-alarm medical-screening
stage owned by ``test_world_video_serial_medical.py``.
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass

from typing import Iterable

import cv2
import numpy as np
import torch

from app.inference.audio_fall_detector import AudioFallConfig, waveform_to_mel
from app.inference.audio_fall_transformer import AudioTransformerConfig
from app.inference.av_utility_gated_world_fusion import (
    AudioVisualUtilityGatedWorldFusion,
    UtilityGatedWorldConfig,
)
from app.inference.inference_config import (
    WEIGHTS, VJEPA2_REPO_PATH, VJEPA2_IMAGE_SIZE, VJEPA2_CONTEXT_FRAMES,
    VJEPA2_FUTURE_FRAMES, VJEPA2_HISTORY_SIZE, VJEPA2_USE_FUTURE_PREDICTION,
    AUDIO_SAMPLE_RATE, AUDIO_DURATION,
)
from app.inference.vjepa2_world_visual_encoder import (
    VJEPA2WorldVisualConfig,
    VJEPA2WorldVisualEncoder,
)


@dataclass
class WorldAVRuntimeConfig:
    model_path: str = ""
    vjepa_checkpoint: str = ""
    vjepa_repo: str = ""
    device: str = "cuda" if torch.cuda.is_available() else "cpu"
    image_size: int = VJEPA2_IMAGE_SIZE
    context_frames: int = VJEPA2_CONTEXT_FRAMES
    future_frames: int = VJEPA2_FUTURE_FRAMES
    history_size: int = VJEPA2_HISTORY_SIZE
    audio_sample_rate: int = AUDIO_SAMPLE_RATE
    audio_duration: float = AUDIO_DURATION
    use_future_prediction: bool = VJEPA2_USE_FUTURE_PREDICTION

    def __post_init__(self) -> None:
        if not self.model_path:
            self.model_path = WEIGHTS["world_av_fusion"]
        if not self.vjepa_checkpoint:
            self.vjepa_checkpoint = WEIGHTS["vjepa2_vitl"]
        if not self.vjepa_repo:
            self.vjepa_repo = VJEPA2_REPO_PATH


class WorldAVRuntime:
    """Streaming adapter around the trained world-token AV checkpoint."""

    def __init__(self, config: WorldAVRuntimeConfig | None = None):
        self.config = config or WorldAVRuntimeConfig()
        self.device = torch.device(self.config.device)
        if not os.path.exists(self.config.model_path):
            raise FileNotFoundError(f"AV model not found: {self.config.model_path}")
        if not os.path.exists(self.config.vjepa_checkpoint):
            raise FileNotFoundError(f"V-JEPA2 checkpoint not found: {self.config.vjepa_checkpoint}")

        visual_config = VJEPA2WorldVisualConfig(
            repo_path=self.config.vjepa_repo,
            checkpoint_path=self.config.vjepa_checkpoint,
            image_size=self.config.image_size,
            context_frames=self.config.context_frames,
            future_frames=self.config.future_frames,
            use_future_prediction=self.config.use_future_prediction,
        )
        self.visual_encoder = VJEPA2WorldVisualEncoder(visual_config, load_weights=True).to(self.device)
        self.visual_encoder.eval()
        self.model = AudioVisualUtilityGatedWorldFusion(
            AudioTransformerConfig(),
            UtilityGatedWorldConfig(cross_attention_gate=True),
            freeze_audio_backbone=True,
        ).to(self.device)
        checkpoint = torch.load(self.config.model_path, map_location=self.device, weights_only=False)
        state = checkpoint.get("model_state_dict", checkpoint) if isinstance(checkpoint, dict) else checkpoint
        compatible = {
            key: value for key, value in state.items()
            if key in self.model.state_dict() and self.model.state_dict()[key].shape == value.shape
        }
        self.model.load_state_dict(compatible, strict=False)
        self.model.eval()
        # Stores *previous* segments. The training dataset supplies previous
        # windows as context and keeps padded entries invalid, so the live
        # path mirrors that contract instead of treating the current window as
        # its own history.
        self.history: deque[torch.Tensor] = deque(maxlen=max(1, int(self.config.history_size)))
        self.last_result: dict[str, float | bool | str] = self._empty_result("initializing")

    @staticmethod
    def _empty_result(status: str) -> dict[str, float | bool | str]:
        return {
            "final_probability": 0.0,
            "visual_probability": 0.0,
            "audio_probability": 0.0,
            "av_probability": 0.0,
            "gate": 0.0,
            "cross_gate": 0.0,
            "selector_gate": 0.0,
            "match_score": 0.0,
            "impact_probability": 0.0,
            "audio_available": False,
            "status": status,
        }

    def _frames_to_tensor(self, frames: Iterable[np.ndarray]) -> torch.Tensor:
        selected = list(frames)
        if not selected:
            raise ValueError("At least one frame is required")
        rgb = []
        for frame in selected:
            image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) if frame.shape[-1] == 3 else frame
            image = cv2.resize(image, (self.config.image_size, self.config.image_size), interpolation=cv2.INTER_LINEAR)
            rgb.append(image)
        array = np.asarray(rgb, dtype=np.uint8)
        return torch.from_numpy(array).permute(0, 3, 1, 2).unsqueeze(0).to(self.device)

    def _audio_mel(self, waveform: np.ndarray | None, sample_rate: int | None) -> tuple[torch.Tensor, bool]:
        audio_config = AudioFallConfig(
            sample_rate=self.config.audio_sample_rate,
            target_duration=self.config.audio_duration,
        )
        target = int(round(self.config.audio_duration * self.config.audio_sample_rate))
        if waveform is None or sample_rate is None or len(waveform) == 0:
            # Keep the tensor contract while exposing the missing modality to
            # the learned quality/gate features through an all-zero mel.
            return torch.zeros((1, 1, audio_config.n_mels, 130), device=self.device), False
        y = np.asarray(waveform, dtype=np.float32)
        if int(sample_rate) != self.config.audio_sample_rate:
            import librosa
            y = librosa.resample(y, orig_sr=int(sample_rate), target_sr=self.config.audio_sample_rate)
        if y.size < target:
            y = np.pad(y, (0, target - y.size))
        elif y.size > target:
            y = y[:target]
        mel = waveform_to_mel(y, self.config.audio_sample_rate, audio_config)
        return torch.from_numpy(mel).unsqueeze(0).to(self.device), True

    @torch.no_grad()
    def predict(
        self,
        frames: Iterable[np.ndarray],
        waveform: np.ndarray | None = None,
        sample_rate: int | None = None,
    ) -> dict[str, float | bool | str]:
        frame_tensor = self._frames_to_tensor(frames)
        visual_tokens = self.visual_encoder.extract_tokens(frame_tensor)
        previous = list(self.history)
        padding = max(0, self.config.history_size - len(previous))
        history_items = [visual_tokens.squeeze(0).cpu()] * padding + previous[-self.config.history_size :]
        history_tokens = torch.stack(history_items, dim=0).unsqueeze(0).to(self.device)
        valid = torch.zeros((1, self.config.history_size), device=self.device)
        if previous:
            valid[0, -len(previous):] = 1.0
        audio_mel, audio_available = self._audio_mel(waveform, sample_rate)
        outputs = self.model(
            visual_tokens,
            audio_mel,
            return_dict=True,
            context_tokens=history_tokens,
            context_valid=valid,
        )
        result = {
            "final_probability": float(outputs["final_probs"][0, 1].item()),
            "visual_probability": float(torch.softmax(outputs["visual_logits"], dim=1)[0, 1].item()),
            "audio_probability": float(torch.softmax(outputs["audio_logits"], dim=1)[0, 1].item()),
            "av_probability": float(torch.softmax(outputs["av_logits"], dim=1)[0, 1].item()),
            "gate": float(outputs["gate"][0].item()),
            "cross_gate": float(outputs["cross_gate"][0].item()),
            "selector_gate": float(outputs["selector_gate"][0].item()),
            "match_score": float(outputs["match_score"][0].item()),
            "impact_probability": float(outputs["impact_probability"][0].item()),
            "audio_available": audio_available,
            "status": "ok",
        }
        self.history.append(visual_tokens.squeeze(0).cpu())
        self.last_result = result
        return result
