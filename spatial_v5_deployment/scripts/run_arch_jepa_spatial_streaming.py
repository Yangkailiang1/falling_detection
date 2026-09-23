"""Run the architecture-preserving Spatial-v5 JEPA student on a video.

The mature deployment runner owns Pose, audio, temporal state, safety guards,
alarm policy, CSV output and rendering.  This entry point replaces only its
large V-JEPA encoder with the MC3-spatial student used by the fixed benchmark.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision.models.video import MC3_18_Weights, mc3_18


ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT = ROOT / "deployment_world_pose_av_v3"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DEPLOYMENT))

import run_world_pose_av_streaming as streaming
from surveillance.arch_jepa_spatial_runtime import (
    ArchitecturePreservingJEPAStudent,
    CausalTemporalResidualExpert,
    StudentConfig,
)
from surveillance.world_pose_audio_residual_fusion import WorldPoseAudioResidualFusion
from surveillance.world_pose_future_fusion import WorldPoseFutureConfig


class SpatialStudentEncoder(nn.Module):
    """Convert RGB clips to the exact token bundle consumed by World-Pose-AV."""

    def __init__(self, checkpoint: dict, device: torch.device):
        super().__init__()
        if "mc3_backbone_state_dict" in checkpoint:
            mc3 = mc3_18(weights=None)
        else:
            mc3 = mc3_18(weights=MC3_18_Weights.KINETICS400_V1)
        self.backbone = nn.Sequential(mc3.stem, mc3.layer1, mc3.layer2, mc3.layer3, mc3.layer4)
        if "mc3_backbone_state_dict" in checkpoint:
            self.backbone.load_state_dict(checkpoint["mc3_backbone_state_dict"], strict=True)
        student_state = checkpoint.get("base_student_state_dict", checkpoint.get("student_state_dict"))
        if student_state is None:
            raise RuntimeError("Student checkpoint lacks a base/student state dictionary")
        self.student = ArchitecturePreservingJEPAStudent(StudentConfig(**checkpoint["student_config"]))
        # These interface matrices are data-dependent buffers.  A freshly
        # constructed student intentionally starts with empty tensors, so give
        # them their checkpoint shapes before strict restoration.
        for name in ("current_right_inverse", "future_right_inverse", "current_bias", "future_joint_bias"):
            setattr(self.student, name, student_state[name].clone())
        self.student.load_state_dict(student_state, strict=True)
        self.temporal_residual = None
        if "expert_state_dict" in checkpoint:
            self.temporal_residual = CausalTemporalResidualExpert()
            self.temporal_residual.load_state_dict(checkpoint["expert_state_dict"], strict=True)
        self.frames = int(checkpoint["student_config"]["frames"])
        self.spatial_size = int(checkpoint["student_config"]["spatial_size"])
        self.register_buffer("mean", torch.tensor((0.43216, 0.394666, 0.37645)).view(1, 3, 1, 1, 1))
        self.register_buffer("std", torch.tensor((0.22803, 0.22145, 0.216989)).view(1, 3, 1, 1, 1))
        self.to(device).eval()

    def refine_token_bundle(
        self,
        bundle: dict[str, torch.Tensor],
        pose: torch.Tensor,
        boxes: torch.Tensor,
    ) -> dict[str, torch.Tensor]:
        """Apply the optional v5 temporal residual after Pose is available.

        The base MC3 student remains the token source.  Only the two temporal
        JEPA-contract tensors are corrected; world/crop tokens stay identical
        to v5 so history and long-view semantics are preserved.
        """
        if self.temporal_residual is None:
            return bundle
        current, future, _ = self.temporal_residual(
            bundle["current_projected"], bundle["future_projected"], pose, boxes
        )
        legacy_current, legacy_future = self.student.legacy_tokens(
            current, future, bundle["pose_future_projected"]
        )
        return {
            **bundle,
            "current_projected": current,
            "future_projected": future,
            "current_temporal_tokens": legacy_current,
            "future_temporal_tokens": legacy_future,
        }

    def extract_token_bundle(self, video: torch.Tensor) -> dict[str, torch.Tensor]:
        # Deployment clips use 32 Pose-aligned frames; distillation used 16 RGB
        # frames sampled across the same causal interval.
        indices = torch.linspace(0, video.size(1) - 1, self.frames, device=video.device).round().long()
        video = video.index_select(1, indices).float().div_(255.0).permute(0, 2, 1, 3, 4)
        batch, channels, frames, height, width = video.shape
        resized = F.interpolate(
            video.permute(0, 2, 1, 3, 4).reshape(batch * frames, channels, height, width),
            size=(112, 112), mode="bilinear", align_corners=False,
        ).reshape(batch, frames, channels, 112, 112).permute(0, 2, 1, 3, 4)
        feature = self.backbone((resized - self.mean) / self.std)
        feature = F.adaptive_avg_pool3d(
            feature, (feature.size(2), self.spatial_size, self.spatial_size)
        )
        feature = feature.float().permute(0, 2, 3, 4, 1).reshape(
            batch, feature.size(2), self.spatial_size ** 2, feature.size(1)
        )
        # The streaming runner batches full-frame and person-crop clips.  The
        # student interface expects the two streams separately.
        if batch == 2:
            output = self.student(feature[:1], feature[1:])
            # Preserve the teacher encoder's batched return contract: row 0 is
            # full-frame and row 1 is crop.  Only row 1 world_tokens is later
            # consumed as crop_world_tokens; duplicate the other interfaces.
            packed = {}
            for key, value in output.items():
                second = output["crop_world_tokens"] if key == "world_tokens" else value
                packed[key] = torch.cat((value, second), dim=0)
            return packed
        return self.student(feature, feature)


def load_spatial_models(args):
    checkpoint_path = Path(args.model_path) if args.model_path else (
        ROOT / "deployment_world_pose_av_v3" / "models" / "v5_temporal_residual_full_v1.pth"
    )
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    visual_config = WorldPoseFutureConfig(**checkpoint["teacher_visual_config"])
    warning_path = Path("")
    warning_checkpoint = None
    has_embedded_warning = any(
        key.startswith("visual.future_pose_risk_head.")
        for key in checkpoint["adapted_detector_state_dict"]
    )
    if has_embedded_warning:
        visual_config = replace(visual_config, future_pose_warning_head=True)
    model = WorldPoseAudioResidualFusion(visual_config, freeze_visual=True, freeze_audio=True)
    model.load_state_dict(checkpoint["adapted_detector_state_dict"], strict=True)
    model.to(args.device).eval()
    encoder = SpatialStudentEncoder(checkpoint, torch.device(args.device))
    if encoder.temporal_residual is not None:
        print("[SpatialStudent] loaded v5 causal temporal-token residual expert")
    # Match the return contract of the original strong-teacher loader.
    return checkpoint_path, checkpoint, warning_path, warning_checkpoint, visual_config, model, encoder


if __name__ == "__main__":
    streaming.load_models = load_spatial_models
    # Spatial-v5 was verified on all 39 local videos with this cache mode:
    # it preserves every alarm/warning timestamp while removing repeated
    # overlapping-window YOLO calls.  Keep an explicit user selection intact.
    if "--pose-mode" not in sys.argv:
        sys.argv.extend(("--pose-mode", "sampled_incremental"))
    if "--normal-hz" not in sys.argv:
        sys.argv.extend(("--normal-hz", "4"))
    if "--active-hz" not in sys.argv:
        sys.argv.extend(("--active-hz", "4"))
    if "--long-view-seconds" not in sys.argv:
        sys.argv.extend(("--long-view-seconds", "6"))
    if "--long-view-refresh" not in sys.argv:
        sys.argv.extend(("--long-view-refresh", "6"))
    streaming.main()
