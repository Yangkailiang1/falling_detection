"""Deploy v4 World-Pose with a bounded audio residual.

The v4 World-Pose branch remains the base decision. Audio Transformer
cross-attention can only make a learned, bounded logit correction; missing or
invalid audio returns the visual probability exactly.
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import deque
from dataclasses import replace
from pathlib import Path

import cv2
import librosa
import numpy as np
import torch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "package"))

from surveillance.audio_fall_detector import AudioFallConfig, waveform_to_mel
from surveillance.vjepa2_world_visual_encoder import VJEPA2WorldVisualConfig, VJEPA2WorldVisualEncoder
from surveillance.world_pose_audio_residual_fusion import WorldPoseAudioResidualFusion
from surveillance.world_pose_future_fusion import WorldPoseFutureConfig


SKELETON = ((5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12),
            (11, 13), (13, 15), (12, 14), (14, 16), (0, 1), (0, 2), (1, 3), (2, 4))


def synchronize_cuda(device: str):
    """Make optional CUDA timings report actual execution, not queued kernels."""
    if torch.cuda.is_available() and str(device).startswith("cuda"):
        torch.cuda.synchronize(device)


def extract_audio(path: str, sample_rate: int):
    exe = shutil.which("ffmpeg")
    if exe is None:
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None, None
    fd, wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        subprocess.run([exe, "-y", "-i", path, "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "wav", wav],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        if os.path.getsize(wav) <= 44:
            return None, None
        y, sr = librosa.load(wav, sr=sample_rate, mono=True)
        return y.astype(np.float32), int(sr)
    except Exception:
        return None, None
    finally:
        if os.path.exists(wav):
            os.remove(wav)


def audio_window(y, sr, end_seconds: float, duration: float = 3.0):
    if y is None or sr is None:
        return np.zeros((1, 128, 130), dtype=np.float32), False
    target = int(round(duration * sr)); end = int(round(end_seconds * sr)); start = end - target
    clip = y[max(0, start):min(len(y), end)]
    clip = np.pad(clip, (max(0, -start), max(0, end - len(y))))[:target]
    mel = waveform_to_mel(clip.astype(np.float32), sr, AudioFallConfig(sample_rate=sr, target_duration=duration))
    return mel, True


def pose_for_frames(pose_model, frames, pose_device):
    results = pose_model.predict(list(frames), imgsz=640, device=pose_device, verbose=False, stream=False)
    pose = np.zeros((len(frames), 17, 3), dtype=np.float32)
    boxes = np.zeros((len(frames), 6), dtype=np.float32)
    for i, (frame, result) in enumerate(zip(frames, results)):
        if result.keypoints is None or result.boxes is None or len(result.boxes) == 0:
            continue
        h, w = frame.shape[:2]
        xyxy = result.boxes.xyxy.detach().cpu().numpy(); conf = result.boxes.conf.detach().cpu().numpy()
        area = np.maximum(0, xyxy[:, 2] - xyxy[:, 0]) * np.maximum(0, xyxy[:, 3] - xyxy[:, 1])
        j = int(np.argmax(conf * np.sqrt(np.maximum(area, 1.0))))
        xy = result.keypoints.xyn[j].detach().cpu().numpy().astype(np.float32)
        kc = result.keypoints.conf[j].detach().cpu().numpy().astype(np.float32)
        n = min(17, len(xy)); pose[i, :n, :2] = np.clip(xy[:n, :2], 0, 1); pose[i, :n, 2] = np.clip(kc[:n], 0, 1)
        x1, y1, x2, y2 = xyxy[j]
        visible = np.clip(min(x2, w) - max(x1, 0), 0, w) * np.clip(min(y2, h) - max(y1, 0), 0, h)
        boxes[i] = [(x1 + x2) / (2 * w), (y1 + y2) / (2 * h), (x2 - x1) / w, (y2 - y1) / h, conf[j], visible / max((x2 - x1) * (y2 - y1), 1)]
    return pose, boxes


def person_crop_frames(frames, boxes, scale=1.35):
    """Build the same causal person-centered RGB crop used by v4 training."""
    crops = []
    previous = None
    for frame, box in zip(frames, np.asarray(boxes, dtype=np.float32)):
        h, w = frame.shape[:2]
        valid = (
            box.shape[0] >= 6 and box[4] >= 0.12 and box[5] >= 0.20
            and box[2] >= 0.025 and box[3] >= 0.025
        )
        current = box[:4].copy() if valid else previous
        if current is None:
            current = np.asarray((0.5, 0.5, 1.0, 1.0), dtype=np.float32)
        if valid:
            previous = current.copy()
        cx, cy, bw, bh = [float(value) for value in current]
        half_w = max(bw * scale * 0.5, 0.12)
        half_h = max(bh * scale * 0.5, 0.12)
        x1 = int(round(np.clip(cx - half_w, 0.0, 1.0) * w))
        y1 = int(round(np.clip(cy - half_h, 0.0, 1.0) * h))
        x2 = int(round(np.clip(cx + half_w, 0.0, 1.0) * w))
        y2 = int(round(np.clip(cy + half_h, 0.0, 1.0) * h))
        crop = frame[max(0, y1):max(y1 + 2, y2), max(0, x1):max(x1 + 2, x2)]
        crops.append(crop if crop.size else frame)
    return crops


def frame_integrity(frame):
    """Reject decoder-corrupted flat frames before they reach JEPA/Pose.

    A normal dim scene still has edges and local contrast. HEVC reference
    corruption observed in deployment produces near-uniform grey blocks with
    both values close to zero, so use their conjunction rather than brightness.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    contrast = float(gray.std())
    low, high = np.percentile(gray, (5, 95))
    range90 = float(high - low)
    edges = float(cv2.Canny(gray, 50, 150).mean() / 255.0)
    valid = not (contrast < 8.0 and range90 < 12.0 and edges < 0.015)
    return valid, contrast, range90, edges


def history_integrity(frames):
    measurements = [frame_integrity(frame) for frame in frames]
    valid_count = sum(item[0] for item in measurements)
    count = max(len(measurements), 1)
    return {
        "valid": valid_count / count >= 0.80,
        "valid_fraction": float(valid_count / count),
        "contrast": float(np.median([item[1] for item in measurements])),
        "range90": float(np.median([item[2] for item in measurements])),
        "edge_density": float(np.median([item[3] for item in measurements])),
    }


def draw_pose(frame, pose):
    """Draw the latest normalized 17-keypoint pose on every output frame."""
    if pose is None:
        return
    pose = np.asarray(pose)
    height, width = frame.shape[:2]
    points = np.round(pose[:, :2] * np.array([width, height], dtype=np.float32)).astype(np.int32)
    for first, second in SKELETON:
        if pose[first, 2] >= 0.25 and pose[second, 2] >= 0.25:
            cv2.line(frame, tuple(points[first]), tuple(points[second]), (255, 190, 40), 2, cv2.LINE_AA)
    for index, point in enumerate(points):
        confidence = float(pose[index, 2])
        if confidence < 0.25:
            continue
        cv2.circle(frame, tuple(point), 5, (40, 235, 255), -1, cv2.LINE_AA)
        cv2.circle(frame, tuple(point), 6, (15, 35, 45), 1, cv2.LINE_AA)
        cv2.putText(frame, str(index), (int(point[0]) + 6, int(point[1]) - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.34, (235, 245, 250), 1, cv2.LINE_AA)


def draw_predicted_pose(frame, pose, visibility):
    """Draw the final predicted future Pose in amber without hiding current Pose."""
    if pose is None:
        return
    pose = np.asarray(pose, dtype=np.float32)
    visibility = np.ones(pose.shape[0], dtype=np.float32) if visibility is None else np.asarray(visibility)
    h, w = frame.shape[:2]
    points = np.round(pose[:, :2] * np.array([w, h], dtype=np.float32)).astype(np.int32)
    for first, second in SKELETON:
        if visibility[first] >= 0.25 and visibility[second] >= 0.25:
            cv2.line(frame, tuple(points[first]), tuple(points[second]), (40, 180, 255), 1, cv2.LINE_AA)
    for index, point in enumerate(points):
        if visibility[index] >= 0.25:
            cv2.circle(frame, tuple(point), 3, (40, 180, 255), -1, cv2.LINE_AA)


def draw_compact_panel(frame, values, frame_no, timestamp, first_alarm_time,
                       context_seconds, forecast_seconds, has_prediction):
    """Render operational values only; no checkpoint or deployment metadata."""
    alarm = values["alarm"]
    warning = values.get("warning", False)
    video_valid = values.get("video_valid", True)
    state = "FALL" if alarm else ("VIDEO INVALID" if not video_valid else ("WARNING" if warning else ("WARMUP" if not has_prediction else "NO FALL")))
    state_color = (0, 0, 255) if alarm else ((100, 155, 255) if not video_valid else ((0, 205, 255) if warning else ((255, 220, 120) if not has_prediction else (80, 220, 105))))
    q = values.get("pose_quality", [0.0, 0.0, 0.0, 0.0])
    lines = [
        ("FALL DETECTION", (230, 235, 240), 0.58),
        (f"Status  {state}", state_color, 0.72),
        (f"p_final  {values['p_final']:.3f}   smooth  {values['smooth']:.3f}", (240, 240, 240), 0.49),
        (f"p_visual {values['p_visual']:.3f}   p_world {values['p_world']:.3f}", (215, 225, 235), 0.47),
        (f"p_future_warning {values.get('p_future_warning', 0.0):.3f}   threshold {values.get('warning_threshold', 0.0):.3f}", (0, 205, 255) if warning else (215, 225, 235), 0.42),
        (f"p_pose   {values['p_pose']:.3f}   audio gate {values['gate']:.3f}", (215, 225, 235), 0.47),
        (f"Pose conf {q[0]:.2f}  visible {q[1]:.2f}  reliable {values['pose_reliability']:.2f}", (215, 225, 235), 0.43),
        (f"Video {'OK' if video_valid else 'INVALID'}  valid frames {values.get('video_valid_fraction', 0.0):.2f}", (120, 220, 255) if video_valid else (100, 155, 255), 0.43),
        (f"Audio  {'OK' if values['audio_valid'] else 'MISSING/INVALID'}", (120, 220, 255) if values['audio_valid'] else (170, 180, 190), 0.43),
        (f"World forecast  +{forecast_seconds:.2f}s   time {timestamp:.2f}s", (120, 220, 255), 0.43),
    ]
    if first_alarm_time is not None:
        lines.append((f"First alarm  {first_alarm_time:.2f}s", (255, 220, 120), 0.43))
    else:
        lines.append((f"History  {min(timestamp, context_seconds):.2f}/{context_seconds:.2f}s", (190, 205, 215), 0.43))

    font = cv2.FONT_HERSHEY_SIMPLEX
    line_height = 23
    text_x = 24
    first_baseline = 38
    text_width = max(
        cv2.getTextSize(text, font, scale, 1)[0][0]
        for text, _, scale in lines
    )
    panel_right = min(frame.shape[1] - 10, max(540, text_x + text_width + 18))
    panel_bottom = min(
        frame.shape[0] - 10,
        first_baseline + (len(lines) - 1) * line_height + 16,
    )
    overlay = frame.copy()
    cv2.rectangle(overlay, (10, 10), (panel_right, panel_bottom), (12, 18, 28), -1)
    cv2.addWeighted(overlay, 0.84, frame, 0.16, 0.0, frame)
    cv2.rectangle(frame, (10, 10), (panel_right, panel_bottom), (80, 92, 108), 1, cv2.LINE_AA)
    cv2.rectangle(frame, (10, 10), (16, panel_bottom), state_color, -1)
    divider_y = first_baseline + line_height + 9
    cv2.line(frame, (24, divider_y), (panel_right - 14, divider_y), (72, 84, 98), 1, cv2.LINE_AA)
    for i, (text, color, scale) in enumerate(lines):
        cv2.putText(
            frame, text, (text_x, first_baseline + i * line_height),
            font, scale, color, 1, cv2.LINE_AA,
        )


def pose_motion_risk(pose, boxes):
    """Estimate sustained fall-like motion from the observed pose trajectory."""
    pose = np.asarray(pose, dtype=np.float32)
    boxes = np.asarray(boxes, dtype=np.float32)
    if pose.ndim != 3 or boxes.ndim != 2 or len(pose) < 4:
        return 0.0
    valid = (boxes[:, 4] >= 0.15) & (pose[..., 2].mean(axis=1) >= 0.15)
    if valid.sum() < 4:
        return 0.0
    pair = valid[1:] & valid[:-1]
    if pair.sum() < 3:
        return 0.0
    center = boxes[:, :2]
    scale = np.maximum(boxes[:, 2:4].mean(axis=1), 0.05)
    center_speed = np.linalg.norm(np.diff(center, axis=0), axis=1) / scale[1:]
    joint_speed = np.linalg.norm(np.diff(pose[..., :2], axis=0), axis=2) / scale[1:, None]
    center_motion = float(np.median(center_speed[pair]))
    joint_motion = float(np.median(np.percentile(joint_speed[pair], 75, axis=1)))
    center_span = float(np.ptp(center[valid, 1]) / max(np.median(scale[valid]), 0.05))
    shoulder = (pose[:, 5, :2] + pose[:, 6, :2]) * 0.5
    hip = (pose[:, 11, :2] + pose[:, 12, :2]) * 0.5
    torso = shoulder - hip
    angles = np.arctan2(torso[:, 0], -torso[:, 1])
    angle_span = float(np.ptp(np.unwrap(angles[valid])))
    components = (
        np.clip(center_motion / 0.055, 0.0, 1.0),
        np.clip(joint_motion / 0.075, 0.0, 1.0),
        np.clip(center_span / 0.55, 0.0, 1.0),
        np.clip(angle_span / 0.85, 0.0, 1.0),
    )
    risk = 0.20 * components[0] + 0.25 * components[1] + 0.25 * components[2] + 0.30 * components[3]
    return float(np.clip(risk, 0.0, 1.0))


def draw(frame, p_visual, p_candidate, p_final, gate, alarm, audio_valid, pose_conflict, p_world, p_pose,
         frame_no, threshold,
         timestamp, first_alarm_time, impact_time, fall_start_time, context_seconds, forecast_seconds, has_prediction):
    overlay = frame.copy(); cv2.rectangle(overlay, (10, 10), (620, 290), (12, 18, 28), -1)
    cv2.addWeighted(overlay, .80, frame, .20, 0, frame)
    state = "FALL" if alarm else ("WATCH / POSE CONFLICT" if pose_conflict else "NO FALL")
    color = (0, 0, 255) if alarm else ((0, 200, 255) if pose_conflict else (80, 220, 105))
    if not has_prediction:
        lines = [("WORLD-POSE V3  |  AUDIO RESIDUAL", (230, 235, 240), .56),
                 ("WARMUP - collecting causal history", (255, 220, 120), .62),
                 (f"history {min(timestamp, context_seconds):.2f}s / {context_seconds:.2f}s", (205, 215, 225), .50),
                 (f"world forecast +{forecast_seconds:.2f}s after context is ready", (180, 205, 215), .44),
                 (f"frame {frame_no}", (180, 195, 205), .45)]
        for i, (text, c, scale) in enumerate(lines):
            cv2.putText(frame, text, (23, 35 + i * 28), cv2.FONT_HERSHEY_SIMPLEX, scale, c, 1, cv2.LINE_AA)
        return
    lines = [("WORLD-POSE V3  |  AUDIO RESIDUAL", (230, 235, 240), .56),
             (f"Decision: {state}", color, .74),
             (f"p_visual    {p_visual:.3f}", (235, 235, 235), .53),
             (f"p_candidate {p_candidate:.3f}   audio_gate {gate:.3f}", (205, 215, 225), .48),
             (f"p_final     {p_final:.3f}   audio {'OK' if audio_valid else 'MISSING'}", (205, 215, 225), .48),
             (f"p_world    {p_world:.3f}", (205, 215, 225), .45),
             (f"p_pose_future {p_pose:.3f}", (205, 215, 225), .45),
             (f"pose guard  {'CONFLICT / waiting' if pose_conflict else 'clear'}", (0, 200, 255) if pose_conflict else (180, 205, 215), .45),
             (f"threshold   {threshold:.3f}   frame {frame_no}", (180, 195, 205), .45),
             (f"history {context_seconds:.2f}s  ->  world forecast +{forecast_seconds:.2f}s", (180, 205, 215), .45)]
    if first_alarm_time is not None:
        lines.append((f"first alarm  {first_alarm_time:.2f}s", (255, 220, 120), .45))
    if impact_time is not None and first_alarm_time is not None:
        lines.append((f"lead to impact  {impact_time - first_alarm_time:+.2f}s", (120, 240, 180), .50))
    elif fall_start_time is not None and first_alarm_time is not None:
        lines.append((f"lead to fall-start  {fall_start_time - first_alarm_time:+.2f}s", (120, 240, 180), .50))
    elif impact_time is not None or fall_start_time is not None:
        lines.append(("lead time  waiting for first alarm", (190, 200, 210), .45))
    else:
        lines.append(("lead time  use --impact-time for labelled video", (150, 170, 185), .40))
    for i, (text, c, scale) in enumerate(lines):
        cv2.putText(frame, text, (23, 35 + i * 27), cv2.FONT_HERSHEY_SIMPLEX, scale, c, 1, cv2.LINE_AA)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--video", required=True); p.add_argument("--output", required=True)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--pose-device", default="")
    p.add_argument("--no-audio", action="store_true")
    p.add_argument("--model-path", default="",
                   help="Optional AV residual checkpoint. Defaults to the v2.2 World-Pose AV deployment checkpoint.")
    p.add_argument("--threshold", type=float, default=None)
    p.add_argument(
        "--warning-model-path", default="",
        help="Optional visual checkpoint containing the separately trained Future-Pose Warning Head. Uses the packaged head when available.",
    )
    p.add_argument("--warning-threshold", type=float, default=None)
    p.add_argument("--warning-min-consecutive", type=int, default=1)
    p.add_argument(
        "--fast-confirm-threshold", type=float, default=0.93,
        help="One-window FALL confirmation threshold; still requires strong observed-Pose motion evidence.",
    )
    p.add_argument("--sample-fps", type=float, default=16.0,
                   help="Causal sampling rate used to define the 32-frame history and future horizon.")
    p.add_argument("--inference-hz", type=float, default=4.0)
    p.add_argument("--pose-display-hz", type=float, default=12.0,
                   help="Refresh the drawn current Pose this often; the skeleton remains on every frame.")
    p.add_argument("--min-consecutive", type=int, default=2)
    p.add_argument("--impact-time", type=float, default=None,
                   help="Optional labelled impact time in seconds for offline lead-time reporting.")
    p.add_argument("--fall-start-time", type=float, default=None,
                   help="Optional labelled fall-start time in seconds for offline lead-time reporting.")
    p.add_argument("--predictions-csv", default="",
                   help="Optional per-inference CSV for probability/lead-time analysis.")
    p.add_argument("--timing-json", default="",
                   help="Optional runtime timing summary. Does not change model inference or alarm policy.")
    args = p.parse_args()
    runtime_started = time.perf_counter()
    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened(): raise RuntimeError(args.video)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0); total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(args.output, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    ckpt_path = Path(args.model_path) if args.model_path else ROOT / "models" / "world_pose_audio_residual_v2_2_gmd_full.pth"
    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    visual_cfg = WorldPoseFutureConfig(**ckpt["visual_config"])
    warning_ckpt = None
    packaged_warning = ROOT / "models" / "world_pose_future_warning_v2_2_gmd_full.pth"
    warning_path = Path(args.warning_model_path) if args.warning_model_path else packaged_warning
    if warning_path.is_file():
        warning_ckpt = torch.load(warning_path, map_location="cpu", weights_only=False)
        visual_cfg = replace(visual_cfg, future_pose_warning_head=True)
    model = WorldPoseAudioResidualFusion(visual_cfg, freeze_visual=True, freeze_audio=True).to(args.device).eval()
    missing, unexpected = model.load_state_dict(ckpt["model_state_dict"], strict=False)
    allowed_missing = {key for key in missing if key.startswith("visual.future_pose_risk_head.")}
    if unexpected or len(allowed_missing) != len(missing):
        raise RuntimeError(f"AV checkpoint mismatch: missing={missing}, unexpected={unexpected}")
    if warning_ckpt is not None:
        warning_state = warning_ckpt["model_state_dict"]
        warning_keys = {
            key: value for key, value in warning_state.items()
            if key.startswith("future_pose_risk_head.")
        }
        if not warning_keys:
            raise RuntimeError("Warning checkpoint does not contain a Future-Pose Warning Head.")
        visual_state = model.visual.state_dict()
        compatible_warning = {
            key: value for key, value in warning_keys.items()
            if key in visual_state and visual_state[key].shape == value.shape
        }
        if len(compatible_warning) != len(warning_keys):
            raise RuntimeError("Warning checkpoint has incompatible Future-Pose Warning Head tensors.")
        visual_state.update(compatible_warning)
        model.visual.load_state_dict(visual_state, strict=True)
    vjepa_cfg = VJEPA2WorldVisualConfig(repo_path=str(ROOT / "third_party" / "vjepa2_runtime"),
        checkpoint_path=str(ROOT / "models" / "vjepa2_1_vitl_dist_vitG_384.pt"),
        token_adapter_path=str(ROOT / "models" / "world_token_adapter_v1.pth"), require_token_adapter=True,
        context_frames=visual_cfg.context_frames, future_frames=visual_cfg.future_frames, image_size=384)
    encoder = VJEPA2WorldVisualEncoder(vjepa_cfg, load_weights=True).to(args.device).eval()
    from ultralytics import YOLO
    pose_model = YOLO(str(ROOT / "models" / "yolo11n-pose.pt")); pose_device = args.pose_device or args.device
    y, sr = (None, None) if args.no_audio else extract_audio(args.video, 22050)
    threshold = float(ckpt.get("decision_threshold", .7834) if args.threshold is None else args.threshold)
    warning_threshold = float(
        warning_ckpt.get("warning_threshold", .70) if args.warning_threshold is None and warning_ckpt is not None
        else (.70 if args.warning_threshold is None else args.warning_threshold)
    )
    if args.sample_fps <= 0 or args.inference_hz <= 0:
        raise ValueError("sample-fps and inference-hz must be positive")
    context_seconds = (visual_cfg.context_frames - 1) / args.sample_fps
    forecast_seconds = visual_cfg.future_frames / args.sample_fps
    # At low-FPS sources (such as test3 at about 10 FPS), 32 physical frames
    # would cover over three seconds. Keep the intended temporal span and let
    # nearest-neighbour resampling repeat frames when the source is below 16 FPS.
    required_history = max(2, int(round(context_seconds * fps)) + 1)
    frames = deque(maxlen=required_history + 4); raw_consecutive = 0; smooth_consecutive = 0; warning_consecutive = 0; frame_no = 0; next_infer = 0.0
    next_pose_display = 0.0
    latest_pose = None
    latest_predicted_pose = None
    latest_predicted_visibility = None
    pose_quality = (0.0, 0.0, 0.0, 0.0)
    smoothed = 0.0
    first_alarm_time = None
    has_prediction = False
    alarm_confirmed = False
    prediction_rows = []
    timing = {
        "display_pose_seconds": 0.0,
        "window_pose_seconds": 0.0,
        "full_jepa_seconds": 0.0,
        "crop_jepa_seconds": 0.0,
        "audio_feature_seconds": 0.0,
        "fusion_seconds": 0.0,
        "inference_window_seconds": 0.0,
        "inference_windows": 0,
    }
    loop_started = time.perf_counter()
    last = {"p_visual": 0.0, "p_candidate": 0.0, "p_final": 0.0, "gate": 0.0,
            "alarm": False, "warning": False, "p_future_warning": 0.0, "warning_threshold": warning_threshold,
            "video_valid": True, "video_valid_fraction": 1.0,
            "audio_valid": False, "p_world": 0.0, "p_pose": 0.0,
            "pose_reliability": 0.0, "pose_quality": pose_quality, "smooth": 0.0}
    while True:
        ok, frame = cap.read()
        if not ok: break
        frames.append(frame.copy()); t = frame_no / fps
        if t >= next_pose_display:
            synchronize_cuda(pose_device)
            pose_display_started = time.perf_counter()
            display_pose, _ = pose_for_frames(pose_model, [frame], pose_device)
            synchronize_cuda(pose_device)
            timing["display_pose_seconds"] += time.perf_counter() - pose_display_started
            latest_pose = display_pose[0]
            next_pose_display = t + 1.0 / max(args.pose_display_hz, 1.0)
        if len(frames) >= required_history and t >= next_infer:
            inference_started = time.perf_counter()
            # Resample the raw video buffer to the fixed 16 FPS temporal
            # contract used by V-JEPA2 and by the world-model checkpoint.
            raw_history = list(frames)
            indices = np.linspace(0, len(raw_history) - 1, visual_cfg.context_frames).round().astype(int)
            sampled = [raw_history[index] for index in indices]
            integrity = history_integrity(sampled)
            synchronize_cuda(pose_device)
            window_pose_started = time.perf_counter()
            pose, box = pose_for_frames(pose_model, sampled, pose_device)
            synchronize_cuda(pose_device)
            timing["window_pose_seconds"] += time.perf_counter() - window_pose_started
            latest_pose = pose[-1]
            rgb = [cv2.cvtColor(cv2.resize(x, (384, 384)), cv2.COLOR_BGR2RGB) for x in sampled]
            image = torch.from_numpy(np.asarray(rgb, dtype=np.uint8)).permute(0, 3, 1, 2).unsqueeze(0).to(args.device)
            synchronize_cuda(args.device)
            full_jepa_started = time.perf_counter()
            bundle = encoder.extract_token_bundle(image)
            synchronize_cuda(args.device)
            timing["full_jepa_seconds"] += time.perf_counter() - full_jepa_started
            crop_frames = person_crop_frames(sampled, box)
            crop_rgb = [cv2.cvtColor(cv2.resize(x, (384, 384)), cv2.COLOR_BGR2RGB) for x in crop_frames]
            crop_image = torch.from_numpy(np.asarray(crop_rgb, dtype=np.uint8)).permute(0, 3, 1, 2).unsqueeze(0).to(args.device)
            synchronize_cuda(args.device)
            crop_jepa_started = time.perf_counter()
            crop_bundle = encoder.extract_token_bundle(crop_image)
            synchronize_cuda(args.device)
            timing["crop_jepa_seconds"] += time.perf_counter() - crop_jepa_started
            audio_started = time.perf_counter()
            mel, valid = audio_window(y, sr, t)
            timing["audio_feature_seconds"] += time.perf_counter() - audio_started
            audio = torch.from_numpy(mel).unsqueeze(0).to(args.device)
            valid_t = torch.tensor([float(valid)], device=args.device)
            pt = torch.from_numpy(pose).unsqueeze(0).to(args.device); bt = torch.from_numpy(box).unsqueeze(0).to(args.device)
            synchronize_cuda(args.device)
            fusion_started = time.perf_counter()
            with torch.inference_mode():
                out = model(bundle['world_tokens'], bundle['current_temporal_tokens'], bundle['future_temporal_tokens'], pt, bt, None, None, audio, valid_t, None, None, None, None, crop_bundle['world_tokens'])
            synchronize_cuda(args.device)
            timing["fusion_seconds"] += time.perf_counter() - fusion_started
            timing["inference_window_seconds"] += time.perf_counter() - inference_started
            timing["inference_windows"] += 1
            visual_logit = float(out['visual_logit'][0]); raw_delta = float(out['raw_delta'][0])
            pv=float(torch.sigmoid(out['visual_logit'])[0]); pc=float(out['candidate_logits'].softmax(1)[0,1]); pf=float(out['final_probs'][0,1]); gate=float(out['utility_gate'][0])
            p_world = float(out['world_logits'].softmax(1)[0, 1])
            p_world_base = float(out['world_base_logits'].softmax(1)[0, 1])
            p_warning = float(
                out.get('warning_probs', out['world_logits'].softmax(1)) [0, 1]
            )
            domain_reliability = float(out.get('domain_reliability', torch.ones(1, device=out['world_logits'].device))[0])
            p_crop = float(out['crop_logits'].softmax(1)[0, 1]) if out.get('crop_logits') is not None else 0.0
            crop_gate = float(out['crop_gate'][0]) if out.get('crop_gate') is not None else 0.0
            p_pose = float(out['pose_logits'].softmax(1)[0, 1])
            p_future_warning = float(
                out['future_pose_led_logits'].softmax(1)[0, 1]
            ) if out.get('future_pose_led_logits') is not None else 0.0
            pose_reliability = float(out['pose_reliability'][0])
            pose_rescue = float(out['pose_rescue'][0])
            pose_effect = float(out['pose_rescue_effect'][0])
            pose_verifier_gate = 0.0
            motion_risk = pose_motion_risk(pose, box)
            pose_evidence = p_pose >= 0.20 or motion_risk >= 0.50
            pose_conflict = False
            pose_quality = tuple(float(x) for x in out['pose_quality'][0].detach().cpu().tolist())
            latest_predicted_pose = out['pose_future_coordinates'][0].detach().cpu().numpy()
            latest_predicted_visibility = torch.sigmoid(out['pose_future_visibility_logits'][0]).detach().cpu().numpy()
            smoothed = pf if not has_prediction else 0.45 * pf + 0.55 * smoothed
            raw_consecutive = raw_consecutive + 1 if integrity["valid"] and pf >= threshold else 0
            smooth_consecutive = smooth_consecutive + 1 if integrity["valid"] and smoothed >= threshold else 0
            warning_consecutive = (
                warning_consecutive + 1
                if integrity["valid"] and p_future_warning >= warning_threshold
                else 0
            )
            warning = integrity["valid"] and warning_consecutive >= max(1, args.warning_min_consecutive)
            # Either criterion is valid: raw probability preserves recall for
            # a fast fall, while EMA persistence suppresses isolated spikes.
            fast_confirm = (
                pf >= args.fast_confirm_threshold
                and pose_evidence
            )
            if integrity["valid"] and (fast_confirm or max(raw_consecutive, smooth_consecutive) >= args.min_consecutive):
                alarm_confirmed = True
            alarm = alarm_confirmed
            if alarm and first_alarm_time is None:
                first_alarm_time = t
            last = {"p_visual": pv, "p_candidate": pc, "p_final": pf, "gate": gate,
                    "alarm": alarm, "warning": warning, "p_future_warning": p_future_warning,
                    "warning_threshold": warning_threshold, "video_valid": integrity["valid"],
                    "video_valid_fraction": integrity["valid_fraction"], "audio_valid": valid, "p_world": p_world,
                    "p_warning": p_warning, "domain_reliability": domain_reliability,
                    "p_pose": p_pose, "pose_reliability": pose_reliability,
                    "pose_quality": pose_quality, "smooth": smoothed}
            has_prediction = True
            prediction_rows.append({
                "frame": frame_no, "time_seconds": t, "p_visual": pv,
                "p_candidate": pc, "audio_gate": gate, "p_final": pf,
                "visual_logit": visual_logit, "raw_delta": raw_delta,
                "p_world": p_world, "p_world_base": p_world_base,
                "p_warning": p_warning, "domain_reliability": domain_reliability,
                "p_crop": p_crop, "crop_gate": crop_gate,
                "p_pose": p_pose,
                "p_future_warning": p_future_warning,
                "warning_threshold": warning_threshold,
                "warning": int(warning),
                "video_valid": int(integrity["valid"]),
                "video_valid_fraction": integrity["valid_fraction"],
                "video_contrast": integrity["contrast"],
                "video_range90": integrity["range90"],
                "video_edge_density": integrity["edge_density"],
                "pose_reliability": pose_reliability, "pose_rescue": pose_rescue,
                "pose_rescue_effect": pose_effect,
                "pose_verifier_gate": pose_verifier_gate,
                "pose_motion_risk": motion_risk,
                "pose_evidence": int(pose_evidence),
                "audio_valid": int(valid), "pose_conflict": int(pose_conflict),
                "alarm_raw": int(pf >= threshold), "alarm": int(alarm),
                "fast_confirm": int(fast_confirm),
                "first_alarm_time": "" if first_alarm_time is None else first_alarm_time,
                "impact_time": "" if args.impact_time is None else args.impact_time,
                "lead_to_impact": "" if args.impact_time is None or first_alarm_time is None else args.impact_time - first_alarm_time,
            })
            next_infer = t + 1.0 / args.inference_hz
        draw_pose(frame, latest_pose)
        draw_predicted_pose(frame, latest_predicted_pose[-1] if latest_predicted_pose is not None else None,
                            latest_predicted_visibility[-1] if latest_predicted_visibility is not None else None)
        draw_compact_panel(frame, last, frame_no, t, first_alarm_time,
                           context_seconds, forecast_seconds, has_prediction)
        writer.write(frame); frame_no += 1
    cap.release(); writer.release()
    loop_seconds = time.perf_counter() - loop_started
    total_seconds = time.perf_counter() - runtime_started
    if args.predictions_csv and prediction_rows:
        csv_path = Path(args.predictions_csv)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer_csv = csv.DictWriter(handle, fieldnames=prediction_rows[0].keys())
            writer_csv.writeheader(); writer_csv.writerows(prediction_rows)
        print(f"[Runtime] wrote {csv_path}")
    if args.timing_json:
        timing_path = Path(args.timing_json)
        timing_path.parent.mkdir(parents=True, exist_ok=True)
        count = max(int(timing["inference_windows"]), 1)
        duration = frame_no / max(fps, 1e-6)
        timing_summary = {
            "video": str(args.video),
            "model": str(ckpt_path),
            "warning_model": str(warning_path) if warning_ckpt is not None else "",
            "frames": int(frame_no),
            "video_seconds": float(duration),
            "input_fps": float(fps),
            "startup_seconds": float(loop_started - runtime_started),
            "loop_seconds": float(loop_seconds),
            "total_seconds": float(total_seconds),
            "end_to_end_fps": float(frame_no / max(total_seconds, 1e-6)),
            "real_time_factor": float(duration / max(total_seconds, 1e-6)),
            "inference_windows": int(timing["inference_windows"]),
            "mean_window_seconds": float(timing["inference_window_seconds"] / count),
            "mean_window_fps_equivalent": float(1.0 / max(timing["inference_window_seconds"] / count, 1e-6)),
            **{key: float(value) for key, value in timing.items() if key != "inference_windows"},
        }
        import json
        with timing_path.open("w", encoding="utf-8") as handle:
            json.dump(timing_summary, handle, ensure_ascii=False, indent=2)
        print(f"[Runtime] wrote timing {timing_path}")
    print(f"[Runtime] wrote {args.output}; frames={frame_no}; final_alarm={last['alarm']}")


if __name__ == "__main__": main()
