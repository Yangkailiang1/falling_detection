"""Serial post-alarm pose analysis for side-contact and medical risk warnings.

Pose is intentionally outside the trained V-JEPA2/audio classifier.  The
caller starts this analyzer only after the main AV detector raises a sustained
fall candidate.  A short raw-frame lookback is analyzed in batch so the first
contact is not missed when the AV warning happens before impact.

The output is a screening warning, not a medical diagnosis.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

import cv2
import numpy as np


LEFT_POINTS = (5, 7, 9, 11, 13, 15)
RIGHT_POINTS = (6, 8, 10, 12, 14, 16)
HEAD_POINTS = (0, 1, 2, 3, 4)
TORSO_POINTS = (5, 6, 11, 12)
CONTACT_NAMES = {
    0: "head",
    1: "left_eye",
    2: "right_eye",
    3: "left_ear",
    4: "right_ear",
    5: "left_shoulder",
    6: "right_shoulder",
    7: "left_elbow",
    8: "right_elbow",
    9: "left_wrist",
    10: "right_wrist",
    11: "left_hip",
    12: "right_hip",
    13: "left_knee",
    14: "right_knee",
    15: "left_ankle",
    16: "right_ankle",
}
HEAD_OR_TORSO_NAMES = {
    CONTACT_NAMES[index] for index in HEAD_POINTS + TORSO_POINTS
}


@dataclass
class PoseFrame:
    frame_index: int
    keypoints: np.ndarray
    confidence: np.ndarray


@dataclass
class SideContact:
    side: str
    frame_index: int
    body_part: str
    score: float
    confidence: float
    support_score: float = 0.0
    velocity_score: float = 0.0


@dataclass
class MedicalWarning:
    active: bool = False
    risk_level: str = "none"
    first_side: str = "uncertain"
    first_contact_frame: int = -1
    first_contact_part: str = "unknown"
    first_contact_confidence: float = 0.0
    first_contact_offset_seconds: float = 0.0
    second_side: str = "uncertain"
    second_contact_frame: int = -1
    second_contact_part: str = "unknown"
    second_contact_confidence: float = 0.0
    impact_score: float = 0.0
    head_or_torso_involved: bool = False
    no_recovery_observed: bool = False
    pose_quality: float = 0.0
    message: str = "No medical warning"
    disclaimer: str = "Screening aid only; not a medical diagnosis."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SerialPoseMedicalAnalyzer:
    """Analyze a buffered fall episode after the AV alarm is raised."""

    def __init__(
        self,
        pose_estimator: Any,
        fps: float,
        lookback_frames: int = 75,
        post_frames: int | None = None,
        min_contact_gap_frames: int = 3,
        confidence_threshold: float = 0.30,
        ground_line_y: float | None = None,
    ):
        self.pose_estimator = pose_estimator
        self.fps = max(float(fps), 1.0)
        self.lookback_frames = max(int(lookback_frames), 8)
        self.post_frames = max(int(post_frames or round(self.fps * 1.5)), 8)
        self.min_contact_gap_frames = max(int(min_contact_gap_frames), 1)
        self.confidence_threshold = float(confidence_threshold)
        self.ground_line_y = (
            float(np.clip(ground_line_y, 0.0, 1.0)) if ground_line_y is not None else None
        )
        self.trigger_frame = -1
        self.active = False
        self.finished = False
        self.frames: list[PoseFrame] = []
        self.warning = MedicalWarning()

    def begin(self, buffered_frames: Iterable[tuple[int, np.ndarray]], trigger_frame: int) -> MedicalWarning:
        """Start once; buffered frames include a pre-alarm lookback."""
        if self.active:
            return self.warning
        self.trigger_frame = int(trigger_frame)
        selected = list(buffered_frames)[-self.lookback_frames :]
        images = [frame for _, frame in selected]
        detections = self._detect_batch(images)
        self.frames = [
            PoseFrame(idx, kp, conf)
            for (idx, _), detection in zip(selected, detections)
            if (kp_conf := self._extract_detection(detection)) is not None
            for kp, conf in [kp_conf]
        ]
        self.active = True
        self.finished = False
        self.warning = self._analyze()
        return self.warning

    def begin_from_arrays(
        self,
        sequence: Iterable[tuple[int, np.ndarray, np.ndarray]],
        trigger_frame: int,
    ) -> MedicalWarning:
        """Start from an already-computed COCO-17 pose sequence."""
        if self.active:
            return self.warning
        self.trigger_frame = int(trigger_frame)
        self.frames = []
        for frame_index, keypoints, confidences in list(sequence)[-self.lookback_frames:]:
            points = np.asarray(keypoints, dtype=np.float32)
            conf = np.asarray(confidences, dtype=np.float32).reshape(-1)
            if points.shape != (17, 2) or conf.size != 17:
                continue
            self.frames.append(PoseFrame(
                int(frame_index), np.clip(points, 0.0, 1.0), np.clip(conf, 0.0, 1.0)
            ))
        self.active = True
        self.finished = False
        self.warning = self._analyze()
        return self.warning

    def update_from_arrays(
        self,
        frame_index: int,
        keypoints: np.ndarray,
        confidences: np.ndarray,
    ) -> MedicalWarning:
        """Append one cached pose after an alert without another detector call."""
        if not self.active or self.finished:
            return self.warning
        points = np.asarray(keypoints, dtype=np.float32)
        conf = np.asarray(confidences, dtype=np.float32).reshape(-1)
        if points.shape == (17, 2) and conf.size == 17:
            self.frames.append(PoseFrame(
                int(frame_index), np.clip(points, 0.0, 1.0), np.clip(conf, 0.0, 1.0)
            ))
        self.warning = self._analyze()
        if frame_index - self.trigger_frame >= self.post_frames:
            self.finished = True
        return self.warning

    def update(self, frame_index: int, frame: np.ndarray) -> MedicalWarning:
        """Add post-trigger frames until the analysis window is complete."""
        if not self.active or self.finished:
            return self.warning
        detection = self.pose_estimator.detect(frame, self.confidence_threshold)
        kp_conf = self._extract_detection(detection)
        if kp_conf is not None:
            keypoints, confidence = kp_conf
            self.frames.append(PoseFrame(int(frame_index), keypoints, confidence))
        self.warning = self._analyze()
        if frame_index - self.trigger_frame >= self.post_frames:
            self.finished = True
        return self.warning

    def _detect_batch(self, images: list[np.ndarray]) -> list[Any]:
        if not images:
            return []
        detect_batch = getattr(self.pose_estimator, "detect_batch", None)
        if callable(detect_batch):
            return list(detect_batch(images, self.confidence_threshold))
        return [self.pose_estimator.detect(image, self.confidence_threshold) for image in images]

    @staticmethod
    def _extract_detection(detection: Any) -> tuple[np.ndarray, np.ndarray] | None:
        if detection is None:
            return None
        keypoints = getattr(detection, "keypoints_xyn", None)
        confidence = getattr(detection, "confidences", None)
        if keypoints is None or confidence is None:
            return None
        keypoints = np.asarray(keypoints, dtype=np.float32)
        confidence = np.asarray(confidence, dtype=np.float32).reshape(-1)
        if keypoints.shape != (17, 2) or confidence.size != 17:
            return None
        return np.clip(keypoints, 0.0, 1.0), np.clip(confidence, 0.0, 1.0)

    def _quality(self) -> float:
        if not self.frames:
            return 0.0
        ratios = [float(np.mean(frame.confidence >= self.confidence_threshold)) for frame in self.frames]
        valid_frame_ratio = float(np.mean(np.asarray(ratios) >= 0.35))
        mean_confidence = float(np.mean([frame.confidence.mean() for frame in self.frames]))
        return float(np.clip(0.55 * valid_frame_ratio + 0.45 * mean_confidence, 0.0, 1.0))

    def _series(self) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        if len(self.frames) < 5:
            return None
        frames = np.asarray([frame.frame_index for frame in self.frames], dtype=np.int32)
        points = np.asarray([frame.keypoints for frame in self.frames], dtype=np.float32)
        confidence = np.asarray([frame.confidence for frame in self.frames], dtype=np.float32)
        return frames, points, confidence

    @staticmethod
    def _body_scale(points: np.ndarray, confidence: np.ndarray) -> np.ndarray:
        scale = np.zeros(points.shape[0], dtype=np.float32)
        for index in range(points.shape[0]):
            shoulder = points[index, [5, 6]]
            hip = points[index, [11, 12]]
            shoulder_ok = confidence[index, [5, 6]] >= 0.3
            hip_ok = confidence[index, [11, 12]] >= 0.3
            values = []
            if shoulder_ok.all():
                values.append(float(np.linalg.norm(shoulder[0] - shoulder[1])))
            if hip_ok.all():
                values.append(float(np.linalg.norm(hip[0] - hip[1])))
            if shoulder_ok.any() and hip_ok.any():
                values.append(float(np.linalg.norm(shoulder[shoulder_ok].mean(axis=0) - hip[hip_ok].mean(axis=0))))
            scale[index] = max(float(np.median(values)) if values else 0.1, 0.08)
        return scale

    @staticmethod
    def _group_indices(side: str) -> tuple[int, ...]:
        if side == "left":
            return LEFT_POINTS
        if side == "right":
            return RIGHT_POINTS
        if side == "center":
            # This is a medical-risk signal only. The reported first side is
            # still chosen from left/right anatomical contact candidates.
            return HEAD_POINTS + TORSO_POINTS
        raise ValueError(f"Unsupported contact group: {side}")

    def _point_contact(
        self,
        point_index: int,
        side: str,
        points: np.ndarray,
        confidence: np.ndarray,
        frame_ids: np.ndarray,
        scale: np.ndarray,
    ) -> SideContact | None:
        """Estimate one anatomical contact using 2-D motion and support cues.

        The ground cue is a calibrated line when supplied. Without calibration,
        the lowest reliable body points in the same frame form a conservative
        support-plane proxy. This is intentionally a screening estimate.
        """
        candidates: list[SideContact] = []
        for t in range(2, len(frame_ids) - 2):
            if confidence[t, point_index] < self.confidence_threshold:
                continue
            history_slice = slice(max(0, t - 8), t)
            previous_values = points[history_slice, point_index, 1]
            previous_ok = confidence[history_slice, point_index] >= self.confidence_threshold
            if not previous_ok.any() or confidence[t - 1, point_index] < self.confidence_threshold:
                continue
            previous_y = float(np.median(previous_values[previous_ok]))
            current_y = float(points[t, point_index, 1])
            drop = (current_y - previous_y) / max(float(scale[t]), 0.08)
            velocity = (current_y - float(points[t - 1, point_index, 1])) / max(float(scale[t]), 0.08)
            future_values = points[t + 1 : t + 4, point_index, 1]
            future_ok = confidence[t + 1 : t + 4, point_index] >= self.confidence_threshold
            if future_ok.any():
                stability = float(np.std(future_values[future_ok]))
            else:
                stability = 0.05
            visible = points[t, confidence[t] >= self.confidence_threshold, 1]
            if visible.size == 0:
                continue
            support_line = self.ground_line_y if self.ground_line_y is not None else float(np.quantile(visible, 0.85))
            support_score = float(np.clip(1.0 - abs(support_line - current_y) / 0.22, 0.0, 1.0))
            if drop < 0.16 or velocity < 0.025:
                continue
            velocity_score = float(np.clip(velocity / 0.16, 0.0, 1.0))
            stability_score = float(np.clip((0.05 - stability) / 0.05, 0.0, 1.0))
            score = float(np.clip(
                0.38 * min(drop / 0.8, 1.0)
                + 0.30 * velocity_score
                + 0.17 * stability_score
                + 0.15 * support_score,
                0.0,
                1.0,
            ))
            if score < 0.46:
                continue
            point_confidence = float(confidence[t, point_index])
            contact_confidence = float(np.clip(
                0.50 * score + 0.28 * self._quality() + 0.22 * point_confidence,
                0.0,
                1.0,
            ))
            candidates.append(SideContact(
                side=side,
                frame_index=int(frame_ids[t]),
                body_part=CONTACT_NAMES.get(point_index, "body"),
                score=score,
                confidence=contact_confidence,
                support_score=support_score,
                velocity_score=velocity_score,
            ))
        if not candidates:
            return None
        # A contact is temporal by definition: among high-confidence candidates
        # use the earliest frame, then prefer the stronger support/motion score.
        return sorted(candidates, key=lambda item: (item.frame_index, -item.score))[0]

    def _side_contact(self, side: str, points: np.ndarray, confidence: np.ndarray, frame_ids: np.ndarray, scale: np.ndarray) -> SideContact | None:
        contacts = [
            self._point_contact(point_index, side, points, confidence, frame_ids, scale)
            for point_index in self._group_indices(side)
        ]
        contacts = [item for item in contacts if item is not None]
        if not contacts:
            return None
        return sorted(contacts, key=lambda item: (item.frame_index, -item.score))[0]

    def _analyze(self) -> MedicalWarning:
        result = MedicalWarning(active=False, pose_quality=self._quality())
        series = self._series()
        if series is None or result.pose_quality < 0.25:
            result.message = "Pose evidence insufficient; keep AV alert and request human assessment."
            return result
        frame_ids, points, confidence = series
        scale = self._body_scale(points, confidence)
        left = self._side_contact("left", points, confidence, frame_ids, scale)
        right = self._side_contact("right", points, confidence, frame_ids, scale)
        center = self._side_contact("center", points, confidence, frame_ids, scale)
        contacts = sorted([item for item in (left, right) if item is not None], key=lambda item: item.frame_index)
        if not contacts:
            result.message = "Fall detected; side contact is uncertain. Keep the person still and assess."
            result.active = True
            result.risk_level = "medium"
            return result

        first = contacts[0]
        second = contacts[1] if len(contacts) > 1 else None
        if second and abs(second.frame_index - first.frame_index) < self.min_contact_gap_frames:
            first_side = "simultaneous"
        else:
            first_side = first.side
        # A central head/torso candidate within the fall episode upgrades the
        # screening risk, while left/right remains the answer to which side
        # contacted first.
        central_near_episode = (
            center is not None
            and abs(center.frame_index - first.frame_index) <= max(3, int(round(self.fps * 0.60)))
        )
        head_or_torso = first.body_part in HEAD_OR_TORSO_NAMES or central_near_episode
        # A conservative stillness signal: after the earliest contact, the hip
        # center stays nearly fixed while the torso remains close to horizontal.
        post_mask = frame_ids >= first.frame_index
        post_points = points[post_mask]
        no_recovery = False
        if len(post_points) >= 4:
            hip_valid = confidence[post_mask][:, [11, 12]] >= self.confidence_threshold
            hip_values = post_points[:, [11, 12], 1][hip_valid]
            if hip_values.size >= 4:
                no_recovery = float(np.std(hip_values)) < 0.035

        impact_candidates = contacts + ([center] if center is not None else [])
        impact_score = float(np.clip(max(item.score for item in impact_candidates) * 100.0, 0.0, 100.0))
        if head_or_torso and (impact_score >= 58.0 or no_recovery):
            risk = "critical"
            message = "URGENT medical warning: possible head/torso impact or no recovery; do not move the person and call medical help."
        elif head_or_torso or impact_score >= 45.0 or no_recovery:
            risk = "high"
            message = "High medical risk screen: check consciousness and breathing, keep the person still, and seek medical assessment."
        else:
            risk = "medium"
            message = "Fall confirmed by AV plus pose: monitor the person and arrange a medical check."
        result.active = True
        result.risk_level = risk
        result.first_side = first_side
        result.first_contact_frame = first.frame_index
        result.first_contact_part = first.body_part
        result.first_contact_confidence = first.confidence
        result.first_contact_offset_seconds = (first.frame_index - self.trigger_frame) / self.fps
        result.second_side = second.side if second else "uncertain"
        result.second_contact_frame = second.frame_index if second else -1
        result.second_contact_part = second.body_part if second else "unknown"
        result.second_contact_confidence = second.confidence if second else 0.0
        result.impact_score = impact_score
        result.head_or_torso_involved = head_or_torso
        result.no_recovery_observed = no_recovery
        result.message = message
        return result


def draw_serial_medical_overlay(frame: np.ndarray, warning: MedicalWarning, fps: float = 25.0) -> np.ndarray:
    """Draw a compact ASCII-safe medical screening panel on a BGR frame."""
    if not warning.active:
        return frame
    height, width = frame.shape[:2]
    color = (0, 0, 255) if warning.risk_level == "critical" else (0, 165, 255) if warning.risk_level == "high" else (0, 210, 255)
    lines = [
        f"MEDICAL SCREEN: {warning.risk_level.upper()}",
        f"First contact: {warning.first_side} / {warning.first_contact_part} ({warning.first_contact_confidence:.2f})",
        f"Impact score: {warning.impact_score:.0f}  Pose Q: {warning.pose_quality:.2f}",
    ]
    if warning.second_side != "uncertain":
        delta = (warning.second_contact_frame - warning.first_contact_frame) / max(float(fps), 1.0)
        lines.append(f"Second contact: {warning.second_side} / {warning.second_contact_part} (+{delta:.2f}s)")
    lines.append("Screening only - human/medical assessment required")
    panel_h = 28 + 23 * len(lines)
    panel_w = min(width - 20, 620)
    x0, y0 = 10, max(10, height - panel_h - 10)
    overlay = frame.copy()
    cv2.rectangle(overlay, (x0, y0), (x0 + panel_w, y0 + panel_h), (12, 18, 28), -1)
    cv2.addWeighted(overlay, 0.78, frame, 0.22, 0, frame)
    for index, line in enumerate(lines):
        cv2.putText(frame, line, (x0 + 12, y0 + 23 + index * 23), cv2.FONT_HERSHEY_SIMPLEX, 0.52, color if index == 0 else (235, 235, 235), 1, cv2.LINE_AA)
    return frame


def draw_serial_pose(frame: np.ndarray, pose_frame: PoseFrame | None, color=(255, 220, 0)) -> np.ndarray:
    """Draw the latest post-alarm COCO-17 skeleton without affecting inference."""
    if pose_frame is None:
        return frame
    height, width = frame.shape[:2]
    for first, second in (
        (0, 1), (0, 2), (1, 3), (2, 4), (5, 6), (5, 7), (7, 9),
        (6, 8), (8, 10), (5, 11), (6, 12), (11, 12), (11, 13),
        (13, 15), (12, 14), (14, 16),
    ):
        if pose_frame.confidence[first] >= 0.30 and pose_frame.confidence[second] >= 0.30:
            p1 = tuple(np.round(pose_frame.keypoints[first] * [width, height]).astype(int))
            p2 = tuple(np.round(pose_frame.keypoints[second] * [width, height]).astype(int))
            cv2.line(frame, p1, p2, color, 2, cv2.LINE_AA)
    for index, point in enumerate(pose_frame.keypoints):
        if pose_frame.confidence[index] >= 0.30:
            center = tuple(np.round(point * [width, height]).astype(int))
            cv2.circle(frame, center, 4, color, -1, cv2.LINE_AA)
    return frame
