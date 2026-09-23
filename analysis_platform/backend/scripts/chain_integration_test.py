"""真实链路集成验证：用真实 v2.2 推理 + 真实事件桥触发一次完整跌倒事件。

喂入 45d6b8 跌倒视频帧（v2.2 离线验证 p_final=0.99 触发），走真实链路：
  真实推理 -> _on_fall_alert -> process_fall_event -> 抓拍/医疗报告/语音问询
  -> 问询超时(Ⅰ级10s) -> _dispatch_emergency -> 企业微信 + 小程序订阅消息推送
"""
import os
import sys
import time
import json
from pathlib import Path
import cv2

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

from app.inference import world_av_pipeline as wp
from app.services import fall_event_archive as archive

VIDEO = os.getenv("CHAIN_TEST_VIDEO", str(Path(__file__).resolve().parents[3] / "test_video" / "45d6b805fc53452704bf44395b79b847.mp4"))


def main():
    # yolo_gate=False: v2.2 自带内部 YOLO-Pose，避免 Stage-1 人检漏检导致跳过推理
    print("[chain] 设置 mode=worldpose yolo_gate=False", flush=True)
    wp.set_mode(mode="worldpose", yolo_gate=False)
    wp.reset_pipeline()

    cap = cv2.VideoCapture(VIDEO)
    if not cap.isOpened():
        print("[chain] 无法打开视频", flush=True)
        return
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    cap.release()
    print(f"[chain] 读取 {len(frames)} 帧（原始帧率喂入，~0.09s/帧模拟 10fps）", flush=True)

    alert_seen = False
    for i, frame in enumerate(frames):
        r = wp.process_frame(frame)
        if i % 3 == 0 or r.get("alert_active"):
            print(f"[chain] frame {i}: persons={len(r.get('persons', []))} "
                  f"fall_prob={r.get('fall_prob')} alert={r.get('alert_active')}", flush=True)
        if r.get("alert_active"):
            alert_seen = True
            print(f"[chain] 告警 @frame {i} fall_prob={r.get('fall_prob')}", flush=True)
            break
        time.sleep(0.09)
    if not alert_seen:
        print("[chain] 未触发告警（检查模型/阈值）", flush=True)
        return

    # 找新创建的真实事件，并强制触发紧急派发（跳过倒计时，直达通知推送）
    import glob
    archive_path = str(Path(BACKEND) / "data" / "fall_events_archive.json")

    def latest_event():
        recs = json.load(open(archive_path))
        if isinstance(recs, dict):
            items = recs.get("events", list(recs.values()))
        else:
            items = recs
        if isinstance(items, dict):
            items = list(items.values())
        return items[-1] if items else {}

    ev = latest_event()
    eid = ev.get("event_id", "?")
    print(f"[chain] 最新事件: {eid} status={ev.get('status')} confidence={ev.get('detection_confidence')}", flush=True)

    # 强制紧急派发（fall_inquiry 内部线程; 触发 _dispatch_emergency → 企业微信+小程序）
    from app.services import fall_inquiry
    try:
        fall_inquiry.force_timeout_inquiry(eid)
        print(f"[chain] 已强制触发紧急派发: {eid}", flush=True)
    except Exception as exc:
        print(f"[chain] force_timeout 失败: {exc}", flush=True)

    print("[chain] 等待通知推送完成 (最多 45s)...", flush=True)
    deadline = time.time() + 45
    while time.time() < deadline:
        ev = latest_event()
        status = ev.get("status", "?")
        nstatus = ev.get("notification_status", {})
        print(f"[chain] 事件 {eid} status={status} notif={json.dumps(nstatus, ensure_ascii=False)[:240]}", flush=True)
        if status in ("archived", "notified", "voice_cancelled", "false_alarm"):
            print(f"[chain] 事件终态: {status}", flush=True)
            break
        time.sleep(3)
    print("[chain] 集成验证完成", flush=True)


if __name__ == "__main__":
    main()
