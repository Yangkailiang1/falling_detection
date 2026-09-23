"""Causal V-JEPA conditioned Pose future prediction and visual fusion."""

from __future__ import annotations

import copy
from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F

from surveillance.av_utility_gated_world_fusion import TemporalWorldVisualHead, UtilityGatedWorldConfig


@dataclass
class WorldPoseFutureConfig:
    dim: int = 256
    nhead: int = 8
    dropout: float = 0.15
    pose_layers: int = 2
    predictor_layers: int = 2
    fusion_layers: int = 2
    feedforward_dim: int = 512
    joints: int = 17
    context_frames: int = 32
    future_frames: int = 16
    current_world_dim: int = 1024
    future_world_dim: int = 1664
    body_queries: int = 6
    phase_classes: int = 16
    # Keep the original monotonic rescue as the compatibility default.  The
    # full-data experiment enables a bounded signed residual so Pose can also
    # suppress lie-down/recovery false positives.
    signed_pose_residual: bool = False
    pose_residual_limit: float = 2.0
    # When enabled, reproduce the validated visual history/long-view branch
    # before applying the Pose residual.
    use_full_visual_context: bool = False
    # Optional second visual stream built from a person-centered RGB crop.
    # The original full-frame World branch remains intact; this branch is a
    # token-level, bounded correction used only when a crop cache is supplied.
    use_crop_tokens: bool = False
    crop_gate_bias: float = -2.0
    # Explicit visual fusion in which observed/predicted Pose tokens are the
    # primary evidence and JEPA world tokens provide causal scene/context.
    pose_led_visual: bool = False
    pose_led_layers: int = 2
    pose_expert_fusion: bool = False
    pose_expert_gate_bias: float = -2.0
    phase_motion_gate: bool = False
    short_pose_frames: int = 16
    short_pose_layers: int = 1
    short_pose_expert_fusion: bool = False
    # Use the causal predicted future trajectory as the primary visual
    # classifier.  The JEPA world classifier remains an auxiliary context
    # expert and is not discarded.
    future_pose_led: bool = False
    dual_pose_heads: bool = False
    # Train a separate, deployable warning expert that chooses between the
    # World warning and the Pose fallback using learned expert utility.
    domain_warning_gate: bool = False
    domain_gate_bias: float = 0.0
    # Deployment-preserving post experts.  These are deliberately disabled
    # by default so legacy checkpoints keep their original computation.
    post_residual_experts: bool = False
    controlled_suppress_limit: float = 1.2
    occlusion_rescue_limit: float = 1.0


class PoseSpatialTemporalEncoder(nn.Module):
    """Encode absolute and body-relative COCO17 trajectories."""

    def __init__(self, config: WorldPoseFutureConfig):
        super().__init__()
        self.config = config
        self.input_proj = nn.Linear(14, config.dim)
        self.joint_embedding = nn.Parameter(torch.randn(1, 1, config.joints, config.dim) * 0.02)
        self.time_embedding = nn.Parameter(torch.randn(1, config.context_frames, 1, config.dim) * 0.02)
        self.role_embedding = nn.Parameter(torch.randn(1, 2, 1, 1, config.dim) * 0.02)
        spatial = nn.TransformerEncoderLayer(
            config.dim,
            config.nhead,
            config.feedforward_dim,
            config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        temporal = nn.TransformerEncoderLayer(
            config.dim,
            config.nhead,
            config.feedforward_dim,
            config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.spatial = nn.TransformerEncoder(spatial, num_layers=config.pose_layers)
        self.temporal = nn.TransformerEncoder(temporal, num_layers=config.pose_layers)
        self.norm = nn.LayerNorm(config.dim)

    @staticmethod
    def features(pose: torch.Tensor, boxes: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        xy = pose[..., :2].float()
        confidence = pose[..., 2:3].float().clamp(0.0, 1.0)
        visible = (confidence >= 0.15).to(xy)
        box_center = boxes[..., :2].float().unsqueeze(2)
        box_scale = boxes[..., 2:4].float().clamp_min(0.05).unsqueeze(2)
        relative = (xy - box_center) / box_scale
        velocity = torch.zeros_like(xy)
        velocity[:, 1:] = xy[:, 1:] - xy[:, :-1]
        box_features = boxes.float().unsqueeze(2).expand(-1, -1, xy.size(2), -1)
        features = torch.cat((xy, relative, confidence, visible, velocity, box_features), dim=-1)
        quality = torch.stack(
            (
                confidence.mean(dim=(1, 2, 3)),
                visible.mean(dim=(1, 2, 3)),
                boxes[..., 4].float().mean(dim=1),
                boxes[..., 5].float().mean(dim=1),
            ),
            dim=1,
        )
        return features, quality

    def forward(self, pose: torch.Tensor, boxes: torch.Tensor, role: int = 0):
        features, quality = self.features(pose, boxes)
        batch, frames, joints, _ = features.shape
        if joints != self.config.joints or frames > self.config.context_frames:
            raise ValueError(f"Unexpected Pose shape: {tuple(pose.shape)}")
        x = self.input_proj(features)
        x = x + self.joint_embedding[:, :, :joints]
        x = x + self.time_embedding[:, :frames]
        x = x + self.role_embedding[:, int(role)]
        x = self.spatial(x.reshape(batch * frames, joints, -1)).reshape(batch, frames, joints, -1)
        x = x.permute(0, 2, 1, 3).reshape(batch * joints, frames, -1)
        x = self.temporal(x).reshape(batch, joints, frames, -1).permute(0, 2, 1, 3)
        x = self.norm(x)
        if frames % 2:
            x = x[:, :-1]
        x = x.reshape(batch, x.size(1) // 2, 2, joints, x.size(-1)).mean(dim=2)
        return x, quality


class ShortPoseMotionEncoder(nn.Module):
    """Causal short-horizon motion/event encoder.

    It consumes only the latest observed pose and box trajectory.  The
    predicted Pose Future tokens are used as a query/value context, so this
    branch can react to a rapid fall without waiting for the full JEPA
    context, while remaining causal at inference time.
    """

    def __init__(self, config: WorldPoseFutureConfig):
        super().__init__()
        self.config = config
        self.input_proj = nn.Linear(19, config.dim)
        self.time_embedding = nn.Parameter(
            torch.randn(1, config.short_pose_frames, config.dim) * 0.02
        )
        self.cls_token = nn.Parameter(torch.zeros(1, 1, config.dim))
        layer = nn.TransformerEncoderLayer(
            config.dim,
            config.nhead,
            config.feedforward_dim,
            config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=config.short_pose_layers)
        self.future_attention = nn.MultiheadAttention(
            config.dim, config.nhead, config.dropout, batch_first=True
        )
        self.norm = nn.LayerNorm(config.dim)
        self.event_head = TemporalWorldVisualHead._head(config.dim, 2, config.dropout)
        self.phase_head = TemporalWorldVisualHead._head(
            config.dim, config.phase_classes, config.dropout
        )

    @staticmethod
    def kinematic_features(pose: torch.Tensor, boxes: torch.Tensor) -> torch.Tensor:
        xy = pose[..., :2].float()
        confidence = pose[..., 2:3].float().clamp(0.0, 1.0)

        def joint_mean(indices):
            values = xy[:, :, indices]
            weights = confidence[:, :, indices]
            return (values * weights).sum(dim=2) / weights.sum(dim=2).clamp_min(1e-3)

        hip = joint_mean((11, 12))
        shoulder = joint_mean((5, 6))
        torso = shoulder - hip
        bbox = boxes[..., :4].float()
        bbox_center = bbox[..., :2]
        hip_velocity = torch.cat((torch.zeros_like(hip[:, :1]), hip[:, 1:] - hip[:, :-1]), dim=1)
        bbox_velocity = torch.cat(
            (torch.zeros_like(bbox_center[:, :1]), bbox_center[:, 1:] - bbox_center[:, :-1]), dim=1
        )
        size_velocity = torch.cat(
            (torch.zeros_like(bbox[..., 2:4][:, :1]), bbox[..., 2:4][:, 1:] - bbox[..., 2:4][:, :-1]), dim=1
        )
        torso_velocity = torch.cat(
            (torch.zeros_like(torso[:, :1]), torso[:, 1:] - torso[:, :-1]), dim=1
        )
        confidence_mean = confidence.mean(dim=2)
        return torch.cat(
            (
                hip, shoulder, torso, bbox,
                hip_velocity, bbox_velocity, size_velocity, torso_velocity,
                confidence_mean,
            ),
            dim=-1,
        )

    def forward(
        self,
        pose: torch.Tensor,
        boxes: torch.Tensor,
        future_pose_tokens: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        features = self.kinematic_features(pose, boxes)
        features = features[:, -self.config.short_pose_frames :]
        if features.size(1) < self.config.short_pose_frames:
            features = torch.cat(
                (
                    features[:, :1].expand(-1, self.config.short_pose_frames - features.size(1), -1),
                    features,
                ),
                dim=1,
            )
        tokens = self.input_proj(features)
        tokens = tokens + self.time_embedding
        cls = self.cls_token.expand(tokens.size(0), -1, -1)
        encoded = self.encoder(torch.cat((cls, tokens), dim=1))
        summary = encoded[:, :1]
        future = future_pose_tokens.reshape(future_pose_tokens.size(0), -1, future_pose_tokens.size(-1))
        attended, _ = self.future_attention(summary, future, future, need_weights=False)
        embedding = self.norm((summary + attended).squeeze(1))
        return embedding, self.event_head(embedding), self.phase_head(embedding)


class PoseFuturePredictor(nn.Module):
    def __init__(self, config: WorldPoseFutureConfig):
        super().__init__()
        self.config = config
        self.future_steps = config.future_frames // 2
        self.horizon_embedding = nn.Parameter(torch.randn(1, self.future_steps, 1, config.dim) * 0.02)
        self.joint_embedding = nn.Parameter(torch.randn(1, 1, config.joints, config.dim) * 0.02)
        self.world_proj = nn.Linear(config.future_world_dim, config.dim)
        self.context_attention = nn.MultiheadAttention(config.dim, config.nhead, config.dropout, batch_first=True)
        self.context_norm = nn.LayerNorm(config.dim)
        spatial = nn.TransformerEncoderLayer(
            config.dim, config.nhead, config.feedforward_dim, config.dropout,
            activation="gelu", batch_first=True, norm_first=True,
        )
        temporal = nn.TransformerEncoderLayer(
            config.dim, config.nhead, config.feedforward_dim, config.dropout,
            activation="gelu", batch_first=True, norm_first=True,
        )
        self.spatial = nn.TransformerEncoder(spatial, num_layers=config.predictor_layers)
        self.temporal = nn.TransformerEncoder(temporal, num_layers=config.predictor_layers)
        self.norm = nn.LayerNorm(config.dim)
        self.coordinate_head = nn.Linear(config.dim, 4)
        self.visibility_head = nn.Linear(config.dim, 2)
        self.log_sigma_head = nn.Linear(config.dim, 4)
        self.bbox_head = nn.Sequential(nn.Linear(config.dim, config.dim), nn.GELU(), nn.Linear(config.dim, 8))

    def forward(self, context_pose_tokens: torch.Tensor, world_future: torch.Tensor):
        batch, _, joints, dim = context_pose_tokens.shape
        if world_future.size(1) != self.future_steps:
            raise ValueError(f"Expected {self.future_steps} future world steps, got {world_future.size(1)}")
        query = self.horizon_embedding.expand(batch, -1, joints, -1)
        query = query + self.joint_embedding[:, :, :joints]
        query = query + self.world_proj(world_future).unsqueeze(2)
        flat_query = query.reshape(batch, self.future_steps * joints, dim)
        context = context_pose_tokens.reshape(batch, -1, dim)
        attended, _ = self.context_attention(flat_query, context, context, need_weights=False)
        x = self.context_norm(flat_query + attended).reshape(batch, self.future_steps, joints, dim)
        x = self.spatial(x.reshape(batch * self.future_steps, joints, dim)).reshape(batch, self.future_steps, joints, dim)
        x = x.permute(0, 2, 1, 3).reshape(batch * joints, self.future_steps, dim)
        x = self.temporal(x).reshape(batch, joints, self.future_steps, dim).permute(0, 2, 1, 3)
        x = self.norm(x)
        coordinates = self.coordinate_head(x).reshape(batch, self.future_steps, joints, 2, 2)
        coordinates = coordinates.permute(0, 1, 3, 2, 4).reshape(batch, self.future_steps * 2, joints, 2)
        visibility = self.visibility_head(x).reshape(batch, self.future_steps, joints, 2)
        visibility = visibility.permute(0, 1, 3, 2).reshape(batch, self.future_steps * 2, joints)
        log_sigma = self.log_sigma_head(x).clamp(-5.0, 3.0).reshape(batch, self.future_steps, joints, 2, 2)
        log_sigma = log_sigma.permute(0, 1, 3, 2, 4).reshape(batch, self.future_steps * 2, joints, 2)
        bbox = self.bbox_head(x.mean(dim=2)).reshape(batch, self.future_steps, 2, 4).reshape(batch, self.future_steps * 2, 4)
        return {
            "pose_future_tokens": x,
            "pose_future_coordinates": coordinates,
            "pose_future_visibility_logits": visibility,
            "pose_future_log_sigma": log_sigma,
            "bbox_future": bbox,
        }


class FuturePoseRiskHead(nn.Module):
    """Classify the *predicted* future Pose trajectory.

    The classifier never consumes the ground-truth future Pose.  During
    training, ``pose_future`` is only a target for the predictor; this head
    reads the predictor's coordinates, visibility probabilities and
    uncertainty, so the deployment path remains strictly causal.
    """

    def __init__(self, config: WorldPoseFutureConfig):
        super().__init__()
        self.config = config
        # predicted xy (2), visibility (1), coordinate sigma (2), displacement
        # from the last observed pose (2), current xy/confidence (3), and the
        # predicted bbox (4), broadcast to each joint.
        self.input_proj = nn.Linear(14, config.dim)
        self.joint_embedding = nn.Parameter(
            torch.randn(1, 1, config.joints, config.dim) * 0.02
        )
        self.time_embedding = nn.Parameter(
            torch.randn(1, config.future_frames, 1, config.dim) * 0.02
        )
        spatial_layer = nn.TransformerEncoderLayer(
            config.dim, config.nhead, config.feedforward_dim, config.dropout,
            activation="gelu", batch_first=True, norm_first=True,
        )
        temporal_layer = nn.TransformerEncoderLayer(
            config.dim, config.nhead, config.feedforward_dim, config.dropout,
            activation="gelu", batch_first=True, norm_first=True,
        )
        self.spatial = nn.TransformerEncoder(spatial_layer, num_layers=1)
        self.temporal = nn.TransformerEncoder(temporal_layer, num_layers=1)
        self.fusion = nn.Sequential(
            nn.LayerNorm(config.dim * 3),
            nn.Linear(config.dim * 3, config.dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.LayerNorm(config.dim),
        )
        self.binary_head = TemporalWorldVisualHead._head(config.dim, 2, config.dropout)
        self.phase_head = TemporalWorldVisualHead._head(
            config.dim, config.phase_classes, config.dropout
        )

    def forward(
        self,
        predicted_coordinates: torch.Tensor,
        predicted_visibility_logits: torch.Tensor,
        predicted_log_sigma: torch.Tensor,
        predicted_bbox: torch.Tensor,
        observed_pose: torch.Tensor,
        observed_bbox: torch.Tensor,
        predicted_tokens: torch.Tensor,
    ):
        last_pose = observed_pose[:, -1, :, :3].float()
        last_xy = last_pose[..., :2]
        last_conf = last_pose[..., 2:3].clamp(0.0, 1.0)
        coords = predicted_coordinates.float()
        visibility = predicted_visibility_logits.float().sigmoid().unsqueeze(-1)
        sigma = predicted_log_sigma.float().exp().clamp(0.0, 8.0)
        displacement = coords - last_xy.unsqueeze(1)
        bbox = predicted_bbox.float().unsqueeze(2).expand(-1, -1, coords.size(2), -1)
        current = torch.cat(
            (last_xy.unsqueeze(1).expand(-1, coords.size(1), -1, -1),
             last_conf.unsqueeze(1).expand(-1, coords.size(1), -1, -1)), dim=-1
        )
        features = torch.cat(
            (coords, visibility, sigma, displacement, current, bbox), dim=-1
        )
        x = self.input_proj(features)
        x = x + self.joint_embedding[:, :, :coords.size(2)]
        x = x + self.time_embedding[:, :coords.size(1)]
        x = self.spatial(x.reshape(x.size(0) * x.size(1), x.size(2), -1))
        x = x.reshape(coords.size(0), coords.size(1), coords.size(2), -1)
        x = x.permute(0, 2, 1, 3).reshape(
            coords.size(0) * coords.size(2), coords.size(1), -1
        )
        x = self.temporal(x)
        x = x.reshape(coords.size(0), coords.size(2), coords.size(1), -1).permute(0, 2, 1, 3)
        future_summary = x.mean(dim=(1, 2))
        observed_summary = predicted_tokens.new_zeros(predicted_tokens.size(0), predicted_tokens.size(-1))
        if predicted_tokens is not None:
            observed_summary = predicted_tokens.mean(dim=(1, 2))
        pose_summary = self.fusion(torch.cat((future_summary, observed_summary, future_summary - observed_summary), dim=1))
        return pose_summary, self.binary_head(pose_summary), self.phase_head(pose_summary)


class ObservedPoseConfirmHead(nn.Module):
    """Current-fall confirmation from observed Pose tokens only."""

    def __init__(self, config: WorldPoseFutureConfig):
        super().__init__()
        self.cls_token = nn.Parameter(torch.zeros(1, 1, config.dim))
        layer = nn.TransformerEncoderLayer(
            config.dim, config.nhead, config.feedforward_dim, config.dropout,
            activation="gelu", batch_first=True, norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=1)
        self.norm = nn.LayerNorm(config.dim)
        self.binary_head = TemporalWorldVisualHead._head(config.dim, 2, config.dropout)
        self.phase_head = TemporalWorldVisualHead._head(
            config.dim, config.phase_classes, config.dropout
        )

    def forward(self, observed_pose_tokens: torch.Tensor):
        tokens = observed_pose_tokens.reshape(
            observed_pose_tokens.size(0), -1, observed_pose_tokens.size(-1)
        )
        cls = self.cls_token.expand(tokens.size(0), -1, -1)
        encoded = self.norm(self.encoder(torch.cat((cls, tokens), dim=1)))
        embedding = encoded[:, 0]
        return embedding, self.binary_head(embedding), self.phase_head(embedding)


class BodyPartPool(nn.Module):
    def __init__(self, config: WorldPoseFutureConfig):
        super().__init__()
        self.queries = nn.Parameter(torch.randn(1, config.body_queries, config.dim) * 0.02)
        self.attention = nn.MultiheadAttention(config.dim, config.nhead, config.dropout, batch_first=True)
        self.norm = nn.LayerNorm(config.dim)

    def forward(self, tokens: torch.Tensor) -> torch.Tensor:
        batch, time_steps, joints, dim = tokens.shape
        source = tokens.reshape(batch * time_steps, joints, dim)
        query = self.queries.expand(batch * time_steps, -1, -1)
        pooled, _ = self.attention(query, source, source, need_weights=False)
        return self.norm(query + pooled).reshape(batch, time_steps * query.size(1), dim)


class WorldPoseVisualFusion(nn.Module):
    """World baseline with token-level Pose fusion and monotonic rescue."""

    def __init__(self, config: WorldPoseFutureConfig | None = None):
        super().__init__()
        self.config = config or WorldPoseFutureConfig()
        visual_config = UtilityGatedWorldConfig(
            dim=self.config.dim,
            nhead=self.config.nhead,
            dropout=self.config.dropout,
            visual_layers=2,
            fusion_layers=self.config.fusion_layers,
            feedforward_dim=self.config.feedforward_dim,
            num_phase_classes=self.config.phase_classes,
        )
        self.visual_model = TemporalWorldVisualHead(visual_config)
        self.crop_visual_model = None
        self.crop_cross_attention = None
        self.crop_cross_norm = None
        self.crop_embedding_fusion = None
        self.crop_binary_delta = None
        self.crop_phase_delta = None
        self.crop_gate = None
        if self.config.use_crop_tokens:
            # Start the crop stream from the validated full-frame head.  The
            # crop stream is trainable, while visual_model remains the frozen
            # compatibility baseline loaded from the stable checkpoint.
            self.crop_visual_model = copy.deepcopy(self.visual_model)
            self.crop_cross_attention = nn.MultiheadAttention(
                self.config.dim, self.config.nhead, self.config.dropout, batch_first=True
            )
            self.crop_cross_norm = nn.LayerNorm(self.config.dim)
            self.crop_embedding_fusion = nn.Sequential(
                nn.Linear(self.config.dim * 2, self.config.dim),
                nn.GELU(),
                nn.Dropout(self.config.dropout),
                nn.Linear(self.config.dim, self.config.dim),
            )
            self.crop_embedding_delta = nn.Sequential(
                nn.Linear(self.config.dim, self.config.dim),
                nn.GELU(),
                nn.Dropout(self.config.dropout),
                nn.Linear(self.config.dim, self.config.dim),
            )
            self.crop_binary_delta = TemporalWorldVisualHead._head(
                self.config.dim, 2, self.config.dropout
            )
            self.crop_phase_delta = TemporalWorldVisualHead._head(
                self.config.dim, self.config.phase_classes, self.config.dropout
            )
            self.crop_gate = nn.Sequential(
                nn.Linear(self.config.dim * 4, self.config.dim // 2),
                nn.GELU(),
                nn.Dropout(self.config.dropout),
                nn.Linear(self.config.dim // 2, 1),
            )
            nn.init.zeros_(self.crop_binary_delta[-1].weight)
            nn.init.zeros_(self.crop_binary_delta[-1].bias)
            nn.init.zeros_(self.crop_phase_delta[-1].weight)
            nn.init.zeros_(self.crop_phase_delta[-1].bias)
            nn.init.zeros_(self.crop_embedding_delta[-1].weight)
            nn.init.zeros_(self.crop_embedding_delta[-1].bias)
            nn.init.zeros_(self.crop_gate[-1].weight)
            nn.init.constant_(self.crop_gate[-1].bias, float(self.config.crop_gate_bias))
        self.phase_binary_scale = nn.Parameter(torch.tensor(-3.0))
        if self.config.use_full_visual_context:
            self.context_adapter = nn.Sequential(
                nn.Linear(self.config.dim * 2, self.config.dim),
                nn.GELU(),
                nn.Dropout(self.config.dropout),
                nn.Linear(self.config.dim, self.config.dim),
            )
            self.history_adapter = nn.Sequential(
                nn.Linear(self.config.dim * 2, self.config.dim),
                nn.GELU(),
                nn.Dropout(self.config.dropout),
                nn.Linear(self.config.dim, self.config.dim),
            )
            self.long_view_adapter = nn.Sequential(
                nn.Linear(self.config.dim * 2, self.config.dim),
                nn.GELU(),
                nn.Dropout(self.config.dropout),
                nn.Linear(self.config.dim, self.config.dim),
            )
            self.context_binary_delta = nn.Linear(self.config.dim, 2)
            self.context_phase_delta = nn.Linear(self.config.dim, self.config.phase_classes)
            self.history_binary_delta = nn.Linear(self.config.dim, 2)
            self.history_phase_delta = nn.Linear(self.config.dim, self.config.phase_classes)
            self.long_binary_delta = nn.Linear(self.config.dim, 2)
            self.long_phase_delta = nn.Linear(self.config.dim, self.config.phase_classes)
        self.pose_encoder = PoseSpatialTemporalEncoder(self.config)
        self.pose_target_encoder = copy.deepcopy(self.pose_encoder)
        for parameter in self.pose_target_encoder.parameters():
            parameter.requires_grad = False
        self.pose_predictor = PoseFuturePredictor(self.config)
        self.future_pose_risk_head = (
            FuturePoseRiskHead(self.config)
            if (self.config.future_pose_led or self.config.dual_pose_heads)
            else None
        )
        self.observed_pose_confirm_head = (
            ObservedPoseConfirmHead(self.config)
            if self.config.dual_pose_heads
            else None
        )
        self.current_world_proj = nn.Linear(self.config.current_world_dim, self.config.dim)
        self.future_world_proj = nn.Linear(self.config.future_world_dim, self.config.dim)
        self.body_pool = BodyPartPool(self.config)
        self.world_to_pose = nn.MultiheadAttention(self.config.dim, self.config.nhead, self.config.dropout, batch_first=True)
        self.pose_to_world = nn.MultiheadAttention(self.config.dim, self.config.nhead, self.config.dropout, batch_first=True)
        self.world_norm = nn.LayerNorm(self.config.dim)
        self.pose_norm = nn.LayerNorm(self.config.dim)
        self.cls_token = nn.Parameter(torch.zeros(1, 1, self.config.dim))
        fusion_layer = nn.TransformerEncoderLayer(
            self.config.dim,
            self.config.nhead,
            self.config.feedforward_dim,
            self.config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.fusion = nn.TransformerEncoder(fusion_layer, num_layers=self.config.fusion_layers)
        self.fusion_norm = nn.LayerNorm(self.config.dim)
        self.pose_phase_head = nn.Linear(self.config.dim, self.config.phase_classes)
        self.pose_head = nn.Linear(self.config.dim, 2)
        self.pose_led_cls = nn.Parameter(torch.zeros(1, 1, self.config.dim))
        self.pose_led_modality_embedding = nn.Parameter(
            torch.randn(1, 2, self.config.dim) * 0.02
        )
        pose_led_layer = nn.TransformerEncoderLayer(
            self.config.dim,
            self.config.nhead,
            self.config.feedforward_dim,
            self.config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.pose_led_fusion = nn.TransformerEncoder(
            pose_led_layer, num_layers=self.config.pose_led_layers
        )
        self.pose_led_norm = nn.LayerNorm(self.config.dim)
        self.pose_led_head = nn.Linear(self.config.dim, 2)
        self.pose_led_phase_head = nn.Linear(self.config.dim, self.config.phase_classes)
        # Start at the validated world baseline; this head learns only a
        # signed two-class logit correction from Pose-led token fusion.
        nn.init.zeros_(self.pose_led_head.weight)
        nn.init.zeros_(self.pose_led_head.bias)
        nn.init.zeros_(self.pose_led_phase_head.weight)
        nn.init.zeros_(self.pose_led_phase_head.bias)
        self.pose_expert_gate = nn.Sequential(
            nn.Linear(self.config.dim * 2 + 10, self.config.dim // 2),
            nn.GELU(),
            nn.Dropout(self.config.dropout),
            nn.Linear(self.config.dim // 2, 1),
        )
        nn.init.zeros_(self.pose_expert_gate[-1].weight)
        nn.init.constant_(self.pose_expert_gate[-1].bias, float(self.config.pose_expert_gate_bias))
        self.short_pose_motion_encoder = None
        self.phase_motion_gate_adjuster = None
        self.short_pose_logit_delta = None
        if self.config.phase_motion_gate:
            self.short_pose_motion_encoder = ShortPoseMotionEncoder(self.config)
            if self.config.short_pose_expert_fusion:
                self.short_pose_logit_delta = TemporalWorldVisualHead._head(
                    self.config.dim, 2, self.config.dropout
                )
                nn.init.zeros_(self.short_pose_logit_delta[-1].weight)
                nn.init.zeros_(self.short_pose_logit_delta[-1].bias)
            # short embedding + long Pose phase + short phase + event logits
            # + observed quality + future uncertainty.
            gate_input_dim = (
                self.config.dim
                + self.config.phase_classes
                + self.config.phase_classes
                + 2
                + 4
                + 1
            )
            self.phase_motion_gate_adjuster = nn.Sequential(
                nn.Linear(gate_input_dim, self.config.dim // 2),
                nn.GELU(),
                nn.Dropout(self.config.dropout),
                nn.Linear(self.config.dim // 2, 1),
            )
            # The v5 branch starts exactly at the v4 gate.  This makes the
            # first optimization steps a controlled fine-tune rather than a
            # random change to the deployed decision boundary.
            nn.init.zeros_(self.phase_motion_gate_adjuster[-1].weight)
            nn.init.zeros_(self.phase_motion_gate_adjuster[-1].bias)
        self.reliability = nn.Sequential(
            nn.Linear(self.config.dim * 2 + 5, self.config.dim),
            nn.GELU(),
            nn.Dropout(self.config.dropout),
            nn.Linear(self.config.dim, 1),
        )
        self.rescue_head = nn.Sequential(
            nn.Linear(self.config.dim, self.config.dim // 2),
            nn.GELU(),
            nn.Linear(self.config.dim // 2, 1),
        )
        nn.init.zeros_(self.rescue_head[-1].weight)
        # Keep the initial correction small while avoiding the near-zero
        # softplus gradient produced by the earlier -4.0 initialization.
        nn.init.constant_(self.rescue_head[-1].bias, -1.5)
        if self.config.signed_pose_residual:
            self.negative_rescue_head = nn.Sequential(
                nn.Linear(self.config.dim, self.config.dim // 2),
                nn.GELU(),
                nn.Linear(self.config.dim // 2, 1),
            )
            nn.init.zeros_(self.negative_rescue_head[-1].weight)
            nn.init.constant_(self.negative_rescue_head[-1].bias, -1.5)
        nn.init.constant_(self.reliability[-1].bias, -1.0)
        self.domain_reliability_gate = None
        if self.config.domain_warning_gate:
            # [world embedding, pose/fused embedding, pose quality(4),
            # uncertainty, world logits(2), pose logits(2), crop logits(2),
            # crop gate] = dim*2 + 12 features.
            self.domain_reliability_gate = nn.Sequential(
                nn.Linear(self.config.dim * 2 + 12, self.config.dim),
                nn.GELU(),
                nn.Dropout(self.config.dropout),
                nn.Linear(self.config.dim, 1),
            )
            nn.init.zeros_(self.domain_reliability_gate[-1].weight)
            nn.init.constant_(self.domain_reliability_gate[-1].bias, float(self.config.domain_gate_bias))
        self.post_context = None
        self.controlled_action_head = None
        self.controlled_suppressor_head = None
        self.occlusion_rescue_head = None
        if self.config.post_residual_experts:
            # [world/fused embeddings, pose quality, uncertainty, expert
            # logits, crop gate, phase logits, short motion statistics].
            post_input_dim = self.config.dim * 2 + 34
            self.post_context = nn.Sequential(
                nn.Linear(post_input_dim, self.config.dim),
                nn.GELU(),
                nn.Dropout(self.config.dropout),
                nn.LayerNorm(self.config.dim),
            )
            self.controlled_action_head = TemporalWorldVisualHead._head(
                self.config.dim, 3, self.config.dropout
            )
            self.controlled_suppressor_head = TemporalWorldVisualHead._head(
                self.config.dim, 1, self.config.dropout
            )
            self.occlusion_rescue_head = TemporalWorldVisualHead._head(
                self.config.dim, 1, self.config.dropout
            )
            for head in (
                self.controlled_action_head,
                self.controlled_suppressor_head,
                self.occlusion_rescue_head,
            ):
                nn.init.zeros_(head[-1].weight)
                nn.init.zeros_(head[-1].bias)
            # Small but non-zero gradients, while keeping the initial model
            # numerically close to v2.  The bounded residuals cannot explode.
            nn.init.constant_(self.controlled_suppressor_head[-1].bias, -5.0)
            nn.init.constant_(self.occlusion_rescue_head[-1].bias, -5.0)

    @torch.no_grad()
    def update_pose_target(self, momentum: float = 0.996):
        for target, online in zip(self.pose_target_encoder.parameters(), self.pose_encoder.parameters()):
            target.mul_(momentum).add_(online, alpha=1.0 - momentum)

    def load_stable_visual(self, checkpoint_path: str, map_location="cpu"):
        checkpoint = torch.load(checkpoint_path, map_location=map_location, weights_only=False)
        source = checkpoint.get("model_state_dict", checkpoint)
        current = self.state_dict()
        compatible = {}
        visual_prefixes = ("visual_model.",)
        if self.config.use_full_visual_context:
            visual_prefixes += (
                "context_adapter.", "history_adapter.", "long_view_adapter.",
                "context_binary_delta.", "context_phase_delta.",
                "history_binary_delta.", "history_phase_delta.",
                "long_binary_delta.", "long_phase_delta.",
            )
        for key, value in source.items():
            if key.startswith(visual_prefixes) and key in current and current[key].shape == value.shape:
                compatible[key] = value
            elif key == "phase_binary_scale" and key in current and current[key].shape == value.shape:
                compatible[key] = value
        missing, unexpected = self.load_state_dict(compatible, strict=False)
        return compatible, missing, unexpected

    def freeze_world_baseline(self):
        for parameter in self.visual_model.parameters():
            parameter.requires_grad = False
        self.phase_binary_scale.requires_grad = False
        # The old best checkpoint includes these adapters.  They form part of
        # the frozen visual baseline, not part of the new Pose correction.
        if self.config.use_full_visual_context:
            for module in (
                self.context_adapter,
                self.history_adapter,
                self.long_view_adapter,
                self.context_binary_delta,
                self.context_phase_delta,
                self.history_binary_delta,
                self.history_phase_delta,
                self.long_binary_delta,
                self.long_phase_delta,
            ):
                for parameter in module.parameters():
                    parameter.requires_grad = False

    def world_logits(
        self,
        world_tokens: torch.Tensor,
        context_tokens: torch.Tensor | None = None,
        context_valid: torch.Tensor | None = None,
        long_view_tokens: torch.Tensor | None = None,
        long_view_valid: torch.Tensor | None = None,
        crop_world_tokens: torch.Tensor | None = None,
    ):
        embedding, raw_logits, phase_logits = self.visual_model(world_tokens.float())
        base_embedding = embedding
        base_logits = raw_logits
        crop_info = {
            "crop_embedding": None,
            "crop_logits": None,
            "crop_phase_logits": None,
            "crop_gate": torch.zeros(world_tokens.size(0), device=world_tokens.device),
        }
        if self.config.use_full_visual_context and context_tokens is not None:
            history_embeddings = context_tokens.float().mean(dim=2)
            if context_valid is None:
                history_valid = torch.ones(history_embeddings.shape[:2], device=history_embeddings.device, dtype=history_embeddings.dtype)
            else:
                history_valid = context_valid.to(history_embeddings).reshape(history_embeddings.shape[:2])
            # Preserve the old best model's exact ordering: both context and
            # history deltas are computed from the original visual embedding,
            # then applied before the optional long-view branch.
            latest_embedding = history_embeddings[:, -1]
            latest_valid = history_valid[:, -1]
            history_weight = history_valid / history_valid.sum(dim=1, keepdim=True).clamp_min(1.0)
            history_embedding = (history_embeddings * history_weight.unsqueeze(-1)).sum(dim=1)
            original_embedding = embedding
            history_delta = self.history_adapter(torch.cat((original_embedding, history_embedding), dim=1))
            history_delta = history_delta * history_valid.any(dim=1).to(history_delta).view(-1, 1)
            context_delta = self.context_adapter(torch.cat((original_embedding, latest_embedding), dim=1))
            context_delta = context_delta * latest_valid.to(context_delta).view(-1, 1)
            embedding = embedding + context_delta
            raw_logits = raw_logits + self.context_binary_delta(context_delta)
            phase_logits = phase_logits + self.context_phase_delta(context_delta)
            embedding = embedding + history_delta
            raw_logits = raw_logits + self.history_binary_delta(history_delta)
            phase_logits = phase_logits + self.history_phase_delta(history_delta)
        if self.config.use_full_visual_context and long_view_tokens is not None:
            long_embedding, _, _ = self.visual_model(long_view_tokens.float())
            long_delta = self.long_view_adapter(torch.cat((embedding, long_embedding), dim=1))
            if long_view_valid is not None:
                long_delta = long_delta * long_view_valid.to(long_delta).view(-1, 1)
            embedding = embedding + long_delta
            raw_logits = raw_logits + self.long_binary_delta(long_delta)
            phase_logits = phase_logits + self.long_phase_delta(long_delta)

        if self.config.use_crop_tokens:
            if crop_world_tokens is None:
                raise ValueError("use_crop_tokens=True requires crop_world_tokens")
            crop_tokens = crop_world_tokens.float()
            crop_embedding, crop_logits, crop_phase_logits = self.crop_visual_model(crop_tokens)
            # Full-frame queries attend to person-centered keys/values.  This
            # preserves spatially local evidence while retaining the global
            # world-model representation as the primary stream.
            full_tokens = world_tokens.float()
            crop_delta, _ = self.crop_cross_attention(
                self.crop_cross_norm(full_tokens),
                self.crop_cross_norm(crop_tokens),
                self.crop_cross_norm(crop_tokens),
                need_weights=False,
            )
            cross_embedding = self.crop_cross_norm(full_tokens + crop_delta).mean(dim=1)
            crop_embedding = self.crop_embedding_fusion(
                torch.cat((crop_embedding, cross_embedding), dim=1)
            )
            gate_features = torch.cat(
                (
                    base_embedding,
                    crop_embedding,
                    torch.abs(base_embedding - crop_embedding),
                    base_embedding * crop_embedding,
                ),
                dim=1,
            )
            crop_gate = torch.sigmoid(self.crop_gate(gate_features)).squeeze(1)
            raw_logits = raw_logits + crop_gate.unsqueeze(1) * self.crop_binary_delta(crop_embedding)
            phase_logits = phase_logits + crop_gate.unsqueeze(1) * self.crop_phase_delta(crop_embedding)
            # Zero-initialized residual keeps the first forward pass exactly
            # at the previous World-Pose representation.
            embedding = embedding + crop_gate.unsqueeze(1) * self.crop_embedding_delta(crop_embedding)
            crop_info = {
                "crop_embedding": crop_embedding,
                "crop_logits": crop_logits,
                "crop_phase_logits": crop_phase_logits,
                "crop_gate": crop_gate,
            }
        phase_log_probs = F.log_softmax(phase_logits, dim=1)
        positive = torch.logsumexp(phase_log_probs[:, 1:3], dim=1)
        negative = torch.logsumexp(torch.cat((phase_log_probs[:, :1], phase_log_probs[:, 3:]), dim=1), dim=1)
        phase_binary = torch.stack((negative, positive), dim=1)
        logits = raw_logits + torch.sigmoid(self.phase_binary_scale) * phase_binary
        crop_info["base_embedding"] = base_embedding
        crop_info["base_logits"] = base_logits
        crop_info["enhanced_logits"] = logits
        return embedding, logits, phase_logits, crop_info

    def forward(
        self,
        world_tokens: torch.Tensor,
        world_current_temporal: torch.Tensor,
        world_future_temporal: torch.Tensor,
        pose_context: torch.Tensor,
        bbox_context: torch.Tensor,
        pose_future: torch.Tensor | None = None,
        bbox_future: torch.Tensor | None = None,
        context_tokens: torch.Tensor | None = None,
        context_valid: torch.Tensor | None = None,
        long_view_tokens: torch.Tensor | None = None,
        long_view_valid: torch.Tensor | None = None,
        crop_world_tokens: torch.Tensor | None = None,
    ):
        world_result = self.world_logits(
            world_tokens,
            context_tokens=context_tokens,
            context_valid=context_valid,
            long_view_tokens=long_view_tokens,
            long_view_valid=long_view_valid,
            crop_world_tokens=crop_world_tokens,
        )
        world_embedding, world_logits, world_phase_logits, crop_info = world_result
        pose_context_tokens, pose_quality = self.pose_encoder(pose_context, bbox_context, role=0)
        prediction = self.pose_predictor(pose_context_tokens, world_future_temporal.float())
        predicted_pose = prediction["pose_future_tokens"]
        short_pose_embedding = None
        short_pose_event_logits = None
        short_pose_phase_logits = None
        phase_motion_gate_delta = torch.zeros(
            world_tokens.size(0), device=world_tokens.device, dtype=world_tokens.dtype
        )
        if self.config.phase_motion_gate:
            short_pose_embedding, short_pose_event_logits, short_pose_phase_logits = (
                self.short_pose_motion_encoder(
                    pose_context,
                    bbox_context,
                    predicted_pose,
                )
            )
        target_pose = None
        if pose_future is not None and bbox_future is not None:
            with torch.no_grad():
                target_pose, _ = self.pose_target_encoder(pose_future, bbox_future, role=1)

        world_sequence = torch.cat(
            (self.current_world_proj(world_current_temporal.float()), self.future_world_proj(world_future_temporal.float())),
            dim=1,
        )
        pose_sequence = self.body_pool(torch.cat((pose_context_tokens, predicted_pose), dim=1))
        world_delta, _ = self.world_to_pose(world_sequence, pose_sequence, pose_sequence, need_weights=False)
        pose_delta, _ = self.pose_to_world(pose_sequence, world_sequence, world_sequence, need_weights=False)
        world_sequence = self.world_norm(world_sequence + world_delta)
        pose_sequence = self.pose_norm(pose_sequence + pose_delta)
        cls = self.cls_token.expand(world_tokens.size(0), -1, -1)
        fused = self.fusion_norm(self.fusion(torch.cat((cls, world_sequence, pose_sequence), dim=1)))[:, 0]
        pose_logits = self.pose_head(fused)
        pose_phase_logits = self.pose_phase_head(fused)
        pose_now_embedding = None
        pose_now_logits = None
        pose_now_phase_logits = None
        if self.observed_pose_confirm_head is not None:
            (
                pose_now_embedding,
                pose_now_logits,
                pose_now_phase_logits,
            ) = self.observed_pose_confirm_head(pose_context_tokens)
        future_pose_led_embedding = None
        future_pose_led_logits = None
        future_pose_led_phase_logits = None
        if self.future_pose_risk_head is not None:
            (
                future_pose_led_embedding,
                future_pose_led_logits,
                future_pose_led_phase_logits,
            ) = self.future_pose_risk_head(
                prediction["pose_future_coordinates"],
                prediction["pose_future_visibility_logits"],
                prediction["pose_future_log_sigma"],
                prediction["bbox_future"],
                pose_context,
                bbox_context,
                predicted_pose,
            )
        short_pose_expert_logits = pose_logits
        short_pose_logit_delta = torch.zeros_like(pose_logits)
        if self.short_pose_logit_delta is not None:
            short_pose_logit_delta = self.short_pose_logit_delta(short_pose_embedding)
            # The zero-initialized residual makes this expert identical to
            # the validated long-horizon Pose expert at initialization.
            short_pose_expert_logits = pose_logits + short_pose_logit_delta
        uncertainty = prediction["pose_future_log_sigma"].exp().mean(dim=(1, 2, 3), keepdim=False).unsqueeze(1)
        reliability_features = torch.cat((world_embedding, fused, pose_quality, uncertainty), dim=1)
        learned_reliability = torch.sigmoid(self.reliability(reliability_features)).squeeze(1)
        observed_quality = (pose_quality[:, 0] * pose_quality[:, 1] * pose_quality[:, 2] * pose_quality[:, 3]).clamp(0.0, 1.0)
        pose_reliability = observed_quality * learned_reliability
        positive_rescue = F.softplus(self.rescue_head(fused)).squeeze(1)
        if self.config.signed_pose_residual:
            negative_rescue = F.softplus(self.negative_rescue_head(fused)).squeeze(1)
            rescue_delta = (positive_rescue - negative_rescue).clamp(
                -float(self.config.pose_residual_limit),
                float(self.config.pose_residual_limit),
            )
        else:
            negative_rescue = torch.zeros_like(positive_rescue)
            rescue_delta = positive_rescue
        world_logodds = world_logits[:, 1] - world_logits[:, 0]
        final_logodds = world_logodds + pose_reliability * rescue_delta
        rescue_logits = torch.stack((torch.zeros_like(final_logodds), final_logodds), dim=1)
        pose_logodds = pose_logits[:, 1] - pose_logits[:, 0]
        domain_reliability = torch.full_like(world_logodds, 0.5)
        warning_logits = torch.stack((torch.zeros_like(world_logodds), world_logodds), dim=1)
        if self.domain_reliability_gate is not None:
            crop_logits_for_gate = crop_info["crop_logits"]
            if crop_logits_for_gate is None:
                crop_logits_for_gate = torch.zeros_like(world_logits)
            domain_features = torch.cat(
                (
                    world_embedding,
                    fused,
                    pose_quality,
                    uncertainty,
                    world_logits,
                    pose_logits,
                    crop_logits_for_gate,
                    crop_info["crop_gate"].unsqueeze(1),
                ),
                dim=1,
            )
            domain_reliability = torch.sigmoid(
                self.domain_reliability_gate(domain_features)
            ).squeeze(1)
            # g=1 trusts the World warning; g=0 falls back to the observed
            # Pose expert. This branch is separate from the v4 final head.
            warning_logodds = (
                domain_reliability * world_logodds
                + (1.0 - domain_reliability) * pose_logodds
            )
            warning_logits = torch.stack(
                (torch.zeros_like(warning_logodds), warning_logodds), dim=1
            )
        gate_features = torch.cat(
            (
                world_embedding,
                fused,
                pose_quality,
                uncertainty,
                world_logits,
                pose_logits,
                (pose_logodds - world_logodds).unsqueeze(1),
            ),
            dim=1,
        )
        pose_expert_gate = torch.sigmoid(self.pose_expert_gate(gate_features)).squeeze(1)
        if self.config.phase_motion_gate:
            phase_motion_features = torch.cat(
                (
                    short_pose_embedding,
                    pose_phase_logits,
                    short_pose_phase_logits,
                    short_pose_event_logits,
                    pose_quality,
                    uncertainty,
                ),
                dim=1,
            )
            # A bounded signed correction lets short, decisive motion raise
            # the Pose expert weight, while recovery/lying evidence can lower
            # it.  The zero initialization above preserves v4 at startup.
            raw_delta = self.phase_motion_gate_adjuster(phase_motion_features).squeeze(1)
            phase_motion_gate_delta = 1.5 * torch.tanh(raw_delta)
            base_logit = torch.logit(pose_expert_gate.clamp(1e-4, 1.0 - 1e-4))
            pose_expert_gate = torch.sigmoid(base_logit + phase_motion_gate_delta)
        pose_led_embedding = None
        pose_led_logits = None
        pose_led_phase_logits = None
        if self.config.dual_pose_heads:
            # In the dual-head experiment ``final_logits`` means current Pose
            # confirmation.  The future warning is exposed independently.
            final_logits = pose_now_logits
        elif self.config.future_pose_led:
            # This is the causal Pose-led path.  Its inputs are the
            # predictor outputs above, never the ground-truth future Pose.
            final_logits = future_pose_led_logits
        elif self.config.pose_expert_fusion:
            # Learned expert selection in logit space.  The gate is trained
            # from which expert has lower supervised NLL, so reliable Pose can
            # take over when the world stream is an OOD failure.
            final_logodds = (
                (1.0 - pose_expert_gate) * world_logodds
                + pose_expert_gate * (
                    short_pose_expert_logits[:, 1] - short_pose_expert_logits[:, 0]
                )
            )
            final_logits = torch.stack(
                (torch.zeros_like(final_logodds), final_logodds), dim=1
            )
        elif self.config.pose_led_visual:
            # Pose comes first and receives its own modality identity.  The
            # future world part is predicted by JEPA and remains causal.
            pose_tokens = pose_sequence + self.pose_led_modality_embedding[:, 0:1]
            world_tokens_led = world_sequence + self.pose_led_modality_embedding[:, 1:2]
            led_cls = self.pose_led_cls.expand(world_tokens_led.size(0), -1, -1)
            led_input = torch.cat((led_cls, pose_tokens, world_tokens_led), dim=1)
            led_encoded = self.pose_led_fusion(led_input)
            # Make Pose a structural part of the classifier input, rather
            # than relying only on attention to discover that priority.  The
            # CLS token still receives JEPA world context through the encoder.
            pose_summary = led_encoded[:, 1:1 + pose_tokens.size(1)].mean(dim=1)
            pose_led_embedding = self.pose_led_norm(led_encoded[:, 0] + 0.5 * pose_summary)
            pose_led_logits = world_logits + self.pose_led_head(pose_led_embedding)
            pose_led_phase_logits = self.pose_led_phase_head(pose_led_embedding)
            final_logits = pose_led_logits
        else:
            final_logits = rescue_logits
        v2_final_logits = final_logits
        controlled_action_logits = None
        controlled_suppression = torch.zeros_like(world_logodds)
        occlusion_rescue = torch.zeros_like(world_logodds)
        post_embedding = None
        if self.config.post_residual_experts:
            xy = pose_context[..., :2].float()
            bbox_xy = bbox_context[..., :2].float()
            xy_delta = torch.cat((torch.zeros_like(xy[:, :1]), xy[:, 1:] - xy[:, :-1]), dim=1)
            box_delta = torch.cat((torch.zeros_like(bbox_xy[:, :1]), bbox_xy[:, 1:] - bbox_xy[:, :-1]), dim=1)
            motion_features = torch.cat(
                (
                    xy_delta.abs().mean(dim=(1, 2)),
                    xy_delta[:, -1].abs().mean(dim=1),
                    box_delta.abs().mean(dim=1),
                    box_delta[:, -1].abs(),
                ),
                dim=1,
            )
            post_features = torch.cat(
                (
                    world_embedding,
                    fused,
                    pose_quality,
                    uncertainty,
                    world_logits,
                    pose_logits,
                    crop_info["crop_gate"].unsqueeze(1),
                    pose_phase_logits,
                    motion_features,
                ),
                dim=1,
            )
            post_embedding = self.post_context(post_features)
            controlled_action_logits = self.controlled_action_head(post_embedding)
            controlled_suppression = (
                float(self.config.controlled_suppress_limit)
                * torch.sigmoid(self.controlled_suppressor_head(post_embedding).squeeze(1))
            )
            occlusion_rescue = (
                float(self.config.occlusion_rescue_limit)
                * torch.sigmoid(self.occlusion_rescue_head(post_embedding).squeeze(1))
            )
            base_logodds = v2_final_logits[:, 1] - v2_final_logits[:, 0]
            safe_logodds = base_logodds - controlled_suppression + occlusion_rescue
            final_logits = torch.stack((torch.zeros_like(safe_logodds), safe_logodds), dim=1)
        return {
            "world_embedding": world_embedding,
            "pose_fused_embedding": fused,
            "world_logits": world_logits,
            "world_base_logits": crop_info["base_logits"],
            "crop_logits": crop_info["crop_logits"],
            "crop_phase_logits": crop_info["crop_phase_logits"],
            "crop_gate": crop_info["crop_gate"],
            "world_phase_logits": world_phase_logits,
            "pose_logits": pose_logits,
            "short_pose_expert_logits": short_pose_expert_logits,
            "short_pose_logit_delta": short_pose_logit_delta,
            "pose_phase_logits": pose_phase_logits,
            "final_logits": final_logits,
            "final_probs": F.softmax(final_logits, dim=1),
            "v2_final_logits": v2_final_logits,
            "post_embedding": post_embedding,
            "controlled_action_logits": controlled_action_logits,
            "controlled_suppression": controlled_suppression,
            "occlusion_rescue": occlusion_rescue,
            "post_residual_enabled": self.config.post_residual_experts,
            "pose_reliability": pose_reliability,
            "learned_pose_reliability": learned_reliability,
            "pose_quality": pose_quality,
            "pose_rescue": rescue_delta,
            "pose_positive_rescue": positive_rescue,
            "pose_negative_rescue": negative_rescue,
            "pose_rescue_delta": rescue_delta,
            "pose_rescue_effect": pose_reliability * rescue_delta,
            "pose_residual_limit": float(self.config.pose_residual_limit),
            "rescue_logits": rescue_logits,
            "pose_led_embedding": pose_led_embedding,
            "pose_led_logits": pose_led_logits,
            "pose_led_phase_logits": pose_led_phase_logits,
            "pose_expert_gate": pose_expert_gate,
            "domain_reliability": domain_reliability,
            "warning_logits": warning_logits,
            "warning_probs": F.softmax(warning_logits, dim=1),
            "domain_warning_gate_enabled": self.domain_reliability_gate is not None,
            "short_pose_embedding": short_pose_embedding,
            "short_pose_event_logits": short_pose_event_logits,
            "short_pose_phase_logits": short_pose_phase_logits,
            "future_pose_led_embedding": future_pose_led_embedding,
            "future_pose_led_logits": future_pose_led_logits,
            "future_pose_led_phase_logits": future_pose_led_phase_logits,
            "pose_now_embedding": pose_now_embedding,
            "pose_now_logits": pose_now_logits,
            "pose_now_phase_logits": pose_now_phase_logits,
            "phase_motion_gate_delta": phase_motion_gate_delta,
            "pose_context_tokens": pose_context_tokens,
            "pose_future_tokens": predicted_pose,
            "pose_future_target_tokens": target_pose,
            **prediction,
        }
