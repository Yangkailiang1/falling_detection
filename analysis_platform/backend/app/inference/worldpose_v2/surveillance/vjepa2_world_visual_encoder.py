"""
V-JEPA2 visual world encoder for fall detection.

This module wraps the local ``D:/pose/vjepa2`` repository and exposes a compact
token interface that can replace the previous skeleton Transformer visual
encoder:

    video frames -> V-JEPA2 encoder / predictor -> visual tokens ``ei``

The output is intentionally small, e.g. ``[B, 48, 256]``, so the existing
audio-visual cross-attention stack can consume it without seeing thousands of
raw ViT patch tokens.
"""

from __future__ import annotations

import os
import sys
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import torch
import torch.nn as nn
import torch.nn.functional as F


IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)
TOKEN_ADAPTER_VERSION = "vjepa2-world-token-adapter-v1"


@dataclass
class VJEPA2WorldVisualConfig:
    """Configuration for the V-JEPA2 visual world encoder."""

    repo_path: str = r"D:\pose\vjepa2"
    checkpoint_path: str = ""
    variant: str = "vjepa2_1_vit_large_384"
    image_size: int = 384
    num_frames: int = 64
    tubelet_size: int = 2
    patch_size: int = 16
    encoder_dim: int = 1024
    predictor_output_dim: int = 1024
    output_dim: int = 256
    current_output_tokens: int = 32
    future_output_tokens: int = 16
    use_future_prediction: bool = True
    context_frames: int = 32
    future_frames: int = 16
    context_spatial_stride: int = 2
    future_spatial_stride: int = 4
    freeze_encoder: bool = True
    freeze_predictor: bool = True
    use_fp16_encoder: bool = True
    token_adapter_path: str = ""
    token_adapter_seed: int = 20260803
    require_token_adapter: bool = False

    def __post_init__(self):
        if not self.checkpoint_path:
            name = (
                "vjepa2_1_vitl_dist_vitG_384.pt"
                if self.variant == "vjepa2_1_vit_large_384"
                else "vitl.pt"
            )
            self.checkpoint_path = os.path.join(
                os.path.dirname(__file__),
                "models",
                "vjepa2",
                name,
            )
        if not self.token_adapter_path:
            self.token_adapter_path = os.path.join(
                os.path.dirname(__file__),
                "models",
                "vjepa2",
                "world_token_adapter_v1.pth",
            )


class TokenResampler(nn.Module):
    """Learned query resampler that compresses dense ViT tokens."""

    def __init__(self, input_dim: int, output_dim: int, num_queries: int, nhead: int = 8, dropout: float = 0.1):
        super().__init__()
        self.queries = nn.Parameter(torch.randn(1, num_queries, output_dim) * 0.02)
        self.input_proj = nn.Linear(input_dim, output_dim)
        self.attn = nn.MultiheadAttention(output_dim, nhead, dropout=dropout, batch_first=True)
        self.norm = nn.LayerNorm(output_dim)
        self.ffn = nn.Sequential(
            nn.Linear(output_dim, output_dim * 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(output_dim * 2, output_dim),
        )
        self.ffn_norm = nn.LayerNorm(output_dim)

    def forward(self, tokens: torch.Tensor) -> torch.Tensor:
        if tokens.numel() == 0:
            raise ValueError("TokenResampler received an empty token tensor")
        kv = self.input_proj(tokens)
        q = self.queries.expand(tokens.size(0), -1, -1)
        out, _ = self.attn(q, kv, kv, need_weights=False)
        out = self.norm(out + q)
        out = self.ffn_norm(out + self.ffn(out))
        return out


class VJEPA2WorldVisualEncoder(nn.Module):
    """V-JEPA2 ViT-L visual encoder with optional latent future prediction."""

    def __init__(self, config: Optional[VJEPA2WorldVisualConfig] = None, load_weights: bool = True):
        super().__init__()
        self.config = config or VJEPA2WorldVisualConfig()
        self._add_vjepa2_to_path()
        self.encoder, self.predictor = self._build_models()
        if load_weights:
            self.load_vjepa2_weights(self.config.checkpoint_path)

        if self.config.freeze_encoder:
            self.encoder.eval()
            for param in self.encoder.parameters():
                param.requires_grad = False
        if self.predictor is not None and self.config.freeze_predictor:
            self.predictor.eval()
            for param in self.predictor.parameters():
                param.requires_grad = False

        self._build_token_adapter()
        if os.path.exists(self.config.token_adapter_path):
            self.load_token_adapter(self.config.token_adapter_path)
        elif self.config.require_token_adapter:
            raise FileNotFoundError(
                "World-token adapter is required for reproducible inference but was not found: "
                f"{self.config.token_adapter_path}"
            )

        mean = torch.tensor(IMAGENET_MEAN, dtype=torch.float32).view(1, 3, 1, 1, 1)
        std = torch.tensor(IMAGENET_STD, dtype=torch.float32).view(1, 3, 1, 1, 1)
        self.register_buffer("image_mean", mean, persistent=False)
        self.register_buffer("image_std", std, persistent=False)

    def _build_token_adapter(self) -> None:
        """Build a deterministic adapter without perturbing global RNG state."""
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(int(self.config.token_adapter_seed))
            self.current_resampler = TokenResampler(
                input_dim=self.config.encoder_dim,
                output_dim=self.config.output_dim,
                num_queries=self.config.current_output_tokens,
            )
            self.future_resampler = TokenResampler(
                input_dim=self.config.predictor_output_dim,
                output_dim=self.config.output_dim,
                num_queries=self.config.future_output_tokens,
            )
            self.type_embedding = nn.Parameter(torch.randn(1, 2, self.config.output_dim) * 0.02)

    def token_adapter_state_dict(self) -> dict[str, torch.Tensor]:
        return {
            **{f"current_resampler.{key}": value.detach().cpu() for key, value in self.current_resampler.state_dict().items()},
            **{f"future_resampler.{key}": value.detach().cpu() for key, value in self.future_resampler.state_dict().items()},
            "type_embedding": self.type_embedding.detach().cpu(),
        }

    def token_adapter_fingerprint(self) -> str:
        digest = hashlib.sha256()
        for key, value in sorted(self.token_adapter_state_dict().items()):
            digest.update(key.encode("utf-8"))
            digest.update(value.detach().cpu().contiguous().numpy().tobytes())
        return digest.hexdigest()[:16]

    def save_token_adapter(self, path: str | None = None) -> str:
        path = path or self.config.token_adapter_path
        payload = {
            "version": TOKEN_ADAPTER_VERSION,
            "fingerprint": self.token_adapter_fingerprint(),
            "config": {
                "output_dim": self.config.output_dim,
                "current_output_tokens": self.config.current_output_tokens,
                "future_output_tokens": self.config.future_output_tokens,
                "encoder_dim": self.config.encoder_dim,
                "predictor_output_dim": self.config.predictor_output_dim,
            },
            "state_dict": self.token_adapter_state_dict(),
        }
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        torch.save(payload, path)
        return str(payload["fingerprint"])

    def load_token_adapter(self, path: str) -> str:
        payload = torch.load(path, map_location="cpu", weights_only=False)
        state = payload.get("state_dict", payload)
        current = {
            key.removeprefix("current_resampler."): value
            for key, value in state.items()
            if key.startswith("current_resampler.")
        }
        future = {
            key.removeprefix("future_resampler."): value
            for key, value in state.items()
            if key.startswith("future_resampler.")
        }
        if not current or not future or "type_embedding" not in state:
            raise ValueError(f"Invalid world-token adapter: {path}")
        self.current_resampler.load_state_dict(current, strict=True)
        self.future_resampler.load_state_dict(future, strict=True)
        with torch.no_grad():
            self.type_embedding.copy_(state["type_embedding"].to(self.type_embedding))
        actual = self.token_adapter_fingerprint()
        expected = str(payload.get("fingerprint", actual))
        if expected != actual:
            raise ValueError(f"World-token adapter fingerprint mismatch for {path}: expected={expected} actual={actual}")
        return actual

    def _add_vjepa2_to_path(self):
        repo = str(Path(self.config.repo_path).resolve())
        if repo not in sys.path:
            sys.path.insert(0, repo)

    def _build_models(self):
        from src.hub import backbones

        if self.config.variant == "vjepa2_1_vit_large_384":
            encoder, predictor = backbones.vjepa2_1_vit_large_384(
                pretrained=False,
                num_frames=self.config.num_frames,
                tubelet_size=self.config.tubelet_size,
            )
            self.config.encoder_dim = encoder.embed_dim
            self.config.predictor_output_dim = predictor.predictor_proj.out_features
            return encoder, predictor
        if self.config.variant == "vjepa2_vit_large":
            encoder, predictor = backbones.vjepa2_vit_large(
                pretrained=False,
                num_frames=self.config.num_frames,
                tubelet_size=self.config.tubelet_size,
            )
            self.config.encoder_dim = encoder.embed_dim
            self.config.predictor_output_dim = predictor.predictor_proj.out_features
            return encoder, predictor
        raise ValueError(f"Unsupported V-JEPA2 variant: {self.config.variant}")

    @staticmethod
    def _clean_state_dict(state_dict: dict[str, torch.Tensor]) -> dict[str, torch.Tensor]:
        cleaned = {}
        for key, value in state_dict.items():
            key = key.replace("module.", "").replace("backbone.", "")
            cleaned[key] = value
        return cleaned

    def load_vjepa2_weights(self, checkpoint_path: str):
        if not checkpoint_path or not os.path.exists(checkpoint_path):
            raise FileNotFoundError(f"V-JEPA2 checkpoint not found: {checkpoint_path}")
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
        if self.config.variant == "vjepa2_1_vit_large_384":
            encoder_key = "ema_encoder"
        else:
            encoder_key = "target_encoder"
        if encoder_key not in checkpoint:
            encoder_key = "encoder"

        encoder_state = self._clean_state_dict(checkpoint[encoder_key])
        predictor_state = self._clean_state_dict(checkpoint["predictor"])
        self.encoder.load_state_dict(encoder_state, strict=self.config.variant == "vjepa2_1_vit_large_384")
        self.predictor.load_state_dict(predictor_state, strict=self.config.variant == "vjepa2_1_vit_large_384")

    def _prepare_frames(self, frames: torch.Tensor) -> torch.Tensor:
        """Normalize frames and convert to V-JEPA layout ``[B, 3, T, H, W]``."""
        if frames.dim() != 5:
            raise ValueError(f"Expected frames [B,T,C,H,W] or [B,C,T,H,W], got {tuple(frames.shape)}")

        # Prefer project-facing layout [B, T, C, H, W].
        if frames.shape[2] == 3:
            frames = frames.permute(0, 2, 1, 3, 4)
        elif frames.shape[1] != 3:
            raise ValueError(f"Cannot infer channel dimension from frames shape: {tuple(frames.shape)}")

        frames = frames.float()
        if frames.max() > 2.0:
            frames = frames / 255.0

        b, c, t, h, w = frames.shape
        target_t = self.config.context_frames + (self.config.future_frames if self.config.use_future_prediction else 0)
        if t < target_t:
            pad = frames[:, :, -1:, :, :].repeat(1, 1, target_t - t, 1, 1)
            frames = torch.cat([frames, pad], dim=2)
        elif t > target_t:
            frames = frames[:, :, :target_t, :, :]

        if h != self.config.image_size or w != self.config.image_size:
            frames_2d = frames.permute(0, 2, 1, 3, 4).reshape(-1, c, h, w)
            frames_2d = F.interpolate(
                frames_2d,
                size=(self.config.image_size, self.config.image_size),
                mode="bilinear",
                align_corners=False,
            )
            frames = frames_2d.reshape(b, -1, c, self.config.image_size, self.config.image_size)
            frames = frames.permute(0, 2, 1, 3, 4)

        return (frames - self.image_mean) / self.image_std

    def _make_temporal_masks(self, batch: int, total_frames: int, device: torch.device):
        h_patches = self.config.image_size // self.config.patch_size
        w_patches = self.config.image_size // self.config.patch_size
        tokens_per_tubelet = h_patches * w_patches
        tubelets = total_frames // self.config.tubelet_size

        context_tubelets = max(1, self.config.context_frames // self.config.tubelet_size)
        current_ids = []
        future_ids = []
        for tt in range(tubelets):
            stride = self.config.context_spatial_stride if tt < context_tubelets else self.config.future_spatial_stride
            for hh in range(0, h_patches, stride):
                for ww in range(0, w_patches, stride):
                    idx = tt * tokens_per_tubelet + hh * w_patches + ww
                    if tt < context_tubelets:
                        current_ids.append(idx)
                    else:
                        future_ids.append(idx)

        if not future_ids:
            future_ids = current_ids[-min(len(current_ids), tokens_per_tubelet):]

        masks_x = torch.tensor(current_ids, dtype=torch.long, device=device).unsqueeze(0).repeat(batch, 1)
        masks_y = torch.tensor(future_ids, dtype=torch.long, device=device).unsqueeze(0).repeat(batch, 1)
        return masks_x, masks_y

    def extract_token_bundle(self, frames: torch.Tensor) -> dict[str, torch.Tensor]:
        """Extract deployment tokens plus temporally aligned dense summaries.

        The learned-query resamplers used by the deployed fusion model do not
        preserve a one-token-per-time-step contract.  Pose forecasting needs
        that contract, so this method also exposes spatially pooled tubelet
        tokens before the learned-query resamplers.  The existing
        ``extract_tokens`` API remains unchanged and returns ``world_tokens``.
        """
        frames = self._prepare_frames(frames)
        batch = frames.size(0)
        total_frames = frames.size(2)
        masks_x, masks_y = self._make_temporal_masks(batch, total_frames, frames.device)

        autocast_enabled = (
            self.config.use_fp16_encoder
            and frames.is_cuda
            and self.config.freeze_encoder
        )
        with torch.no_grad() if self.config.freeze_encoder else torch.enable_grad():
            with torch.amp.autocast("cuda", dtype=torch.float16, enabled=autocast_enabled):
                context_tokens = self.encoder(frames, masks=masks_x)

        context_tokens = context_tokens.float()
        current_tokens = self.current_resampler(context_tokens)
        current_tokens = current_tokens + self.type_embedding[:, 0:1, :]

        context_tubelets = max(1, self.config.context_frames // self.config.tubelet_size)
        context_spatial = len(range(0, self.config.image_size // self.config.patch_size, self.config.context_spatial_stride)) ** 2
        expected_context = context_tubelets * context_spatial
        if context_tokens.size(1) != expected_context:
            raise RuntimeError(
                "Unexpected V-JEPA context-token layout: "
                f"tokens={context_tokens.size(1)} expected={expected_context}"
            )
        current_temporal = context_tokens.reshape(
            batch,
            context_tubelets,
            context_spatial,
            context_tokens.size(-1),
        ).mean(dim=2)

        bundle = {
            "current_tokens": current_tokens,
            "current_temporal_tokens": current_temporal,
        }

        if self.config.use_future_prediction and self.predictor is not None:
            with torch.no_grad() if self.config.freeze_predictor else torch.enable_grad():
                pred_tokens, _ = self.predictor(
                    context_tokens,
                    masks_x=[masks_x],
                    masks_y=[masks_y],
                    mod="video",
                )
            pred_tokens = pred_tokens.float()
            future_tokens = self.future_resampler(pred_tokens)
            future_tokens = future_tokens + self.type_embedding[:, 1:2, :]
            future_tubelets = max(1, self.config.future_frames // self.config.tubelet_size)
            future_spatial = len(range(0, self.config.image_size // self.config.patch_size, self.config.future_spatial_stride)) ** 2
            expected_future = future_tubelets * future_spatial
            if pred_tokens.size(1) != expected_future:
                raise RuntimeError(
                    "Unexpected V-JEPA future-token layout: "
                    f"tokens={pred_tokens.size(1)} expected={expected_future}"
                )
            future_temporal = pred_tokens.reshape(
                batch,
                future_tubelets,
                future_spatial,
                pred_tokens.size(-1),
            ).mean(dim=2)
            bundle.update({
                "future_tokens": future_tokens,
                "future_temporal_tokens": future_temporal,
                "world_tokens": torch.cat([current_tokens, future_tokens], dim=1),
            })
            return bundle

        bundle["world_tokens"] = current_tokens
        return bundle

    def extract_tokens(self, frames: torch.Tensor) -> torch.Tensor:
        return self.extract_token_bundle(frames)["world_tokens"]

    def forward(self, frames: torch.Tensor) -> torch.Tensor:
        return self.extract_tokens(frames)
