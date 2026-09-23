"""直接用真实 process_frame 喂原始视频末帧，验证告警触发。"""
import os, sys, time, cv2
BACKEND = "<RELEASE_ROOT>/analysis_platform/backend"
sys.path.insert(0, BACKEND); os.chdir(BACKEND)
from app.inference import world_av_pipeline as wp

wp.set_mode(mode="worldpose", yolo_gate=False)
wp.reset_pipeline()

cap = cv2.VideoCapture("<RELEASE_ROOT>/test_video/45d6b805fc53452704bf44395b79b847.mp4")
frames = []
while True:
    ok, f = cap.read()
    if not ok: break
    frames.append(f)
cap.release()
print(f"视频 {len(frames)} 帧", flush=True)

# 喂末 32 帧原始帧（密集），看每帧 fall_prob/alert
for i, frame in enumerate(frames[-32:]):
    r = wp.process_frame(frame)
    print(f"  raw#{len(frames)-32+i}: persons={len(r.get('persons',[]))} fall_prob={r.get('fall_prob'):.3f} alert={r.get('alert_active')}", flush=True)
    if r.get("alert_active"):
        print("🚨 告警触发！", flush=True)
        break
    time.sleep(0.1)
print("=== 完成 ===", flush=True)
