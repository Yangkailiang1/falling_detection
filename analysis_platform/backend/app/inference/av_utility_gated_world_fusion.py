"""Utility-aware gated AV fusion over cached V-JEPA2 world tokens.

The module is intentionally separate from the earlier gated fusion model.  It
uses a learned audio-video match score before cross-attention and learns the
gate from the observed classification utility of AV relative to the V-JEPA
visual branch during training.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F

from app.inference.audio_fall_transformer import AudioFallTransformer, AudioTransformerConfig


@dataclass
class UtilityGatedWorldConfig:
    dim: int = 256
    nhead: int = 8
    dropout: float = 0.15
    visual_layers: int = 2
    fusion_layers: int = 2
    feedforward_dim: int = 512
    num_classes: int = 2
    num_phase_classes: int = 16
    gate_hidden_dim: int = 256
    event_gated_audio: bool = False
    # Ablation switch: when disabled, audio tokens always enter bidirectional
    # cross-attention and the fusion encoder. The final reliability fallback
    # remains available, so this isolates token-level gating from output-level
    # expert selection.
    cross_attention_gate: bool = True


class TemporalWorldVisualHead(nn.Module):
    """Attention pooling over all V-JEPA world tokens, not mean pooling."""

    def __init__(self, config: UtilityGatedWorldConfig):
        super().__init__()
        self.config = config
        self.cls_token = nn.Parameter(torch.zeros(1, 1, config.dim))
        self.position = nn.Parameter(torch.randn(1, 49, config.dim) * 0.02)
        layer = nn.TransformerEncoderLayer(
            d_model=config.dim,
            nhead=config.nhead,
            dim_feedforward=config.feedforward_dim,
            dropout=config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=config.visual_layers)
        self.norm = nn.LayerNorm(config.dim)
        self.binary_head = self._head(config.dim, config.num_classes, config.dropout)
        self.phase_head = self._head(config.dim, config.num_phase_classes, config.dropout)
        # Shared phase prototypes let the training loss align the same action
        # across cameras and subjects, instead of relying on a source-specific
        # binary boundary.
        self.phase_prototypes = nn.Parameter(torch.randn(config.num_phase_classes, config.dim) * 0.02)

    @staticmethod
    def _head(dim: int, classes: int, dropout: float) -> nn.Sequential:
        return nn.Sequential(
            nn.Linear(dim, dim // 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(dim // 2, classes),
        )

    def forward(self, tokens: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        cls = self.cls_token.expand(tokens.size(0), -1, -1)
        x = torch.cat([cls, tokens], dim=1)
        x = x + self.position[:, : x.size(1)]
        x = self.norm(self.encoder(x))
        embedding = x[:, 0]
        return embedding, self.binary_head(embedding), self.phase_head(embedding)


class ReliabilityCrossAttentionBlock(nn.Module):
    """Bidirectional cross-attention whose cross-modal residual is gated."""

    def __init__(self, config: UtilityGatedWorldConfig):
        super().__init__()
        dim = config.dim
        self.visual_to_audio = nn.MultiheadAttention(dim, config.nhead, dropout=config.dropout, batch_first=True)
        self.audio_to_visual = nn.MultiheadAttention(dim, config.nhead, dropout=config.dropout, batch_first=True)
        self.visual_norm1 = nn.LayerNorm(dim)
        self.audio_norm1 = nn.LayerNorm(dim)
        self.visual_norm2 = nn.LayerNorm(dim)
        self.audio_norm2 = nn.LayerNorm(dim)
        self.visual_ffn = nn.Sequential(nn.Linear(dim, dim * 2), nn.GELU(), nn.Dropout(config.dropout), nn.Linear(dim * 2, dim))
        self.audio_ffn = nn.Sequential(nn.Linear(dim, dim * 2), nn.GELU(), nn.Dropout(config.dropout), nn.Linear(dim * 2, dim))
        self.dropout = nn.Dropout(config.dropout)

    def forward(self, visual_tokens: torch.Tensor, audio_tokens: torch.Tensor, gate: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        scale = gate.view(-1, 1, 1)
        v = self.visual_norm1(visual_tokens)
        a = self.audio_norm1(audio_tokens)
        v_attn, _ = self.visual_to_audio(v, a, a, need_weights=False)
        a_attn, _ = self.audio_to_visual(a, v, v, need_weights=False)
        visual_tokens = visual_tokens + scale * self.dropout(v_attn)
        audio_tokens = audio_tokens + scale * self.dropout(a_attn)
        visual_tokens = visual_tokens + self.dropout(self.visual_ffn(self.visual_norm2(visual_tokens)))
        audio_tokens = audio_tokens + scale * self.dropout(self.audio_ffn(self.audio_norm2(audio_tokens)))
        return visual_tokens, audio_tokens


class AudioVisualUtilityGatedWorldFusion(nn.Module):
    """V-JEPA world-token AV fusion with learned synchrony and utility gate."""

    def __init__(self, audio_config: AudioTransformerConfig | None = None, config: UtilityGatedWorldConfig | None = None, freeze_audio_backbone: bool = True):
        super().__init__()
        self.audio_config = audio_config or AudioTransformerConfig()
        self.config = config or UtilityGatedWorldConfig()
        self.freeze_audio_backbone = freeze_audio_backbone
        self.visual_model = TemporalWorldVisualHead(self.config)
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
        self.context_binary_delta = nn.Linear(self.config.dim, self.config.num_classes)
        self.context_phase_delta = nn.Linear(self.config.dim, self.config.num_phase_classes)
        self.history_binary_delta = nn.Linear(self.config.dim, self.config.num_classes)
        self.history_phase_delta = nn.Linear(self.config.dim, self.config.num_phase_classes)
        self.long_binary_delta = nn.Linear(self.config.dim, self.config.num_classes)
        self.long_phase_delta = nn.Linear(self.config.dim, self.config.num_phase_classes)
        # Starts close to the already-validated binary visual head and learns
        # how much the phase taxonomy should alter the binary boundary.
        self.phase_binary_scale = nn.Parameter(torch.tensor(-3.0))
        self.audio_model = AudioFallTransformer(self.audio_config)
        if freeze_audio_backbone:
            self.audio_model.eval()
            for parameter in self.audio_model.parameters():
                parameter.requires_grad = False

        dim = self.config.dim
        self.audio_proj = nn.Linear(self.audio_config.d_model, dim)
        self.audio_head = TemporalWorldVisualHead._head(dim, self.config.num_classes, self.config.dropout)
        self.match_net = nn.Sequential(
            nn.Linear(dim * 4, dim),
            nn.GELU(),
            nn.Dropout(self.config.dropout),
            nn.Linear(dim, dim // 2),
            nn.GELU(),
            nn.Linear(dim // 2, 1),
        )
        self.gate = nn.Sequential(
            nn.Linear(dim * 2 + 6 + 3, self.config.gate_hidden_dim),
            nn.GELU(),
            nn.Dropout(self.config.dropout),
            nn.Linear(self.config.gate_hidden_dim, self.config.gate_hidden_dim // 2),
            nn.GELU(),
            nn.Linear(self.config.gate_hidden_dim // 2, 1),
        )
        self.cross_blocks = nn.ModuleList(ReliabilityCrossAttentionBlock(self.config) for _ in range(self.config.fusion_layers))
        self.fusion_cls = nn.Parameter(torch.zeros(1, 1, dim))
        self.modality_embedding = nn.Parameter(torch.randn(1, 3, dim) * 0.02)
        layer = nn.TransformerEncoderLayer(
            d_model=dim,
            nhead=self.config.nhead,
            dim_feedforward=self.config.feedforward_dim,
            dropout=self.config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.fusion_encoder = nn.TransformerEncoder(layer, num_layers=self.config.fusion_layers)
        self.fusion_norm = nn.LayerNorm(dim)
        self.av_head = TemporalWorldVisualHead._head(dim, self.config.num_classes, self.config.dropout)
        self.selector = nn.Sequential(
            nn.Linear(dim * 2 + 4, dim),
            nn.GELU(),
            nn.Dropout(self.config.dropout),
            nn.Linear(dim, dim // 2),
            nn.GELU(),
            nn.Linear(dim // 2, 1),
        )
        nn.init.zeros_(self.context_binary_delta.weight)
        nn.init.zeros_(self.context_binary_delta.bias)
        nn.init.zeros_(self.context_phase_delta.weight)
        nn.init.zeros_(self.context_phase_delta.bias)
        nn.init.zeros_(self.history_binary_delta.weight)
        nn.init.zeros_(self.history_binary_delta.bias)
        nn.init.zeros_(self.history_phase_delta.weight)
        nn.init.zeros_(self.history_phase_delta.bias)
        nn.init.zeros_(self.long_binary_delta.weight)
        nn.init.zeros_(self.long_binary_delta.bias)
        nn.init.zeros_(self.long_phase_delta.weight)
        nn.init.zeros_(self.long_phase_delta.bias)
        nn.init.constant_(self.selector[-1].bias, -2.0)

    @staticmethod
    def audio_quality_features(audio_mel: torch.Tensor) -> torch.Tensor:
        x = audio_mel.float()
        flat = x.flatten(start_dim=1)
        mean = flat.mean(dim=1)
        std = flat.std(dim=1, unbiased=False)
        max_value = flat.max(dim=1).values
        nonzero_ratio = (flat > 1e-4).float().mean(dim=1)
        temporal_energy = x.mean(dim=2).squeeze(1)
        temporal_std = temporal_energy.std(dim=1, unbiased=False)
        temporal_max = temporal_energy.max(dim=1).values
        return torch.stack((mean, std, max_value, nonzero_ratio, temporal_std, temporal_max), dim=1)

    @staticmethod
    def match_features(visual_embedding: torch.Tensor, audio_embedding: torch.Tensor) -> torch.Tensor:
        return torch.cat((visual_embedding, audio_embedding, torch.abs(visual_embedding - audio_embedding), visual_embedding * audio_embedding), dim=1)

    def match_logits(self, visual_embedding: torch.Tensor, audio_embedding: torch.Tensor) -> torch.Tensor:
        return self.match_net(self.match_features(visual_embedding, audio_embedding)).squeeze(1)

    def load_audio_weights(self, path: str, map_location: str | torch.device = "cpu") -> tuple[list[str], list[str]]:
        checkpoint = torch.load(path, map_location=map_location, weights_only=False)
        state = checkpoint.get("model_state_dict", checkpoint)
        return self.audio_model.load_state_dict(state, strict=False)

    def train(self, mode: bool = True):
        super().train(mode)
        if self.freeze_audio_backbone:
            self.audio_model.eval()
        return self

    def forward(
        self,
        visual_tokens: torch.Tensor,
        audio_mel: torch.Tensor,
        return_dict: bool = False,
        gate_override: torch.Tensor | None = None,
        context_tokens: torch.Tensor | None = None,
        context_valid: torch.Tensor | None = None,
        long_view_tokens: torch.Tensor | None = None,
        long_view_valid: torch.Tensor | None = None,
    ):
        visual_tokens = visual_tokens.float()
        visual_embedding, raw_visual_logits, phase_logits = self.visual_model(visual_tokens)
        if context_tokens is not None:
            context_tokens = context_tokens.float()
            if context_tokens.ndim == 4:
                # [B, history, tokens, D].  The most recent segment keeps the
                # original context path; the masked average supplies a longer
                # action history without changing the V-JEPA backbone.
                history_embeddings = context_tokens.mean(dim=2)
                if context_valid is None:
                    history_valid = torch.ones(history_embeddings.shape[:2], device=history_embeddings.device, dtype=history_embeddings.dtype)
                else:
                    history_valid = context_valid.to(history_embeddings).reshape(history_embeddings.shape[:2])
                context_embedding = history_embeddings[:, -1]
                latest_valid = history_valid[:, -1]
                history_weight = history_valid / history_valid.sum(dim=1, keepdim=True).clamp_min(1.0)
                history_embedding = (history_embeddings * history_weight.unsqueeze(-1)).sum(dim=1)
                history_delta = self.history_adapter(torch.cat((visual_embedding, history_embedding), dim=1))
                history_delta = history_delta * history_valid.any(dim=1).to(history_delta).view(-1, 1)
            else:
                context_embedding = context_tokens.mean(dim=1)
                latest_valid = context_valid.to(context_embedding) if context_valid is not None else None
                history_delta = None
            context_delta = self.context_adapter(torch.cat((visual_embedding, context_embedding), dim=1))
            if latest_valid is not None:
                context_delta = context_delta * latest_valid.to(context_delta).view(-1, 1)
            visual_embedding = visual_embedding + context_delta
            raw_visual_logits = raw_visual_logits + self.context_binary_delta(context_delta)
            phase_logits = phase_logits + self.context_phase_delta(context_delta)
            if history_delta is not None:
                visual_embedding = visual_embedding + history_delta
                raw_visual_logits = raw_visual_logits + self.history_binary_delta(history_delta)
                phase_logits = phase_logits + self.history_phase_delta(history_delta)
        if long_view_tokens is not None:
            long_embedding, _, _ = self.visual_model(long_view_tokens.float())
            long_delta = self.long_view_adapter(torch.cat((visual_embedding, long_embedding), dim=1))
            if long_view_valid is not None:
                long_delta = long_delta * long_view_valid.to(long_delta).view(-1, 1)
            visual_embedding = visual_embedding + long_delta
            raw_visual_logits = raw_visual_logits + self.long_binary_delta(long_delta)
            phase_logits = phase_logits + self.long_phase_delta(long_delta)

        phase_log_probs = F.log_softmax(phase_logits, dim=1)
        phase_positive = torch.logsumexp(phase_log_probs[:, 1:3], dim=1)
        phase_negative = torch.logsumexp(
            torch.cat((phase_log_probs[:, :1], phase_log_probs[:, 3:]), dim=1),
            dim=1,
        )
        phase_binary_logits = torch.stack((phase_negative, phase_positive), dim=1)
        visual_logits = raw_visual_logits + torch.sigmoid(self.phase_binary_scale) * phase_binary_logits
        with torch.no_grad() if self.freeze_audio_backbone else torch.enable_grad():
            audio_full = self.audio_model.extract_tokens(audio_mel)
        audio_tokens = self.audio_proj(audio_full[:, 1:, :])
        audio_embedding = audio_tokens.mean(dim=1)
        audio_logits = self.audio_head(audio_embedding)

        match_logits = self.match_logits(visual_embedding, audio_embedding)
        match_score = torch.sigmoid(match_logits).unsqueeze(1)
        quality = self.audio_quality_features(audio_mel)
        p_visual = F.softmax(visual_logits, dim=1)[:, 1:2]
        p_audio = F.softmax(audio_logits, dim=1)[:, 1:2]
        gate_input = torch.cat((visual_embedding, audio_embedding, quality, match_score, p_visual, p_audio), dim=1)
        learned_gate = torch.sigmoid(self.gate(gate_input)).squeeze(1)
        gate = learned_gate if gate_override is None else gate_override.to(learned_gate).reshape_as(learned_gate)

        cross_gate = gate if self.config.cross_attention_gate else torch.ones_like(gate)
        cross_visual = visual_tokens
        cross_audio = audio_tokens
        for block in self.cross_blocks:
            cross_visual, cross_audio = block(cross_visual, cross_audio, cross_gate)
        if self.config.cross_attention_gate:
            # Do not give unreliable audio tokens a second route through the
            # fusion encoder. The ablation intentionally bypasses this path.
            cross_audio = cross_audio * cross_gate.view(-1, 1, 1)
        cls = self.fusion_cls.expand(visual_tokens.size(0), -1, -1) + self.modality_embedding[:, :1]
        cross_visual = cross_visual + self.modality_embedding[:, 1:2]
        cross_audio = cross_audio + self.modality_embedding[:, 2:3]
        fused = torch.cat((cls, cross_visual, cross_audio), dim=1)
        av_embedding = self.fusion_norm(self.fusion_encoder(fused))[:, 0]
        av_logits = self.av_head(av_embedding)
        p_av = F.softmax(av_logits, dim=1)[:, 1:2]
        impact_probability = F.softmax(phase_logits, dim=1)[:, 1:2]
        selector_input = torch.cat((visual_embedding, av_embedding, match_score, p_visual, p_av, impact_probability), dim=1)
        selector_gate = torch.sigmoid(self.selector(selector_input)).squeeze(1)
        event_gate = impact_probability.squeeze(1)
        if self.config.event_gated_audio:
            # Audio can corroborate an imminent impact, but it cannot
            # establish a static fallen state.  This prevents a missing/noisy
            # post-impact track from moving an otherwise visual fallen decision.
            final_gate = gate * selector_gate * event_gate
        else:
            final_gate = gate * selector_gate
        final_fall = ((1.0 - final_gate.unsqueeze(1)) * p_visual + final_gate.unsqueeze(1) * p_av).clamp(1e-6, 1.0 - 1e-6)
        final_probs = torch.cat((1.0 - final_fall, final_fall), dim=1)
        output = {
            "visual_logits": visual_logits,
            "phase_logits": phase_logits,
            "audio_logits": audio_logits,
            "av_logits": av_logits,
            "match_logits": match_logits,
            "match_score": match_score.squeeze(1),
            "gate": final_gate,
            "cross_gate": cross_gate,
            "selector_gate": selector_gate,
            "event_gate": event_gate,
            "learned_gate": learned_gate,
            "final_probs": final_probs,
            "final_log_probs": torch.log(final_probs),
            "visual_embedding": visual_embedding,
            "phase_prototypes": self.visual_model.phase_prototypes,
            "audio_embedding": audio_embedding,
            "impact_probability": impact_probability.squeeze(1),
        }
        return output if return_dict else output["final_log_probs"]
