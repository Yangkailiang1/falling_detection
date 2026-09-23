"""Stream the strong World-Pose-AV teacher without redoing window work.

This runner keeps the validated v2.2 AV residual as the final classifier.  It
does not substitute a student model.  Instead it builds the inputs used by the
strong protocol causally at runtime:

* incremental YOLO Pose ring buffer;
* previous three full-frame world-token bundles;
* periodically refreshed long-view world tokens;
* a batched Full/Crop V-JEPA pass;
* normal/suspicious state scheduling controlled by Pose motion only.

Pose controls how often the expensive teacher runs; it never replaces the
teacher's final fall decision.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import deque
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn as nn

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "package"))

import run_world_pose_av_v4 as base
from latest_frame_capture import LatestFrameCapture
from surveillance.vjepa2_world_visual_encoder import VJEPA2WorldVisualConfig, VJEPA2WorldVisualEncoder
from surveillance.world_pose_audio_residual_fusion import WorldPoseAudioResidualFusion
from surveillance.world_pose_future_fusion import WorldPoseFutureConfig


class ViTBStreamingInterface(nn.Module):
    """Apply the trained ViT-B interface used to build the offline cache."""

    def __init__(self, backbone: VJEPA2WorldVisualEncoder, interface_path: str):
        super().__init__()
        self.backbone = backbone
        self.current_temporal = nn.Sequential(nn.LayerNorm(768), nn.Linear(768, 1024))
        payload = torch.load(interface_path, map_location="cpu", weights_only=False)
        state = payload["interface_adapter_state_dict"]
        self.backbone.current_resampler.load_state_dict(state["current_resampler"], strict=True)
        self.backbone.future_resampler.load_state_dict(state["future_resampler"], strict=True)
        self.current_temporal.load_state_dict(state["current_temporal"], strict=True)
        with torch.no_grad():
            self.backbone.type_embedding.copy_(state["type_embedding"].to(self.backbone.type_embedding))

    def extract_token_bundle(self, frames: torch.Tensor):
        prepared = self.backbone._prepare_frames(frames)
        masks_x, masks_y = self.backbone._make_temporal_masks(
            prepared.size(0), prepared.size(2), prepared.device
        )
        with torch.inference_mode(), torch.amp.autocast(
            "cuda", dtype=torch.float16, enabled=prepared.is_cuda
        ):
            context = self.backbone.encoder(prepared, masks=masks_x)
            future, _ = self.backbone.predictor(
                context, masks_x=[masks_x], masks_y=[masks_y], mod="video"
            )
        context = context.float()
        future = future.float()
        batch = context.size(0)
        current_tokens = self.backbone.current_resampler(context) + self.backbone.type_embedding[:, 0:1]
        future_tokens = self.backbone.future_resampler(future) + self.backbone.type_embedding[:, 1:2]
        current_temporal = self.current_temporal(context.reshape(batch, 16, 144, 768).mean(dim=2))
        future_temporal = future.reshape(batch, 8, 36, future.size(-1)).mean(dim=2)
        return {
            "current_tokens": current_tokens,
            "future_tokens": future_tokens,
            "world_tokens": torch.cat((current_tokens, future_tokens), dim=1),
            "current_temporal_tokens": current_temporal,
            "future_temporal_tokens": future_temporal,
        }


def fixed_samples(items, count: int):
    """Select a fixed causal sequence, repeating the earliest item at warm-up."""
    if not items:
        raise ValueError("Cannot sample an empty stream")
    positions = np.linspace(0, len(items) - 1, count).round().astype(np.int64)
    return [items[int(position)] for position in positions]


def streaming_history_integrity(frames):
    """Cheap corruption guard for a live stream.

    This only distinguishes a normal scene from decoder-corrupted flat/grey
    video. Eight evenly distributed 160-pixel frames preserve that decision
    while avoiding 32 full-resolution Canny calls at every teacher window.
    """
    probe = fixed_samples(list(frames), min(8, len(frames)))
    measurements = []
    for frame in probe:
        height, width = frame.shape[:2]
        scale = min(1.0, 160.0 / max(height, width, 1))
        if scale < 1.0:
            frame = cv2.resize(frame, (max(2, round(width * scale)), max(2, round(height * scale))), interpolation=cv2.INTER_AREA)
        measurements.append(base.frame_integrity(frame))
    valid_count = sum(item[0] for item in measurements)
    count = max(len(measurements), 1)
    return {
        "valid": valid_count / count >= 0.80,
        "valid_fraction": float(valid_count / count),
        "contrast": float(np.median([item[1] for item in measurements])),
        "range90": float(np.median([item[2] for item in measurements])),
        "edge_density": float(np.median([item[3] for item in measurements])),
    }


def frames_to_tensor(frames, device: str):
    rgb = [cv2.cvtColor(cv2.resize(frame, (384, 384)), cv2.COLOR_BGR2RGB) for frame in frames]
    return torch.from_numpy(np.asarray(rgb, dtype=np.uint8)).permute(0, 3, 1, 2).unsqueeze(0).to(device)


def split_bundle(bundle: dict[str, torch.Tensor], index: int) -> dict[str, torch.Tensor]:
    return {key: value[index:index + 1] for key, value in bundle.items()}


def build_history(history: deque[torch.Tensor], current: torch.Tensor, size: int, device: str):
    previous = list(history)[-size:]
    padding = size - len(previous)
    tokens = [current] * padding + previous
    valid = [0.0] * padding + [1.0] * len(previous)
    return (
        torch.stack(tokens, dim=1).to(device),
        torch.tensor(valid, dtype=torch.float32, device=device).unsqueeze(0),
    )


def load_models(args):
    checkpoint_path = Path(args.model_path) if args.model_path else ROOT / "models" / "world_pose_audio_residual_v2_2_gmd_full.pth"
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    visual_config = WorldPoseFutureConfig(**checkpoint["visual_config"])
    warning_path = Path(args.warning_model_path) if args.warning_model_path else ROOT / "models" / "world_pose_future_warning_v2_2_gmd_full.pth"
    warning_checkpoint = None
    if not args.no_warning_model and warning_path.is_file():
        warning_checkpoint = torch.load(warning_path, map_location="cpu", weights_only=False)
        visual_config = replace(visual_config, future_pose_warning_head=True)
    model = WorldPoseAudioResidualFusion(visual_config, freeze_visual=True, freeze_audio=True).to(args.device).eval()
    missing, unexpected = model.load_state_dict(checkpoint["model_state_dict"], strict=False)
    allowed_missing = {key for key in missing if key.startswith("visual.future_pose_risk_head.")}
    if unexpected or len(allowed_missing) != len(missing):
        raise RuntimeError(f"AV checkpoint mismatch: missing={missing}, unexpected={unexpected}")
    if warning_checkpoint is not None:
        current_state = model.visual.state_dict()
        warning_state = {
            key: value for key, value in warning_checkpoint["model_state_dict"].items()
            if key.startswith("future_pose_risk_head.")
        }
        if not warning_state:
            raise RuntimeError("Warning checkpoint lacks Future-Pose Warning tensors")
        current_state.update(warning_state)
        model.visual.load_state_dict(current_state, strict=True)
    use_vitb = args.encoder_variant == "vitb"
    encoder_config = VJEPA2WorldVisualConfig(
        repo_path=str(ROOT / "third_party" / "vjepa2_runtime"),
        checkpoint_path=(args.vjepa_checkpoint or str(
            ROOT / "models" / ("vjepa2_1_vitb_dist_vitG_384.pt" if use_vitb else "vjepa2_1_vitl_dist_vitG_384.pt")
        )),
        variant=("vjepa2_1_vit_base_384" if use_vitb else "vjepa2_1_vit_large_384"),
        token_adapter_path=(
            str(ROOT / "models" / "_vitb_interface_managed.pth")
            if use_vitb else str(ROOT / "models" / "world_token_adapter_v1.pth")
        ),
        require_token_adapter=not use_vitb,
        context_frames=visual_config.context_frames,
        future_frames=visual_config.future_frames,
        image_size=384,
    )
    encoder = VJEPA2WorldVisualEncoder(encoder_config, load_weights=True)
    if use_vitb:
        if not args.vitb_interface_model:
            raise ValueError("--vitb-interface-model is required for --encoder-variant vitb")
        encoder = ViTBStreamingInterface(encoder, args.vitb_interface_model)
    encoder = encoder.to(args.device).eval()
    if args.tf32 and str(args.device).startswith("cuda"):
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        torch.backends.cudnn.benchmark = True
    if args.compile_encoder and hasattr(torch, "compile"):
        encoder = torch.compile(encoder, mode="reduce-overhead")
    return checkpoint_path, checkpoint, warning_path, warning_checkpoint, visual_config, model, encoder


def parse_args():
    parser = argparse.ArgumentParser(description="Streaming Spatial-v5 World-Pose-AV detector")
    parser.add_argument("--video", default="", help="Offline video path.")
    parser.add_argument("--live-source", default="", help="Camera index (for example 0) or RTSP URL; uses a bounded latest-frame queue.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--pose-device", default="")
    parser.add_argument("--model-path", default="")
    parser.add_argument("--warning-model-path", default="")
    parser.add_argument("--no-warning-model", action="store_true")
    parser.add_argument("--encoder-variant", choices=("vitl", "vitb"), default="vitl")
    parser.add_argument("--vjepa-checkpoint", default="")
    parser.add_argument("--vitb-interface-model", default="")
    parser.add_argument("--no-audio", action="store_true")
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--warning-threshold", type=float, default=None)
    parser.add_argument("--normal-hz", type=float, default=1.0)
    parser.add_argument("--active-hz", type=float, default=2.0)
    parser.add_argument("--pose-mode", choices=("window", "incremental", "sampled_incremental"), default="window",
                        help=("window recomputes 32 poses per detector window; incremental runs pose on every decoded frame; "
                              "sampled_incremental caches one pose per 16 Hz model sample for real-time deployment."))
    parser.add_argument("--pose-batch-size", type=int, default=4,
                        help="Only used by --pose-mode incremental.")
    parser.add_argument("--pose-risk-threshold", type=float, default=0.35)
    parser.add_argument("--long-view-seconds", type=float, default=8.0)
    parser.add_argument("--long-view-refresh", type=float, default=4.0)
    parser.add_argument("--history-size", type=int, default=3)
    parser.add_argument("--sample-fps", type=float, default=16.0)
    parser.add_argument("--min-consecutive", type=int, default=2)
    parser.add_argument("--fast-confirm-threshold", type=float, default=0.93)
    parser.add_argument("--tf32", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--compile-encoder", action="store_true")
    parser.add_argument("--warmup", action=argparse.BooleanOptionalAction, default=True,
                        help="Warm up CUDA before processing frames so first-window latency is not affected by model compilation.")
    parser.add_argument("--predictions-csv", default="")
    parser.add_argument("--timing-json", default="")
    return parser.parse_args()


def main():
    args = parse_args()
    if min(args.normal_hz, args.active_hz, args.sample_fps) <= 0 or args.pose_batch_size < 1:
        raise ValueError("All frequencies must be positive")
    if not args.video and not args.live_source:
        raise ValueError("Provide --video or --live-source")
    live_source = bool(args.live_source)
    started = time.perf_counter()
    (checkpoint_path, checkpoint, warning_path, warning_checkpoint, visual_config,
     model, encoder) = load_models(args)
    from ultralytics import YOLO
    # On 8 GB GPUs, co-locating YOLO Pose with JEPA causes allocator pressure
    # and severe kernel stalls. Keep JEPA on CUDA and run the tiny Pose model
    # on CPU unless the operator explicitly selects another Pose device.
    pose_device = args.pose_device or (
        "cpu" if args.encoder_variant == "vitb" and str(args.device).startswith("cuda") else args.device
    )
    pose_model = YOLO(str(ROOT / "models" / "yolo11n-pose.pt"))
    # Audio capture is intentionally not emulated for a camera source.  A
    # production microphone adapter can feed real mel features later; the
    # reliability path already handles the absent-audio state safely.
    y, sr = (None, None) if args.no_audio or live_source else base.extract_audio(args.video, 22050)
    threshold = float(checkpoint.get("decision_threshold", 0.7444) if args.threshold is None else args.threshold)
    warning_threshold = float(
        warning_checkpoint.get("warning_threshold", 0.70)
        if args.warning_threshold is None and warning_checkpoint is not None else (args.warning_threshold or 0.70)
    )
    capture = LatestFrameCapture(args.live_source) if live_source else cv2.VideoCapture(args.video)
    if not live_source and not capture.isOpened():
        raise RuntimeError(f"Could not open {args.video}")
    # The first Ultralytics CUDA call can compile kernels for tens of seconds.
    # Use one frame from this camera/video so its aspect-ratio-specific kernels
    # are also ready before the stream starts.
    warmup_seconds = 0.0
    if args.warmup:
        ok, warmup_frame = capture.read()
        if not ok:
            raise RuntimeError(f"Could not read a warm-up frame from {args.video}")
        if not live_source:
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
        warmup_started = time.perf_counter()
        warmup_frames = [warmup_frame] * visual_config.context_frames
        with torch.inference_mode():
            _, warmup_boxes = base.pose_for_frames(pose_model, warmup_frames, pose_device)
            warmup_full = frames_to_tensor(warmup_frames, args.device)
            warmup_crop = frames_to_tensor(base.person_crop_frames(warmup_frames, warmup_boxes), args.device)
            if args.encoder_variant == "vitb":
                encoder.extract_token_bundle(warmup_full)
                encoder.extract_token_bundle(warmup_crop)
            else:
                encoder.extract_token_bundle(torch.cat((warmup_full, warmup_crop), dim=0))
        base.synchronize_cuda(args.device)
        warmup_seconds = time.perf_counter() - warmup_started
        print(f"[SpatialV5] CUDA warm-up complete in {warmup_seconds:.2f}s; ready for frames.")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 25.0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)); height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    context_seconds = (visual_config.context_frames - 1) / args.sample_fps
    forecast_seconds = visual_config.future_frames / args.sample_fps
    raw_buffer = deque(maxlen=max(int(round(max(args.long_view_seconds, context_seconds) * fps)) + 8, visual_config.context_frames + 4))
    # The detector was trained with a 16 Hz, 32-frame visual/Pose sequence.
    # Cache exactly that sequence once instead of re-running YOLO for the same
    # overlapping 32 frames at every 4 Hz detection update.
    sampled_buffer = deque(maxlen=max(
        int(round(max(args.long_view_seconds, context_seconds) * args.sample_fps)) + 8,
        visual_config.context_frames + 4,
    ))
    token_history: deque[torch.Tensor] = deque(maxlen=max(1, args.history_size))
    long_tokens = None; long_valid = None; next_long = args.long_view_seconds
    next_teacher = 0.0; next_sample = 0.0; frame_number = 0; last_stream_timestamp = 0.0
    pose_pending: list[dict] = []
    raw_consecutive = 0; smooth_consecutive = 0; warning_consecutive = 0
    smoothed = 0.0; has_prediction = False; alarm = False; first_alarm_time = None
    temporal_was_ready = False
    latest_pose = None; latest_future = None; latest_visibility = None
    rows = []
    timing = {"incremental_pose_seconds": 0.0, "input_prepare_seconds": 0.0, "full_crop_jepa_seconds": 0.0,
              "long_view_jepa_seconds": 0.0, "audio_feature_seconds": 0.0,
              "fusion_seconds": 0.0, "window_seconds": 0.0, "inference_windows": 0,
              "long_view_updates": 0}
    last = {"p_visual": 0.0, "p_candidate": 0.0, "p_final": 0.0, "gate": 0.0,
            "alarm": False, "warning": False, "p_future_warning": 0.0,
            "warning_threshold": warning_threshold, "video_valid": True, "video_valid_fraction": 0.0,
            "audio_valid": False, "p_world": 0.0, "p_pose": 0.0,
            "pose_reliability": 0.0, "pose_quality": (0.0, 0.0, 0.0, 0.0), "smooth": 0.0}
    loop_started = time.perf_counter()
    def flush_pose_pending():
        """Run Pose on a small batch and write results into existing ring rows."""
        nonlocal latest_pose
        if not pose_pending:
            return
        base.synchronize_cuda(args.device)
        pose_started = time.perf_counter()
        pose_value, box_value = base.pose_for_frames(pose_model, [item["frame"] for item in pose_pending], pose_device)
        base.synchronize_cuda(args.device)
        timing["incremental_pose_seconds"] += time.perf_counter() - pose_started
        for item, keypoints, box in zip(pose_pending, pose_value, box_value):
            item["pose"] = keypoints
            item["box"] = box
        latest_pose = pose_value[-1]
        pose_pending.clear()

    while True:
        ok, frame = capture.read()
        if not ok:
            break
        timestamp = max(0.0, capture.last_timestamp - loop_started) if live_source else frame_number / fps
        last_stream_timestamp = timestamp
        item = {"time": timestamp, "frame": frame.copy(),
                "pose": np.zeros((17, 3), np.float32), "box": np.zeros(6, np.float32)}
        raw_buffer.append(item)
        if args.pose_mode == "sampled_incremental":
            # Native camera FPS can be greater than 16.  Keep the most recent
            # decoded frame for each causal 16 Hz slot; this is bounded work
            # and matches the model's training sample rate.
            if timestamp + 1e-6 >= next_sample:
                sampled_buffer.append(item)
                pose_pending.append(item)
                step = 1.0 / args.sample_fps
                while next_sample <= timestamp + 1e-6:
                    next_sample += step
        elif args.pose_mode == "incremental":
            pose_pending.append(item)
            if len(pose_pending) >= args.pose_batch_size:
                flush_pose_pending()
        source_buffer = sampled_buffer if args.pose_mode == "sampled_incremental" else raw_buffer
        recent = [entry for entry in source_buffer if entry["time"] >= timestamp - context_seconds]
        if len(recent) >= 2 and timestamp >= next_teacher:
            window_started = time.perf_counter()
            sampled = fixed_samples(recent, visual_config.context_frames)
            sampled_frames = [entry["frame"] for entry in sampled]
            if args.pose_mode in {"incremental", "sampled_incremental"}:
                # Finalize the small tail batch before using its Pose trajectory.
                flush_pose_pending()
                pose = np.asarray([entry["pose"] for entry in sampled], dtype=np.float32)
                boxes = np.asarray([entry["box"] for entry in sampled], dtype=np.float32)
            else:
                pose_started = time.perf_counter()
                with torch.inference_mode():
                    pose, boxes = base.pose_for_frames(pose_model, sampled_frames, pose_device)
                timing["incremental_pose_seconds"] += time.perf_counter() - pose_started
                latest_pose = pose[-1]
            prepare_started = time.perf_counter()
            integrity = streaming_history_integrity(sampled_frames)
            motion_risk = base.pose_motion_risk(pose, boxes)
            full = frames_to_tensor(sampled_frames, args.device)
            crop = frames_to_tensor(base.person_crop_frames(sampled_frames, boxes), args.device)
            timing["input_prepare_seconds"] += time.perf_counter() - prepare_started
            base.synchronize_cuda(args.device)
            jepa_started = time.perf_counter()
            with torch.inference_mode():
                if args.encoder_variant == "vitb":
                    bundle = encoder.extract_token_bundle(full)
                    crop_bundle = encoder.extract_token_bundle(crop)
                else:
                    combined = encoder.extract_token_bundle(torch.cat((full, crop), dim=0))
                    bundle = split_bundle(combined, 0)
                    crop_bundle = split_bundle(combined, 1)
            base.synchronize_cuda(args.device)
            timing["full_crop_jepa_seconds"] += time.perf_counter() - jepa_started
            if timestamp >= next_long and timestamp >= args.long_view_seconds:
                long_items = [entry for entry in source_buffer if entry["time"] >= timestamp - args.long_view_seconds]
                if len(long_items) >= 2:
                    long_frames = [entry["frame"] for entry in fixed_samples(long_items, visual_config.context_frames)]
                    base.synchronize_cuda(args.device)
                    long_started = time.perf_counter()
                    with torch.inference_mode():
                        long_bundle = encoder.extract_token_bundle(frames_to_tensor(long_frames, args.device))
                    base.synchronize_cuda(args.device)
                    timing["long_view_jepa_seconds"] += time.perf_counter() - long_started
                    long_tokens = long_bundle["world_tokens"].detach()
                    long_valid = torch.ones((1,), dtype=torch.float32, device=args.device)
                    next_long = timestamp + args.long_view_refresh
                    timing["long_view_updates"] += 1
            context_tokens, context_valid = build_history(token_history, bundle["world_tokens"].detach(), args.history_size, args.device)
            audio_started = time.perf_counter()
            mel, audio_is_valid = base.audio_window(y, sr, timestamp)
            timing["audio_feature_seconds"] += time.perf_counter() - audio_started
            audio = torch.from_numpy(mel).unsqueeze(0).to(args.device)
            audio_valid = torch.tensor([float(audio_is_valid)], dtype=torch.float32, device=args.device)
            pose_tensor = torch.from_numpy(pose).unsqueeze(0).to(args.device)
            box_tensor = torch.from_numpy(boxes).unsqueeze(0).to(args.device)
            # Compact v5-derived encoders may expose a learned temporal-token
            # correction.  It runs here, after the current 32-frame Pose/box
            # context exists, which matches its training contract exactly.
            if hasattr(encoder, "refine_token_bundle"):
                with torch.inference_mode():
                    bundle = encoder.refine_token_bundle(bundle, pose_tensor, box_tensor)
            base.synchronize_cuda(args.device)
            fusion_started = time.perf_counter()
            with torch.inference_mode():
                outputs = model(
                    bundle["world_tokens"], bundle["current_temporal_tokens"], bundle["future_temporal_tokens"],
                    pose_tensor, box_tensor, None, None, audio, audio_valid,
                    context_tokens, context_valid, long_tokens, long_valid, crop_bundle["world_tokens"],
                )
            base.synchronize_cuda(args.device)
            timing["fusion_seconds"] += time.perf_counter() - fusion_started
            token_history.append(bundle["world_tokens"].detach())
            timing["window_seconds"] += time.perf_counter() - window_started
            timing["inference_windows"] += 1
            p_final = float(outputs["final_probs"][0, 1]); p_visual = float(torch.sigmoid(outputs["visual_logit"])[0])
            p_candidate = float(outputs["candidate_logits"].softmax(1)[0, 1]); gate = float(outputs["utility_gate"][0])
            p_world = float(outputs["world_logits"].softmax(1)[0, 1]); p_pose = float(outputs["pose_logits"].softmax(1)[0, 1])
            p_pose_observed = float(outputs["observed_pose_fusion_probability"][0])
            pose_world_agreement = bool(outputs["pose_world_agreement"][0])
            p_warning = float(outputs["future_pose_led_logits"].softmax(1)[0, 1]) if outputs.get("future_pose_led_logits") is not None else 0.0
            latest_future = outputs["pose_future_coordinates"][0].detach().cpu().numpy()
            latest_visibility = torch.sigmoid(outputs["pose_future_visibility_logits"][0]).detach().cpu().numpy()
            # The model is trained on a real 32-frame temporal context. At
            # stream start `fixed_samples` must repeat the available frames;
            # those synthetic repetitions can yield a spurious high Pose logit.
            # Keep displaying scores during warm-up, but never warn or alarm
            # until the complete causal RGB window has been observed.
            temporal_ready = timestamp >= context_seconds
            decision_valid = integrity["valid"] and temporal_ready
            pose_evidence = p_pose >= 0.20 or motion_risk >= 0.50
            if temporal_ready:
                smoothed = p_final if not temporal_was_ready else 0.45 * p_final + 0.55 * smoothed
                raw_consecutive = raw_consecutive + 1 if decision_valid and p_final >= threshold else 0
                smooth_consecutive = smooth_consecutive + 1 if decision_valid and smoothed >= threshold else 0
                warning_consecutive = warning_consecutive + 1 if decision_valid and p_warning >= warning_threshold else 0
            else:
                smoothed = 0.0
                raw_consecutive = 0; smooth_consecutive = 0; warning_consecutive = 0
            warning = decision_valid and warning_consecutive >= 1
            fast_confirm = (
                (p_final >= args.fast_confirm_threshold and pose_evidence)
                or pose_world_agreement
            )
            if decision_valid and (fast_confirm or max(raw_consecutive, smooth_consecutive) >= args.min_consecutive):
                alarm = True
            if alarm and first_alarm_time is None:
                first_alarm_time = timestamp
            last = {"p_visual": p_visual, "p_candidate": p_candidate, "p_final": p_final, "gate": gate,
                    "alarm": alarm, "warning": warning, "p_future_warning": p_warning,
                    "warning_threshold": warning_threshold, "video_valid": integrity["valid"],
                    "video_valid_fraction": integrity["valid_fraction"], "audio_valid": audio_is_valid,
                    "p_world": p_world, "p_pose": p_pose,
                    "pose_reliability": float(outputs["pose_reliability"][0]),
                    "pose_quality": tuple(float(value) for value in outputs["pose_quality"][0].cpu()), "smooth": smoothed}
            has_prediction = True
            temporal_was_ready = temporal_ready
            active = temporal_ready and (motion_risk >= args.pose_risk_threshold or p_warning >= warning_threshold)
            next_teacher = timestamp + 1.0 / (args.active_hz if active else args.normal_hz)
            rows.append({"frame": frame_number, "time_seconds": timestamp, "p_final": p_final,
                         "p_visual": p_visual, "p_candidate": p_candidate, "p_world": p_world,
                         "p_pose": p_pose, "p_future_warning": p_warning, "audio_gate": gate,
                         "p_pose_observed": p_pose_observed,
                         "pose_world_agreement": int(pose_world_agreement),
                         "pose_reliability": float(outputs["pose_reliability"][0]),
                         "pose_expert_gate": float(outputs["pose_expert_gate"][0]),
                         "phase_motion_gate_delta": float(outputs["phase_motion_gate_delta"][0]),
                         "pose_quality_mean": float(outputs["pose_quality"][0].mean()),
                         "motion_risk": motion_risk, "active_schedule": int(active),
                         "history_valid": int(context_valid.sum().item()), "long_view_valid": int(long_tokens is not None),
                         "video_valid": int(integrity["valid"]), "temporal_ready": int(temporal_ready),
                         "alarm": int(alarm), "warning": int(warning)})
        base.draw_pose(frame, latest_pose)
        base.draw_predicted_pose(frame, latest_future[-1] if latest_future is not None else None,
                                 latest_visibility[-1] if latest_visibility is not None else None)
        base.draw_compact_panel(frame, last, frame_number, timestamp, first_alarm_time,
                                context_seconds, forecast_seconds, has_prediction)
        writer.write(frame); frame_number += 1
    capture.release(); writer.release()
    total_seconds = time.perf_counter() - started; loop_seconds = time.perf_counter() - loop_started
    if args.predictions_csv and rows:
        prediction_path = Path(args.predictions_csv); prediction_path.parent.mkdir(parents=True, exist_ok=True)
        with prediction_path.open("w", newline="", encoding="utf-8-sig") as handle:
            csv.DictWriter(handle, fieldnames=rows[0].keys()).writeheader()
            writer_rows = csv.DictWriter(handle, fieldnames=rows[0].keys()); writer_rows.writerows(rows)
    if args.timing_json:
        count = max(int(timing["inference_windows"]), 1)
        duration = last_stream_timestamp if live_source else frame_number / max(fps, 1e-6)
        summary = {"video": args.video or args.live_source, "model": str(checkpoint_path), "frames": frame_number,
                   "video_seconds": duration, "startup_seconds": loop_started - started,
                   "warmup_seconds": warmup_seconds,
                   "loop_seconds": loop_seconds, "total_seconds": total_seconds,
                   "real_time_factor": duration / max(total_seconds, 1e-6),
                   "steady_state_real_time_factor": duration / max(loop_seconds, 1e-6),
                   "steady_state_seconds_per_video_second": loop_seconds / max(duration, 1e-6),
                   "no_delay_accumulation": bool(loop_seconds <= duration),
                   "pose_device": str(pose_device),
                   "inference_windows": int(timing["inference_windows"]),
                   "long_view_updates": int(timing["long_view_updates"]),
                   "mean_window_seconds": timing["window_seconds"] / count,
                   "mean_window_hz": 1.0 / max(timing["window_seconds"] / count, 1e-6),
                   "live_source": live_source,
                   "dropped_stale_frames": int(getattr(capture, "dropped_frames", 0)),
                   **{key: float(value) for key, value in timing.items() if key not in {"inference_windows", "long_view_updates"}}}
        timing_path = Path(args.timing_json); timing_path.parent.mkdir(parents=True, exist_ok=True)
        timing_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[SpatialV5] wrote {output}; windows={timing['inference_windows']} alarm={alarm}")


if __name__ == "__main__":
    main()
