"""Inference-only Spatial-v5 JEPA-compatible student modules."""
from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F


class TokenDecoder(nn.Module):
    def __init__(self, dim: int, queries: int, output_dim: int):
        super().__init__()
        self.query = nn.Parameter(torch.randn(1, queries, dim) * 0.02)
        self.attn = nn.MultiheadAttention(dim, 4, batch_first=True)
        self.norm = nn.LayerNorm(dim)
        self.proj = nn.Sequential(
            nn.Linear(dim, dim * 2),
            nn.GELU(),
            nn.Linear(dim * 2, output_dim),
        )

    def forward(self, tokens: torch.Tensor) -> torch.Tensor:
        query = self.query.expand(tokens.size(0), -1, -1)
        decoded, _ = self.attn(query, tokens, tokens, need_weights=False)
        return self.proj(self.norm(query + decoded))


@dataclass
class StudentConfig:
    input_dim: int = 512
    dim: int = 512
    layers: int = 6
    heads: int = 8
    frames: int = 16
    dropout: float = 0.10
    aligned_motion: bool = False
    spatial_size: int = 1


class AlignedCurrentDecoder(nn.Module):
    def __init__(self, dim: int, output_dim: int, heads: int, dropout: float):
        super().__init__()
        self.base = nn.Linear(dim, output_dim)
        self.attention = nn.MultiheadAttention(
            output_dim, heads, dropout=dropout, batch_first=True
        )
        self.norm1 = nn.LayerNorm(output_dim)
        self.ffn = nn.Sequential(
            nn.Linear(output_dim, output_dim * 3),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(output_dim * 3, output_dim),
        )
        self.norm2 = nn.LayerNorm(output_dim)

    def forward(self, temporal: torch.Tensor, causal_mask: torch.Tensor) -> torch.Tensor:
        base = self.base(temporal)
        delta, _ = self.attention(
            base, base, base, attn_mask=causal_mask, need_weights=False
        )
        current = self.norm1(base + delta)
        return self.norm2(current + self.ffn(current))


class FutureMotionDecoder(nn.Module):
    def __init__(
        self,
        input_dim: int,
        output_dim: int,
        steps: int,
        heads: int,
        dropout: float,
    ):
        super().__init__()
        self.motion = nn.Sequential(
            nn.Linear(input_dim * 3, input_dim),
            nn.LayerNorm(input_dim),
            nn.GELU(),
        )
        self.horizon = nn.Parameter(torch.randn(1, steps, input_dim) * 0.02)
        self.attention = nn.MultiheadAttention(
            input_dim, heads, dropout=dropout, batch_first=True
        )
        self.norm = nn.LayerNorm(input_dim)
        self.proj = nn.Sequential(
            nn.Linear(input_dim, input_dim * 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(input_dim * 2, output_dim),
        )

    def forward(self, temporal: torch.Tensor) -> torch.Tensor:
        velocity = torch.diff(temporal, dim=1, prepend=temporal[:, :1])
        acceleration = torch.diff(velocity, dim=1, prepend=velocity[:, :1])
        motion = self.motion(torch.cat((temporal, velocity, acceleration), dim=-1))
        query = self.horizon.expand(temporal.size(0), -1, -1)
        future, _ = self.attention(query, motion, motion, need_weights=False)
        return self.proj(self.norm(query + future))


class ArchitecturePreservingJEPAStudent(nn.Module):
    """RGB student that reproduces the downstream V-JEPA token contract."""

    def __init__(self, config: StudentConfig):
        super().__init__()
        self.config = config
        self.full_proj = nn.Sequential(
            nn.Linear(config.input_dim, config.dim),
            nn.LayerNorm(config.dim),
            nn.GELU(),
        )
        self.crop_proj = nn.Sequential(
            nn.Linear(config.input_dim, config.dim),
            nn.LayerNorm(config.dim),
            nn.GELU(),
        )
        if config.spatial_size > 1:
            spatial_tokens = config.spatial_size * config.spatial_size
            self.spatial_embedding = nn.Parameter(
                torch.randn(1, 1, spatial_tokens, config.dim) * 0.02
            )
            self.full_spatial_query = nn.Parameter(
                torch.randn(1, 1, config.dim) * 0.02
            )
            self.crop_spatial_query = nn.Parameter(
                torch.randn(1, 1, config.dim) * 0.02
            )
            self.spatial_pool = nn.MultiheadAttention(
                config.dim,
                config.heads,
                dropout=config.dropout,
                batch_first=True,
            )
            self.spatial_norm = nn.LayerNorm(config.dim)
        self.stream_fusion = nn.Sequential(
            nn.Linear(config.dim * 4, config.dim * 2),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.dim * 2, config.dim),
            nn.LayerNorm(config.dim),
        )
        self.time_embedding = nn.Parameter(
            torch.randn(1, config.frames, config.dim) * 0.02
        )
        self.stream_embedding = nn.Parameter(torch.randn(1, 3, config.dim) * 0.02)
        layer = nn.TransformerEncoderLayer(
            config.dim,
            config.heads,
            config.dim * 4,
            config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.temporal = nn.TransformerEncoder(layer, num_layers=config.layers)
        self.temporal_norm = nn.LayerNorm(config.dim)
        self.world_decoder = TokenDecoder(config.dim, 48, 256)
        self.crop_decoder = TokenDecoder(config.dim, 48, 256)
        if config.aligned_motion:
            self.current_decoder = AlignedCurrentDecoder(
                config.dim, 256, config.heads, config.dropout
            )
            self.future_decoder = FutureMotionDecoder(
                config.dim, 256, 8, config.heads, config.dropout
            )
            self.pose_future_decoder = FutureMotionDecoder(
                config.dim, 256, 8, config.heads, config.dropout
            )
        else:
            self.current_decoder = TokenDecoder(config.dim, 16, 256)
            self.future_decoder = TokenDecoder(config.dim, 8, 256)
            self.pose_future_decoder = TokenDecoder(config.dim, 8, 256)
        self.register_buffer("current_right_inverse", torch.empty(0), persistent=True)
        self.register_buffer("future_right_inverse", torch.empty(0), persistent=True)
        self.register_buffer("current_bias", torch.empty(0), persistent=True)
        self.register_buffer("future_joint_bias", torch.empty(0), persistent=True)

    @staticmethod
    def causal_mask(length: int, device: torch.device) -> torch.Tensor:
        return torch.full((length, length), float("-inf"), device=device).triu_(1)

    def legacy_tokens(
        self,
        current: torch.Tensor,
        future: torch.Tensor,
        pose_future: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        legacy_current = (current - self.current_bias).matmul(
            self.current_right_inverse
        )
        joint = torch.cat((future, pose_future), dim=-1)
        legacy_future = (joint - self.future_joint_bias).matmul(
            self.future_right_inverse
        )
        return legacy_current, legacy_future

    def forward(
        self, full_features: torch.Tensor, crop_features: torch.Tensor
    ) -> dict[str, torch.Tensor]:
        full_projected = self.full_proj(full_features.float())
        crop_projected = self.crop_proj(crop_features.float())
        if full_projected.dim() == 4:
            batch, time_steps, spatial_tokens, dim = full_projected.shape
            position = self.spatial_embedding[:, :, :spatial_tokens]
            full_spatial = full_projected + position
            crop_spatial = crop_projected + position
            full_query = self.full_spatial_query.expand(batch * time_steps, -1, -1)
            crop_query = self.crop_spatial_query.expand(batch * time_steps, -1, -1)
            full_flat = full_spatial.reshape(batch * time_steps, spatial_tokens, dim)
            crop_flat = crop_spatial.reshape(batch * time_steps, spatial_tokens, dim)
            full_pooled, _ = self.spatial_pool(
                full_query, full_flat, full_flat, need_weights=False
            )
            crop_pooled, _ = self.spatial_pool(
                crop_query, crop_flat, crop_flat, need_weights=False
            )
            full = self.spatial_norm(full_query + full_pooled).reshape(
                batch, time_steps, dim
            )
            crop = self.spatial_norm(crop_query + crop_pooled).reshape(
                batch, time_steps, dim
            )
            full_memory = full_spatial.reshape(
                batch, time_steps * spatial_tokens, dim
            )
            crop_memory = crop_spatial.reshape(
                batch, time_steps * spatial_tokens, dim
            )
        else:
            full = full_projected
            crop = crop_projected
            full_memory = full
            crop_memory = crop
        full = full + self.stream_embedding[:, 0:1]
        crop = crop + self.stream_embedding[:, 1:2]
        fused = self.stream_fusion(
            torch.cat((full, crop, (full - crop).abs(), full * crop), dim=-1)
        )
        fused = (
            fused
            + self.stream_embedding[:, 2:3]
            + self.time_embedding[:, : fused.size(1)]
        )
        temporal = self.temporal_norm(
            self.temporal(
                fused,
                mask=self.causal_mask(fused.size(1), fused.device),
            )
        )
        if self.config.aligned_motion:
            current = self.current_decoder(
                temporal, self.causal_mask(temporal.size(1), temporal.device)
            )
        else:
            current = self.current_decoder(temporal)
        future = self.future_decoder(temporal)
        pose_future = self.pose_future_decoder(temporal)
        legacy_current, legacy_future = self.legacy_tokens(
            current, future, pose_future
        )
        return {
            "world_tokens": self.world_decoder(
                torch.cat((temporal, full_memory), dim=1)
            ),
            "crop_world_tokens": self.crop_decoder(
                torch.cat((temporal, crop_memory), dim=1)
            ),
            "current_projected": current,
            "future_projected": future,
            "pose_future_projected": pose_future,
            "current_temporal_tokens": legacy_current,
            "future_temporal_tokens": legacy_future,
        }


class CausalDepthwiseBlock(nn.Module):
    def __init__(self, dim: int, dilation: int):
        super().__init__()
        self.dilation = dilation
        self.depthwise = nn.Conv1d(
            dim, dim, 3, groups=dim, dilation=dilation, bias=False
        )
        self.pointwise = nn.Conv1d(dim, dim, 1, bias=False)
        self.norm = nn.GroupNorm(16, dim)

    def forward(self, sequence: torch.Tensor) -> torch.Tensor:
        channels_first = sequence.transpose(1, 2)
        padded = F.pad(channels_first, (2 * self.dilation, 0))
        output = self.pointwise(self.depthwise(padded))
        return F.gelu(self.norm(output)).transpose(1, 2)


class CausalTemporalResidualExpert(nn.Module):
    """Bounded, observability-aware correction for current/future tokens."""

    def __init__(
        self, dim: int = 256, heads: int = 4, residual_limit: float = 0.20
    ):
        super().__init__()
        self.residual_limit = float(residual_limit)
        self.input_norm = nn.LayerNorm(dim)
        self.blocks = nn.ModuleList(
            CausalDepthwiseBlock(dim, dilation) for dilation in (1, 2, 4)
        )
        self.mix = nn.Sequential(
            nn.Linear(dim * 3, dim * 2),
            nn.GELU(),
            nn.Linear(dim * 2, dim),
            nn.LayerNorm(dim),
        )
        self.future_attention = nn.MultiheadAttention(
            dim, heads, dropout=0.05, batch_first=True
        )
        self.future_norm = nn.LayerNorm(dim)
        self.current_head = nn.Sequential(
            nn.Linear(dim, dim), nn.GELU(), nn.Linear(dim, dim)
        )
        self.future_head = nn.Sequential(
            nn.Linear(dim * 2, dim), nn.GELU(), nn.Linear(dim, dim)
        )
        self.observability = nn.Sequential(
            nn.Linear(4, 32), nn.GELU(), nn.Linear(32, 1)
        )

    @staticmethod
    def observability_features(
        pose: torch.Tensor, boxes: torch.Tensor, steps: int
    ) -> torch.Tensor:
        indices = torch.linspace(
            0, pose.size(1) - 1, steps, device=pose.device
        ).round().long()
        selected_pose = pose[:, indices].float()
        selected_boxes = boxes[:, indices].float()
        visible = selected_pose[..., 2].clamp(0.0, 1.0).mean((1, 2))
        confidence = (
            selected_boxes[..., 4].clamp(0.0, 1.0).mean(1)
            if selected_boxes.size(-1) > 4
            else visible
        )
        valid = (
            selected_boxes[..., 5].clamp(0.0, 1.0).mean(1)
            if selected_boxes.size(-1) > 5
            else visible
        )
        center = selected_boxes[..., :2]
        jitter = torch.diff(center, dim=1).norm(dim=-1).median(dim=1).values
        stability = torch.exp(-8.0 * jitter).clamp(0.0, 1.0)
        return torch.stack((visible, confidence, valid, stability), dim=1)

    def forward(
        self,
        current: torch.Tensor,
        future: torch.Tensor,
        pose: torch.Tensor,
        boxes: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, dict[str, torch.Tensor]]:
        normalized = self.input_norm(current)
        multi_scale = [block(normalized) for block in self.blocks]
        memory = self.mix(torch.cat(multi_scale, dim=-1))
        observability = self.observability_features(
            pose, boxes, current.size(1)
        )
        reliability = torch.sigmoid(self.observability(observability)).unsqueeze(1)
        current_delta = (
            self.residual_limit
            * torch.tanh(self.current_head(memory))
            * reliability
        )
        attended, _ = self.future_attention(
            future, memory, memory, need_weights=False
        )
        future_context = self.future_norm(future + attended)
        future_delta = (
            self.residual_limit
            * torch.tanh(
                self.future_head(torch.cat((future_context, future), dim=-1))
            )
            * reliability
        )
        return current + current_delta, future + future_delta, {
            "residual_reliability": reliability.squeeze(1),
            "observability": observability,
            "current_residual": current_delta,
            "future_residual": future_delta,
        }
