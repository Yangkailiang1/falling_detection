"""Bounded latest-frame capture for delay-safe live inference.

Camera/RTSP decoding runs independently from inference.  Only the newest frame
is retained; frames that arrive while the model is busy are intentionally
dropped.  This bounds end-to-end latency instead of allowing a hidden decoder
or Python queue to grow without limit.
"""
from __future__ import annotations

import threading
import time

import cv2


def _source_value(source: str):
    return int(source) if str(source).strip().isdigit() else source


class LatestFrameCapture:
    """A ``cv2.VideoCapture``-like reader with a one-frame queue."""

    def __init__(self, source: str):
        self.capture = cv2.VideoCapture(_source_value(source))
        if not self.capture.isOpened():
            raise RuntimeError(f"Could not open live source: {source}")
        # Backends that support this honour it; the Python-side slot below is
        # still the correctness guarantee when they do not.
        self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._condition = threading.Condition()
        self._latest = None
        self._sequence = 0
        self._consumed = 0
        self._closed = False
        self.last_timestamp = 0.0
        self.dropped_frames = 0
        self._thread = threading.Thread(target=self._read_loop, name="fall-live-capture", daemon=True)
        self._thread.start()

    def _read_loop(self):
        while True:
            with self._condition:
                if self._closed:
                    return
            ok, frame = self.capture.read()
            if not ok:
                time.sleep(0.01)
                continue
            with self._condition:
                self._sequence += 1
                self._latest = (self._sequence, time.perf_counter(), frame)
                self._condition.notify_all()

    def read(self, timeout_seconds: float = 2.0):
        """Return the newest not-yet-consumed frame, dropping stale frames."""
        deadline = time.perf_counter() + timeout_seconds
        with self._condition:
            while not self._closed and (self._latest is None or self._latest[0] <= self._consumed):
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return False, None
                self._condition.wait(timeout=remaining)
            if self._closed or self._latest is None:
                return False, None
            sequence, timestamp, frame = self._latest
            self.dropped_frames += max(0, sequence - self._consumed - 1)
            self._consumed = sequence
            self.last_timestamp = timestamp
            return True, frame

    def get(self, property_id):
        return self.capture.get(property_id)

    def release(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._thread.join(timeout=1.0)
        self.capture.release()
