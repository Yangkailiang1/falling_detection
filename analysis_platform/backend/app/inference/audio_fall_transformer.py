"""
Audio Transformer branch for audio fall detection.

The model treats a Mel spectrogram as a time-frequency image, splits it into
patches, and encodes the patch sequence with a Transformer. It exposes both
classification logits and embeddings/tokens for learned audio-visual fusion.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from app.inference.audio_fall_detector import (
    AudioFallConfig,
    audio_file_to_mel,
    waveform_to_mel,
)


@dataclass
class AudioTransformerConfig(AudioFallConfig):
    patch_size: tuple[int, int] = (16, 16)
    d_model: int = 128
    nhead: int = 4
    num_encoder_layers: int = 4
    dim_feedforward: int = 256
    dropout: float = 0.15
    num_classes: int = 2
    max_time_patches: int = 64

    def __post_init__(self):
        if not self.model_path:
            self.model_path = os.path.join(
                os.path.dirname(__file__),
                "models",
                "audio_fall_transformer.pth",
            )


class AudioFallTransformer(nn.Module):
    """ViT-style Transformer for Mel spectrogram fall/non-fall classification."""

    def __init__(self, config: Optional[AudioTransformerConfig] = None):
        super().__init__()
        self.config = config or AudioTransformerConfig()
        ph, pw = self.config.patch_size
        if self.config.n_mels % ph != 0:
            raise ValueError("n_mels must be divisible by patch_size[0]")

        self.freq_patches = self.config.n_mels // ph
        self.max_patches = self.freq_patches * self.config.max_time_patches

        self.patch_embed = nn.Conv2d(
            1,
            self.config.d_model,
            kernel_size=self.config.patch_size,
            stride=self.config.patch_size,
        )
        self.cls_token = nn.Parameter(torch.zeros(1, 1, self.config.d_model))
        self.position_embedding = nn.Parameter(
            torch.randn(1, 1 + self.max_patches, self.config.d_model) * 0.02
        )
        self.dropout = nn.Dropout(self.config.dropout)

        encoder_layer = nn.TransformerEncoderLayer(
            d_model=self.config.d_model,
            nhead=self.config.nhead,
            dim_feedforward=self.config.dim_feedforward,
            dropout=self.config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.transformer_encoder = nn.TransformerEncoder(
            encoder_layer,
            num_layers=self.config.num_encoder_layers,
        )
        self.norm = nn.LayerNorm(self.config.d_model)
        self.classifier = nn.Sequential(
            nn.Linear(self.config.d_model, self.config.d_model // 2),
            nn.GELU(),
            nn.Dropout(self.config.dropout),
            nn.Linear(self.config.d_model // 2, self.config.num_classes),
        )

    def _pad_or_crop_time(self, x: torch.Tensor) -> torch.Tensor:
        """Pad/crop spectrogram time bins so patch count stays bounded."""
        _, _, _, time_bins = x.shape
        target_time = self.config.max_time_patches * self.config.patch_size[1]
        if time_bins == target_time:
            return x
        if time_bins > target_time:
            return x[:, :, :, :target_time]
        return F.pad(x, (0, target_time - time_bins, 0, 0))

    def extract_tokens(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: [B, 1, n_mels, time]

        Returns:
            tokens including CLS: [B, 1 + N, d_model]
        """
        if x.dim() == 3:
            x = x.unsqueeze(0)
        x = self._pad_or_crop_time(x)
        x = self.patch_embed(x)  # [B, d_model, Fp, Tp]
        x = x.flatten(2).transpose(1, 2)  # [B, N, d_model]

        cls = self.cls_token.expand(x.size(0), -1, -1)
        x = torch.cat([cls, x], dim=1)
        x = x + self.position_embedding[:, : x.size(1), :]
        x = self.dropout(x)
        x = self.transformer_encoder(x)
        return self.norm(x)

    def extract_embedding(self, x: torch.Tensor) -> torch.Tensor:
        tokens = self.extract_tokens(x)
        return tokens[:, 0]

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        embedding = self.extract_embedding(x)
        return self.classifier(embedding)


class AudioTransformerDetector:
    """Inference wrapper matching AudioFallDetector's public style."""

    class_names = ["NO_FALL", "FALL"]

    def __init__(
        self,
        config: Optional[AudioTransformerConfig] = None,
        device: Optional[str] = None,
        load_weights: bool = True,
    ):
        self.config = config or AudioTransformerConfig()
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self.model = AudioFallTransformer(self.config).to(self.device)
        self.model.eval()
        self.weights_loaded = False
        if load_weights:
            self.weights_loaded = self._load_weights(self.config.model_path)

    def _load_weights(self, model_path: str) -> bool:
        if not os.path.exists(model_path):
            return False
        state = torch.load(model_path, map_location=self.device)
        if isinstance(state, dict) and "model_state_dict" in state:
            state = state["model_state_dict"]
        self.model.load_state_dict(state, strict=False)
        return True

    def predict_mel(self, mel: np.ndarray) -> Tuple[str, float, np.ndarray]:
        x = torch.from_numpy(mel).float()
        if x.dim() == 3:
            x = x.unsqueeze(0)
        x = x.to(self.device)
        with torch.no_grad():
            logits = self.model(x)
            probs = F.softmax(logits, dim=-1).squeeze(0).cpu().numpy()
        idx = int(np.argmax(probs))
        return self.class_names[idx], float(probs[idx]), probs

    def predict_file(self, audio_path: str) -> Tuple[str, float, np.ndarray]:
        mel = audio_file_to_mel(audio_path, self.config)
        return self.predict_mel(mel)

    def predict_waveform(self, y: np.ndarray, sr: int) -> Tuple[str, float, np.ndarray]:
        mel = waveform_to_mel(y, sr, self.config)
        return self.predict_mel(mel)

    def fall_probability_file(self, audio_path: str) -> float:
        _, _, probs = self.predict_file(audio_path)
        return float(probs[1])

    def fall_probability_waveform(self, y: np.ndarray, sr: int) -> float:
        _, _, probs = self.predict_waveform(y, sr)
        return float(probs[1])

    def extract_embedding_mel(self, mel: np.ndarray) -> np.ndarray:
        x = torch.from_numpy(mel).float()
        if x.dim() == 3:
            x = x.unsqueeze(0)
        x = x.to(self.device)
        with torch.no_grad():
            z = self.model.extract_embedding(x)
        return z.squeeze(0).cpu().numpy()
