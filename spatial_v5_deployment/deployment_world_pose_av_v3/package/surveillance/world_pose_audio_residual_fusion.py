"""Audio-conditioned residual correction for a frozen World-Pose visual model."""

from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F

from surveillance.audio_fall_transformer import AudioFallTransformer, AudioTransformerConfig
from surveillance.world_pose_future_fusion import WorldPoseFutureConfig, WorldPoseVisualFusion


@dataclass
class WorldPoseAudioResidualConfig:
    dim: int = 256
    nhead: int = 8
    dropout: float = 0.15
    residual_limit: float = 2.0


class WorldPoseAudioResidualFusion(nn.Module):
    """Keep a calibrated visual logit and let audio make a bounded correction."""

    def __init__(
        self,
        visual_config: WorldPoseFutureConfig,
        config: WorldPoseAudioResidualConfig | None = None,
        freeze_visual: bool = True,
        freeze_audio: bool = True,
    ):
        super().__init__()
        self.config = config or WorldPoseAudioResidualConfig()
        self.visual = WorldPoseVisualFusion(visual_config)
        self.audio = AudioFallTransformer(AudioTransformerConfig())
        self.audio_proj = nn.Linear(self.audio.config.d_model, self.config.dim)
        self.audio_norm = nn.LayerNorm(self.config.dim)
        self.visual_norm = nn.LayerNorm(self.config.dim)
        self.visual_to_audio = nn.MultiheadAttention(
            self.config.dim, self.config.nhead, self.config.dropout, batch_first=True
        )
        self.cross_norm = nn.LayerNorm(self.config.dim)
        self.match_head = nn.Sequential(
            nn.Linear(self.config.dim * 4, self.config.dim), nn.GELU(), nn.Dropout(self.config.dropout),
            nn.Linear(self.config.dim, 1),
        )
        self.delta_head = nn.Sequential(
            nn.Linear(self.config.dim * 4 + 6, self.config.dim), nn.GELU(), nn.Dropout(self.config.dropout),
            nn.Linear(self.config.dim, 1),
        )
        self.utility_gate = nn.Sequential(
            nn.Linear(self.config.dim * 4 + 8, self.config.dim), nn.GELU(), nn.Dropout(self.config.dropout),
            nn.Linear(self.config.dim, self.config.dim // 2), nn.GELU(), nn.Linear(self.config.dim // 2, 1),
        )
        nn.init.zeros_(self.delta_head[-1].weight)
        nn.init.zeros_(self.delta_head[-1].bias)
        nn.init.zeros_(self.utility_gate[-1].weight)
        nn.init.constant_(self.utility_gate[-1].bias, -3.0)
        self.pose_verifier = None
        if freeze_visual:
            self.freeze_visual()
        if freeze_audio:
            self.freeze_audio()

    @staticmethod
    def audio_quality(audio_mel: torch.Tensor) -> torch.Tensor:
        x = audio_mel.float()
        flat = x.flatten(start_dim=1)
        mean = flat.mean(dim=1)
        std = flat.std(dim=1, unbiased=False)
        peak = flat.abs().amax(dim=1)
        nonzero = (flat.abs() > 1e-4).float().mean(dim=1)
        energy = x.mean(dim=2).squeeze(1)
        temporal_std = energy.std(dim=1, unbiased=False)
        temporal_peak = energy.abs().amax(dim=1)
        return torch.stack((mean, std, peak, nonzero, temporal_std, temporal_peak), dim=1)

    def freeze_visual(self):
        for parameter in self.visual.parameters():
            parameter.requires_grad = False
        self.visual.eval()

    def freeze_audio(self):
        for parameter in self.audio.parameters():
            parameter.requires_grad = False
        self.audio.eval()

    def train(self, mode: bool = True):
        super().train(mode)
        if self.pose_verifier is not None:
            self.pose_verifier.eval()
        return self

    def train(self, mode: bool = True):
        super().train(mode)
        if not any(parameter.requires_grad for parameter in self.visual.parameters()):
            self.visual.eval()
        if not any(parameter.requires_grad for parameter in self.audio.parameters()):
            self.audio.eval()
        return self

    def load_visual_checkpoint(self, path: str, map_location="cpu"):
        checkpoint = torch.load(path, map_location=map_location, weights_only=False)
        self.visual.load_state_dict(checkpoint["model_state_dict"], strict=True)
        return checkpoint

    def load_audio_from_av_checkpoint(self, path: str, map_location="cpu"):
        checkpoint = torch.load(path, map_location=map_location, weights_only=False)
        source = checkpoint.get("model_state_dict", checkpoint)
        own = self.state_dict()
        compatible = {}
        for key, value in source.items():
            if key.startswith("audio."):
                target = key
            elif key.startswith("audio_model."):
                target = "audio." + key.removeprefix("audio_model.")
            elif key.startswith("audio_proj."):
                target = key
            else:
                continue
            if target in own and own[target].shape == value.shape:
                compatible[target] = value
        self.load_state_dict(compatible, strict=False)
        return len(compatible)

    def load_pose_verifier(self, path: str, map_location="cpu"):
        from surveillance.train_pose_evidence_verifier import PoseEvidenceVerifier

        checkpoint = torch.load(path, map_location=map_location, weights_only=False)
        verifier = PoseEvidenceVerifier(len(checkpoint["feature_names"]))
        verifier.load_state_dict(checkpoint["verifier_state_dict"], strict=True)
        self.pose_verifier = verifier.to(next(self.parameters()).device).eval()
        for parameter in self.pose_verifier.parameters():
            parameter.requires_grad = False
        return checkpoint

    def forward(
        self,
        world_tokens: torch.Tensor,
        world_current_temporal: torch.Tensor,
        world_future_temporal: torch.Tensor,
        pose_context: torch.Tensor,
        bbox_context: torch.Tensor,
        pose_future: torch.Tensor | None,
        bbox_future: torch.Tensor | None,
        audio_mel: torch.Tensor,
        audio_valid: torch.Tensor,
        context_tokens: torch.Tensor | None = None,
        context_valid: torch.Tensor | None = None,
        long_view_tokens: torch.Tensor | None = None,
        long_view_valid: torch.Tensor | None = None,
        crop_world_tokens: torch.Tensor | None = None,
    ):
        visual_outputs = self.visual(
            world_tokens, world_current_temporal, world_future_temporal, pose_context, bbox_context,
            pose_future, bbox_future, context_tokens, context_valid, long_view_tokens, long_view_valid,
            crop_world_tokens,
        )
        visual_logit_raw = visual_outputs["final_logits"][:, 1] - visual_outputs["final_logits"][:, 0]
        visual_logit = visual_logit_raw
        pose_verifier_gate = torch.zeros_like(visual_logit)
        if self.pose_verifier is not None:
            world_logit = visual_outputs["world_logits"][:, 1] - visual_outputs["world_logits"][:, 0]
            pose_logit = visual_outputs["pose_logits"][:, 1] - visual_outputs["pose_logits"][:, 0]
            quality = visual_outputs["pose_quality"].mean(dim=1)
            uncertainty = visual_outputs["pose_future_log_sigma"].exp().mean(dim=(1, 2, 3))
            verifier_features = torch.stack((
                world_logit, pose_logit, visual_logit_raw,
                visual_outputs["pose_reliability"], quality, uncertainty,
                visual_outputs["pose_rescue"],
            ), dim=1)
            visual_logit, pose_verifier_gate = self.pose_verifier(verifier_features)
        with torch.set_grad_enabled(any(parameter.requires_grad for parameter in self.audio.parameters())):
            full_audio = self.audio.extract_tokens(audio_mel.float())
        audio_tokens = self.audio_norm(self.audio_proj(full_audio[:, 1:, :]))
        visual_token = self.visual_norm(
            visual_outputs["world_embedding"] + visual_outputs["pose_fused_embedding"]
        ).unsqueeze(1)
        attended, _ = self.visual_to_audio(visual_token, audio_tokens, audio_tokens, need_weights=False)
        audio_evidence = self.cross_norm(visual_token + attended).squeeze(1)
        visual_embedding = visual_token.squeeze(1)
        quality = self.audio_quality(audio_mel)
        match_features = torch.cat(
            (visual_embedding, audio_evidence, torch.abs(visual_embedding - audio_evidence), visual_embedding * audio_evidence), dim=1
        )
        synchrony = torch.sigmoid(self.match_head(match_features)).squeeze(1)
        residual_features = torch.cat((match_features, quality), dim=1)
        raw_delta = float(self.config.residual_limit) * torch.tanh(self.delta_head(residual_features).squeeze(1))
        gate_features = torch.cat(
            (residual_features, synchrony.unsqueeze(1), torch.sigmoid(visual_logit).unsqueeze(1)), dim=1
        )
        learned_gate = torch.sigmoid(self.utility_gate(gate_features)).squeeze(1)
        valid = audio_valid.float().clamp(0.0, 1.0)
        gate = learned_gate * valid
        final_logit = visual_logit + gate * raw_delta
        final_logits = torch.stack((torch.zeros_like(final_logit), final_logit), dim=1)
        candidate_logits = torch.stack((torch.zeros_like(visual_logit), visual_logit + raw_delta), dim=1)
        return {
            **visual_outputs,
            "visual_logit": visual_logit,
            "visual_logit_raw": visual_logit_raw,
            "pose_verifier_gate": pose_verifier_gate,
            "audio_tokens": audio_tokens,
            "audio_evidence": audio_evidence,
            "synchrony": synchrony,
            "raw_delta": raw_delta,
            "utility_gate": gate,
            "learned_utility_gate": learned_gate,
            "audio_valid": valid,
            "candidate_logits": candidate_logits,
            "final_logits": final_logits,
            "final_probs": F.softmax(final_logits, dim=1),
        }
