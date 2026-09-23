"""Batch-test the deployable Spatial-v5 model on videos with one warm GPU.

The runner keeps exactly the same causal streaming path used for camera
deployment.  It does not alter sources, and it emits a compact summary with
alarm, Future-Pose warning, timing, and optional manually annotated lead time.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT = ROOT / "deployment_world_pose_av_v3"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DEPLOYMENT))

import run_world_pose_av_streaming as streaming
from scripts.run_arch_jepa_spatial_streaming import load_spatial_models


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_annotations(path: str) -> dict[str, dict[str, str]]:
    if not path:
        return {}
    result = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            key = str(row.get("video_id", "")).strip()
            if key:
                result[f"{key}.mp4"] = row
    return result


def read_prediction_summary(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return {}
    alarms = [row for row in rows if int(row["alarm"])]
    warnings = [row for row in rows if int(row["warning"])]
    return {
        "windows": len(rows),
        "max_p_final": max(float(row["p_final"]) for row in rows),
        "max_p_future_warning": max(float(row["p_future_warning"]) for row in rows),
        "alarm": int(bool(alarms)),
        "warning": int(bool(warnings)),
        "first_alarm_seconds": float(alarms[0]["time_seconds"]) if alarms else None,
        "first_warning_seconds": float(warnings[0]["time_seconds"]) if warnings else None,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Batch streaming evaluation for Spatial-v5")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--model-path",
        default=str(ROOT / "deployment_world_pose_av_v3" / "models" / "v5_temporal_residual_full_v1.pth"),
    )
    parser.add_argument("--annotations-csv", default="")
    parser.add_argument("--normal-hz", type=float, default=4.0)
    parser.add_argument("--active-hz", type=float, default=4.0)
    parser.add_argument("--threshold", type=float, default=None,
                        help="Optional fixed deployment decision threshold passed to every video run.")
    parser.add_argument(
        "--pose-mode",
        choices=("window", "incremental", "sampled_incremental"),
        default="sampled_incremental",
    )
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--reuse", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main():
    args = parse_args()
    input_dir, output_dir = Path(args.input_dir), Path(args.output_dir)
    videos = sorted(path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() in {".mp4", ".avi", ".mov", ".mkv"})
    if not videos:
        raise FileNotFoundError(f"No videos found under {input_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    annotations = read_annotations(args.annotations_csv)

    manifest = []
    for video in videos:
        manifest.append({"video": video.name, "sha256": sha256(video), "source": str(video)})
    with (output_dir / "input_manifest.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("video", "sha256", "source"))
        writer.writeheader(); writer.writerows(manifest)

    resource_args = SimpleNamespace(model_path=args.model_path, warning_model_path="", device=args.device, tf32=True, compile_encoder=False)
    resources = load_spatial_models(resource_args)
    from ultralytics import YOLO
    pose_model = YOLO(str(DEPLOYMENT / "models" / "yolo11n-pose.pt"))
    original_load, streaming.load_models = streaming.load_models, lambda _: resources
    import ultralytics
    original_yolo, ultralytics.YOLO = ultralytics.YOLO, lambda _: pose_model
    results = []
    inferred = False
    try:
        for index, video in enumerate(videos, 1):
            stem = video.stem
            output = output_dir / f"{stem}_spatial_v5.mp4"
            prediction = output_dir / f"{stem}.csv"
            timing = output_dir / f"{stem}_timing.json"
            reusable = args.reuse and output.is_file() and prediction.is_file() and timing.is_file() and output.stat().st_size > 1024
            if reusable:
                print(f"[SpatialV5Batch] reuse {index}/{len(videos)} {video.name}", flush=True)
            else:
                print(f"[SpatialV5Batch] start {index}/{len(videos)} {video.name}", flush=True)
                sys.argv = [
                    "run_world_pose_av_streaming.py", "--video", str(video), "--output", str(output),
                    "--predictions-csv", str(prediction), "--timing-json", str(timing),
                    "--normal-hz", str(args.normal_hz), "--active-hz", str(args.active_hz),
                    "--pose-mode", args.pose_mode, "--long-view-seconds", "6", "--long-view-refresh", "6",
                    "--device", args.device, "--model-path", args.model_path,
                ]
                if args.threshold is not None:
                    sys.argv.extend(("--threshold", str(args.threshold)))
                if inferred:
                    sys.argv.append("--no-warmup")
                streaming.main(); inferred = True
            item = {"video": video.name, **read_prediction_summary(prediction)}
            with timing.open("r", encoding="utf-8") as handle:
                timing_values = json.load(handle)
            item.update({key: timing_values.get(key, "") for key in (
                "video_seconds", "loop_seconds", "total_seconds", "real_time_factor",
                "inference_windows", "mean_window_seconds", "mean_window_hz",
            )})
            annotation = annotations.get(video.name, {})
            item["manual_label"] = annotation.get("video_label", "")
            item["manual_label_name"] = annotation.get("video_label_name", "")
            impact = annotation.get("approx_impact_seconds", "")
            item["annotated_impact_seconds"] = impact
            item["warning_lead_to_annotated_impact"] = (
                float(impact) - item["first_warning_seconds"]
                if impact and item.get("first_warning_seconds") is not None else ""
            )
            item["alarm_lead_to_annotated_impact"] = (
                float(impact) - item["first_alarm_seconds"]
                if impact and item.get("first_alarm_seconds") is not None else ""
            )
            results.append(item)
            print(f"[SpatialV5Batch] done {index}/{len(videos)} alarm={item['alarm']} warning={item['warning']} rtf={item.get('real_time_factor')}", flush=True)
    finally:
        streaming.load_models = original_load
        ultralytics.YOLO = original_yolo
    fields = [
        "video", "windows", "max_p_final", "max_p_future_warning", "alarm", "warning",
        "first_alarm_seconds", "first_warning_seconds", "video_seconds", "loop_seconds", "total_seconds",
        "real_time_factor", "inference_windows", "mean_window_seconds", "mean_window_hz",
        "manual_label", "manual_label_name", "annotated_impact_seconds",
        "warning_lead_to_annotated_impact", "alarm_lead_to_annotated_impact",
    ]
    with (output_dir / "spatial_v5_batch_summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(results)
    print(f"[SpatialV5Batch] complete videos={len(results)} output={output_dir}", flush=True)


if __name__ == "__main__":
    main()
