"""Temporal smoothing helpers for frame/clip-level fall probabilities."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TemporalSmoothingConfig:
    threshold: float = 0.45
    ema_alpha: float = 0.35
    min_consecutive: int = 3
    cooldown_frames: int = 0
    decay_on_missing: float = 0.90


class TemporalFallSmoother:
    """EMA + consecutive-hit smoothing for deployable fall alerts."""

    def __init__(self, config: TemporalSmoothingConfig | None = None):
        self.config = config or TemporalSmoothingConfig()
        self.reset()

    def reset(self):
        self.ema: float | None = None
        self.hit_count = 0
        self.cooldown_remaining = 0

    def update(self, probability: float | None) -> tuple[float, int, int]:
        if probability is None:
            if self.ema is not None:
                self.ema *= self.config.decay_on_missing
            self.hit_count = max(0, self.hit_count - 1)
        else:
            value = float(max(0.0, min(1.0, probability)))
            if self.ema is None:
                self.ema = value
            else:
                alpha = float(max(0.0, min(1.0, self.config.ema_alpha)))
                self.ema = alpha * value + (1.0 - alpha) * self.ema
            if self.ema >= self.config.threshold:
                self.hit_count += 1
            else:
                self.hit_count = max(0, self.hit_count - 1)

        smoothed = float(self.ema or 0.0)
        pred = 0
        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1
        elif self.hit_count >= max(1, self.config.min_consecutive):
            pred = 1
            self.cooldown_remaining = max(0, self.config.cooldown_frames)
        return smoothed, pred, self.hit_count
