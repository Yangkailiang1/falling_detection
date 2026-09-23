"""验证假设：32 不重复帧（匹配训练） vs 20帧重采样32（重复帧）对 p_final 的影响。"""
import os, sys, time, cv2
BACKEND = "<RELEASE_ROOT>/analysis_platform/backend"
sys.path.insert(0, BACKEND); os.chdir(BACKEND)
from app.inference.worldpose_v2.world_pose_v2_runtime import WorldPoseV2Runtime

rt = WorldPoseV2Runtime()   # 默认 capture_fps=10 → required_history=20
print(f"threshold={rt.threshold:.4f}", flush=True)

cap = cv2.VideoCapture("<RELEASE_ROOT>/test_video/45d6b805fc53452704bf44395b79b847.mp4")
frames = []
while True:
    ok, f = cap.read()
    if not ok: break
    frames.append(f)
cap.release()
print(f"视频 {len(frames)} 帧", flush=True)

# 取末尾 32 帧作为跌倒窗口（跌倒在最末）
win32 = frames[-32:]

# Case A: 32 不重复帧（CLI/训练匹配）
rt.required_history = 32
rA = rt.predict(win32, waveform=None, sample_rate=None)

# Case B: 20 帧重采样 32（当前实时 10fps 路径）
rt.required_history = 20
rB = rt.predict(win32, waveform=None, sample_rate=None)

print(f"[A: 32不重复帧] p_final={rA['final_probability']:.3f} p_visual={rA['visual_probability']:.3f} p_pose={rA['pose_probability']:.3f}", flush=True)
print(f"[B: 20帧重采样32] p_final={rB['final_probability']:.3f} p_visual={rB['visual_probability']:.3f} p_pose={rB['pose_probability']:.3f}", flush=True)
print("=== 若 A >> B，则重复帧重采样是根因 → 实时需按 16fps 采样 ===", flush=True)
