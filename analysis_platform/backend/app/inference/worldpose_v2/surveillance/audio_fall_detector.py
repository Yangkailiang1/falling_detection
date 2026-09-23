"""
Audio fall detection module.

This module follows the audio branch described in the audio-visual fall
detection paper: normalize audio, focus on the highest-energy window, convert
it to a Mel spectrogram, and classify it with a compact CNN.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


@dataclass
class AudioFallConfig:
    sample_rate: int = 22050
    target_duration: float = 3.0
    realtime_window: float = 1.0
    realtime_hop: float = 0.5
    n_mels: int = 128
    n_fft: int = 2048
    hop_length: int = 512
    fall_threshold: float = 0.65
    model_path: str = ""

    def __post_init__(self):
        if not self.model_path:
            self.model_path = os.path.join(
                os.path.dirname(__file__),
                "models",
                "audio_fall_cnn.pth",
            )


class AudioFallCNN(nn.Module):
    """Small CNN for binary fall/non-fall audio classification."""

    def __init__(self, embedding_dim: int = 64):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 16, kernel_size=3, padding=1),
            nn.BatchNorm2d(16),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(16, 32, kernel_size=3, padding=1),
            nn.BatchNorm2d(32),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(32, 64, kernel_size=3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(inplace=True),
            nn.AdaptiveAvgPool2d((1, 1)),
        )
        self.embedding = nn.Sequential(
            nn.Flatten(),
            nn.Linear(64, embedding_dim),
            nn.ReLU(inplace=True),
            nn.Dropout(0.25),
        )
        self.classifier = nn.Linear(embedding_dim, 2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        z = self.extract_embedding(x)
        return self.classifier(z)

    def extract_embedding(self, x: torch.Tensor) -> torch.Tensor:
        x = self.features(x)
        return self.embedding(x)


def _require_librosa():
    try:
        import librosa  # type: ignore

        return librosa
    except ImportError as exc:
        raise ImportError(
            "Audio preprocessing requires librosa. Install project dependencies with "
            "`pip install -r requirements.txt`."
        ) from exc


def normalize_waveform(y: np.ndarray) -> np.ndarray:
    y = np.asarray(y, dtype=np.float32)
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak > 0:
        y = y / peak
    return y


def extract_centered_window(y: np.ndarray, sr: int, duration: float = 3.0) -> np.ndarray:
    """Return the highest-energy window, matching the SisFall audio pipeline."""
    target = int(duration * sr)
    if target <= 0:
        raise ValueError("duration must be positive")
    if len(y) <= target:
        return np.pad(y, (0, target - len(y))).astype(np.float32)

    hop = max(1, int(0.05 * sr))
    best_i = 0
    best_e = -1.0
    for i in range(0, len(y) - target + 1, hop):
        e = float(np.sum(y[i : i + target] ** 2))
        if e > best_e:
            best_i = i
            best_e = e
    return y[best_i : best_i + target].astype(np.float32)


def waveform_to_mel(
    y: np.ndarray,
    sr: int,
    config: Optional[AudioFallConfig] = None,
) -> np.ndarray:
    """Convert waveform to normalized Mel spectrogram [1, n_mels, time]."""
    librosa = _require_librosa()
    config = config or AudioFallConfig()
    y = normalize_waveform(y)

    mel = librosa.feature.melspectrogram(
        y=y,
        sr=sr,
        n_mels=config.n_mels,
        n_fft=config.n_fft,
        hop_length=config.hop_length,
        power=2.0,
    )
    mel_db = librosa.power_to_db(mel, ref=np.max)
    mel_db = (mel_db + 80.0) / 80.0
    mel_db = np.clip(mel_db, 0.0, 1.0).astype(np.float32)
    return mel_db[None, :, :]


def audio_file_to_mel(
    audio_path: str,
    config: Optional[AudioFallConfig] = None,
    use_energy_window: bool = True,
) -> np.ndarray:
    librosa = _require_librosa()
    config = config or AudioFallConfig()
    y, sr = librosa.load(audio_path, sr=config.sample_rate, mono=True)
    y = normalize_waveform(y)
    if use_energy_window:
        y = extract_centered_window(y, sr, config.target_duration)
    return waveform_to_mel(y, sr, config)


class AudioFallDetector:
    """Inference wrapper for the audio fall CNN."""

    class_names = ["NO_FALL", "FALL"]

    def __init__(
        self,
        config: Optional[AudioFallConfig] = None,
        device: Optional[str] = None,
        load_weights: bool = True,
    ):
        self.config = config or AudioFallConfig()
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self.model = AudioFallCNN()
        self.model.to(self.device)
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

    def extract_embedding_file(self, audio_path: str) -> np.ndarray:
        mel = audio_file_to_mel(audio_path, self.config)
        x = torch.from_numpy(mel).float().unsqueeze(0).to(self.device)
        with torch.no_grad():
            z = self.model.extract_embedding(x)
        return z.squeeze(0).cpu().numpy()


_audio_detector_instance: Optional[AudioFallDetector] = None


def get_audio_fall_detector() -> AudioFallDetector:
    global _audio_detector_instance
    if _audio_detector_instance is None:
        _audio_detector_instance = AudioFallDetector()
    return _audio_detector_instance
