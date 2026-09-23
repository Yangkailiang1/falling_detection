"""Pose-estimator backends used by data preparation and video evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import torch


@dataclass
class PoseDetection:
    keypoints_xy: np.ndarray
    keypoints_xyn: np.ndarray
    confidences: np.ndarray

    def as_keypoints(self):
        return KeypointsAdapter(self)


class KeypointsAdapter:
    """Small adapter matching the keypoint fields used from Ultralytics results."""

    def __init__(self, detection: PoseDetection):
        self.xy = torch.from_numpy(detection.keypoints_xy.astype(np.float32)).unsqueeze(0)
        self.xyn = torch.from_numpy(detection.keypoints_xyn.astype(np.float32)).unsqueeze(0)
        self.conf = torch.from_numpy(detection.confidences.astype(np.float32)).unsqueeze(0)


def _pad_coco17(keypoints: np.ndarray, scores: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    keypoints = np.asarray(keypoints, dtype=np.float32)
    scores = np.asarray(scores, dtype=np.float32).reshape(-1)
    if keypoints.ndim != 2 or keypoints.shape[1] < 2:
        return np.zeros((17, 2), dtype=np.float32), np.zeros(17, dtype=np.float32)
    keypoints = keypoints[:, :2]
    out_kp = np.zeros((17, 2), dtype=np.float32)
    out_conf = np.zeros(17, dtype=np.float32)
    count = min(17, keypoints.shape[0])
    out_kp[:count] = keypoints[:count]
    if scores.size:
        out_conf[: min(17, scores.size)] = scores[: min(17, scores.size)]
    else:
        out_conf[:count] = 1.0
    return out_kp, np.clip(out_conf, 0.0, 1.0)


class YoloPoseEstimator:
    def __init__(self, model_path: str, device: str = ""):
        from ultralytics import YOLO

        self.backend_name = "yolo"
        self.model = YOLO(model_path)
        self.device = device

    def _detection_from_result(self, result, conf_threshold: float = 0.5) -> PoseDetection | None:
        if result is None or len(result.boxes) == 0:
            return None
        keypoints = result.keypoints
        if keypoints is None or len(keypoints.xy) == 0:
            return None
        xy = keypoints.xy[0].detach().cpu().numpy().astype(np.float32)
        xyn = keypoints.xyn[0].detach().cpu().numpy().astype(np.float32)
        conf = keypoints.conf[0].detach().cpu().numpy().astype(np.float32)
        xy, conf = _pad_coco17(xy, conf)
        xyn, _ = _pad_coco17(xyn, conf)
        if float(conf.mean() if conf.size else 0.0) < conf_threshold:
            return None
        return PoseDetection(xy, np.clip(xyn, 0.0, 1.0), conf)

    def detect(self, frame: np.ndarray, conf_threshold: float = 0.5) -> PoseDetection | None:
        kwargs = {"device": self.device} if self.device else {}
        results = self.model.predict(source=frame, conf=conf_threshold, imgsz=320, verbose=False, **kwargs)
        if not results:
            return None
        return self._detection_from_result(results[0], conf_threshold)

    def detect_batch(self, frames: list[np.ndarray], conf_threshold: float = 0.5) -> list[PoseDetection | None]:
        if not frames:
            return []
        kwargs = {"device": self.device} if self.device else {}
        results = self.model.predict(source=frames, conf=conf_threshold, imgsz=320, verbose=False, **kwargs)
        return [self._detection_from_result(result, conf_threshold) for result in results]


class ViTPoseEstimator:
    """ViTPose through MMPose Inferencer.

    Install MMPose/MMCV/MMEngine and provide ViTPose config/checkpoint paths, or
    use a valid MMPose inferencer alias through ``pose_config``.
    """

    def __init__(
        self,
        pose_config: str = "",
        pose_checkpoint: str = "",
        det_config: str = "",
        det_checkpoint: str = "",
        device: str = "",
    ):
        try:
            from mmpose.apis import MMPoseInferencer
        except Exception as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "ViTPose backend requires MMPose. Install a compatible mmpose/mmcv/mmengine stack "
                "and pass --pose-backend vitpose with ViTPose config/checkpoint paths."
            ) from exc

        kwargs: dict[str, Any] = {}
        if pose_config:
            kwargs["pose2d"] = pose_config
        if pose_checkpoint:
            kwargs["pose2d_weights"] = pose_checkpoint
        if det_config:
            kwargs["det_model"] = det_config
        if det_checkpoint:
            kwargs["det_weights"] = det_checkpoint
        if device:
            kwargs["device"] = device
        if "pose2d" not in kwargs:
            kwargs["pose2d"] = "human"
        self.backend_name = "vitpose"
        self.inferencer = MMPoseInferencer(**kwargs)

    def detect(self, frame: np.ndarray, conf_threshold: float = 0.5) -> PoseDetection | None:
        result_iter = self.inferencer(frame, show=False, return_vis=False)
        result = next(result_iter, None)
        people = list(_flatten_predictions(result.get("predictions", []) if isinstance(result, dict) else []))
        if not people:
            return None

        best = None
        best_score = -1.0
        for person in people:
            keypoints = person.get("keypoints", None)
            if keypoints is None:
                continue
            scores = person.get("keypoint_scores", person.get("keypoints_visible", []))
            score_arr = np.asarray(scores, dtype=np.float32).reshape(-1)
            bbox_score = person.get("bbox_score", None)
            if isinstance(bbox_score, (list, tuple, np.ndarray)):
                bbox_score = float(np.asarray(bbox_score).reshape(-1)[0])
            score = float(bbox_score) if bbox_score is not None else float(score_arr.mean() if score_arr.size else 0.0)
            if score > best_score:
                best_score = score
                best = person
        if best is None or best_score < conf_threshold:
            return None

        xy, conf = _pad_coco17(best.get("keypoints", []), best.get("keypoint_scores", []))
        height, width = frame.shape[:2]
        max_coord = float(np.max(xy)) if xy.size else 0.0
        if max_coord <= 1.5:
            xyn = xy.copy()
            xy = xy * np.asarray([max(width, 1), max(height, 1)], dtype=np.float32)
        else:
            xyn = xy / np.asarray([max(width, 1), max(height, 1)], dtype=np.float32)
        return PoseDetection(xy.astype(np.float32), np.clip(xyn, 0.0, 1.0).astype(np.float32), conf)


def _flatten_predictions(value: Any) -> Iterable[dict]:
    if isinstance(value, dict):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten_predictions(item)


def create_pose_estimator(
    backend: str = "yolo",
    model_path: str = "",
    vitpose_config: str = "",
    vitpose_checkpoint: str = "",
    vitpose_det_config: str = "",
    vitpose_det_checkpoint: str = "",
    device: str = "",
):
    backend = backend.lower().strip()
    if backend == "yolo":
        if not model_path:
            raise ValueError("YOLO pose backend requires model_path")
        return YoloPoseEstimator(model_path, device=device)
    if backend == "vitpose":
        return ViTPoseEstimator(
            pose_config=vitpose_config,
            pose_checkpoint=vitpose_checkpoint,
            det_config=vitpose_det_config,
            det_checkpoint=vitpose_det_checkpoint,
            device=device,
        )
    raise ValueError(f"Unsupported pose backend: {backend}")
