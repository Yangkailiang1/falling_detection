"""Record RTSP stream to mp4 for offline testing (20s)."""
import cv2
import sys
import time

RTSP_URL = "rtsp://admin:LHGOWQ@CAMERA_IP:554/h264/ch1/sub/av_stream"
OUT = "/tmp/fall_test.mp4"
DURATION = 20

cap = cv2.VideoCapture(RTSP_URL)
if not cap.isOpened():
    print("RTSP open failed")
    sys.exit(1)

fps = 10
w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
writer = cv2.VideoWriter(OUT, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
t0 = time.time()
frames = 0
while time.time() - t0 < DURATION:
    ret, frame = cap.read()
    if not ret:
        continue
    writer.write(frame)
    frames += 1
writer.release()
cap.release()
print(f"recorded {frames} frames -> {OUT}")
