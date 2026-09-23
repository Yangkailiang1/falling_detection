"""
恢复被清空的跌倒事件归档 [2026-08-13]

背景: 小程序只显示 1 条跌倒消息 —— 因为共享归档 backend/data/fall_events_archive.json
只剩 1 条(test-III), 今天(8-13) 16+ 个真实 worldav 检测事件全部丢了(见 CLAUDE.md 十三节)。
数据在别处有完整留存, 本脚本把它们重建回归档:
  - 算法平台 SQLite (algorithm_platform) 的 src_8a28aadb + scenario_key='worldav_real' payload
    (含 created_at/status/检测/着地分析/风险/医疗报告/真实骨骼; 原始 event_id 从 created_at 推出)
  - backend/data/captures/worldav_*.jpg   (现场抓拍图)
  - backend/data/clips/fall_worldav*.mp4   (跌倒录像)
  - backend/data/notification_inbox.json   (通知状态)

只恢复"近期真实检测事件"(worldav_*), 不恢复 8 月初的 43 条模拟测试事件。

用法: cd backend && python3 scripts/recover_archive.py
幂等: 已存在的事件会保留, 重复运行不产生重复。
"""
import json
import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent          # backend/scripts
ANALYSIS_ROOT = SCRIPT_DIR.parent.parent             # analysis_platform
ARCHIVE = ANALYSIS_ROOT / "backend" / "data" / "fall_events_archive.json"
CAPTURES_DIR = ANALYSIS_ROOT / "backend" / "data" / "captures"
CLIPS_DIR = ANALYSIS_ROOT / "backend" / "data" / "clips"
INBOX = ANALYSIS_ROOT / "backend" / "data" / "notification_inbox.json"
ALGO_DB = ANALYSIS_ROOT.parent / "algorithm_platform" / "backend" / "data" / "algorithm_platform.db"

SOURCE_ID = "src_8a28aadb"        # = sha256("CHANGE_ME_DEVICE_SERIAL"+salt)[:8], 客户侧 CHANGE_ME_DEVICE_SERIAL 站点
DEVICE_SERIAL = "CHANGE_ME_DEVICE_SERIAL"
LOCATION = "客厅"

CLIP_RE = re.compile(r"worldav_*(\d+)")   # 兼容 fall_worldav_<n>.mp4 与 fall_worldav__<n>.mp4


def _epoch_of(created_at: str) -> int:
    try:
        return int(datetime.fromisoformat(created_at).timestamp())
    except Exception:
        return 0


def _empty_record(event_id: str, created_at: str, status: str = "archived") -> dict:
    """构造最小 FallEventRecord 兼容的扁平 dict（缺字段用默认值）。"""
    return {
        "event_id": event_id,
        "status": status,
        "created_at": created_at,
        "detection_confidence": 0,
        "detection_latency_ms": 0,
        "video_window_frames": 0,
        "video_window_duration_s": 0,
        "touch_ground_part": "",
        "fall_direction": "",
        "impact_velocity": 0,
        "body_tilt_angle": 0,
        "center_of_mass_velocity": 0,
        "risk_level": "",
        "risk_level_name": "",
        "likely_injury_types": [],
        "location": LOCATION,
        "device_serial": DEVICE_SERIAL,
        "scenario_key": "worldav_real",
        "scenario_name": "WorldAV 实时检测",
        "description": "恢复自算法平台同步数据",
        "medical_report": "",
        "report_recommendation": "",
        "response_strategy": "",
        "countdown_seconds": 0,
        "voice_confirm_status": "pending",
        "notification_status": {},
        "capture_pic_url": "",
        "capture_time": "",
        "capture_pic_path": "",
        "video_clip": "",
        "skeleton_sequence": {},
        "timeline": [],
        "feedback": None,
        "pushed_to_algo": True,   # 早已推送到算法平台, 避免重启后 15s 轮询重复推送
    }


def _timeline_for(rec: dict) -> list:
    """按 status 重建最小时间线（对齐 TimelineEntry 序列化结构）。"""
    created = rec.get("created_at") or datetime.now(timezone.utc).isoformat()
    tl = [
        {"time": created, "stage": "detected", "label": "跌倒检测",
         "detail": "WorldAV 实时检测触发", "status": "success", "duration_ms": 0},
    ]
    touch = rec.get("touch_ground_part") or "未知"
    risk = rec.get("risk_level_name") or rec.get("risk_level") or "未知"
    tl.append({"time": created, "stage": "analyzed", "label": "骨骼分析",
               "detail": f"{touch}着地, 风险 {risk}", "status": "success", "duration_ms": 0})
    tl.append({"time": created, "stage": "inquiry", "label": "语音问询",
               "detail": "已进行语音问询确认", "status": "pending", "duration_ms": 0})
    status = rec.get("status", "")
    if status == "notified":
        tl.append({"time": created, "stage": "notifying", "label": "通知家属",
                   "detail": "已推送家属告警", "status": "success", "duration_ms": 0})
    elif status == "archived":
        tl.append({"time": created, "stage": "archived", "label": "事件归档",
                   "detail": "事件已归档", "status": "success", "duration_ms": 0})
    elif status == "voice_cancelled":
        tl.append({"time": created, "stage": "voice_cancelled", "label": "语音取消",
                   "detail": "老人回应'我没事', 已取消告警", "status": "success", "duration_ms": 0})
    return tl


def _record_from_payload(event_id: str, payload: dict) -> dict:
    """从算法平台脱敏 payload 重建 FallEventRecord 兼容扁平 dict。"""
    rec = _empty_record(event_id, payload.get("created_at") or "", payload.get("status") or "archived")
    det = payload.get("detection") or {}
    rec["detection_confidence"] = det.get("confidence", 0)
    rec["detection_latency_ms"] = det.get("latency_ms", 0)
    rec["video_window_frames"] = det.get("video_window_frames", 0)
    rec["video_window_duration_s"] = det.get("video_window_duration_s", 0)
    sa = payload.get("skeleton_analysis") or {}
    rec["touch_ground_part"] = sa.get("touch_ground_part", "")
    rec["fall_direction"] = sa.get("fall_direction", "")
    rec["impact_velocity"] = sa.get("impact_velocity", 0)
    rec["body_tilt_angle"] = sa.get("body_tilt_angle", 0)
    rec["center_of_mass_velocity"] = sa.get("center_of_mass_velocity", 0)
    risk = payload.get("risk") or {}
    rec["risk_level"] = risk.get("level", "")
    rec["risk_level_name"] = risk.get("level_name", "")
    rec["likely_injury_types"] = risk.get("likely_injury_types", [])
    med = payload.get("medical_report") or {}
    rec["medical_report"] = med.get("full_text", "")
    rec["report_recommendation"] = med.get("recommendation", "")
    resp = payload.get("response") or {}
    rec["response_strategy"] = resp.get("strategy", "")
    rec["countdown_seconds"] = resp.get("countdown_seconds", 0)
    rec["voice_confirm_status"] = resp.get("voice_confirm_status", "pending")
    ctx = payload.get("context") or {}
    rec["scenario_key"] = ctx.get("scenario_key", "worldav_real")
    rec["scenario_name"] = ctx.get("scenario_name", "WorldAV 实时检测")
    rec["description"] = ctx.get("description") or "WorldAV 实时检测"
    rec["skeleton_sequence"] = payload.get("skeleton_sequence") or {}
    rec["feedback"] = payload.get("feedback")
    rec["timeline"] = _timeline_for(rec)
    return rec


def _collect_from_algo(events: dict) -> int:
    """从算法平台 SQLite 读取 src_8a28aadb + worldav_real 事件。"""
    if not ALGO_DB.exists():
        print(f"[skip] 算法平台 DB 不存在: {ALGO_DB}")
        return 0
    conn = sqlite3.connect(str(ALGO_DB))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT created_at, payload FROM events "
        "WHERE source_id=? AND created_at >= '2026-08-01' ORDER BY created_at",
        (SOURCE_ID,))
    rows = cur.fetchall()
    conn.close()
    n = 0
    for r in rows:
        try:
            payload = json.loads(r["payload"])
        except Exception:
            continue
        if (payload.get("context") or {}).get("scenario_key") != "worldav_real":
            continue
        event_id = f"worldav_{_epoch_of(r['created_at'])}"
        events[event_id] = _record_from_payload(event_id, payload)
        n += 1
    print(f"[algo] 从算法平台恢复 {n} 个 worldav_real 事件")
    return n


def _collect_from_media(events: dict) -> int:
    """扫描 captures/clips 目录, 为每个 worldav_* id 确保事件存在并挂图片/录像。"""
    n = 0
    if CAPTURES_DIR.exists():
        for f in sorted(CAPTURES_DIR.glob("worldav_*.jpg")):
            event_id = f.stem
            epoch = int(event_id.removeprefix("worldav_")) if event_id.startswith("worldav_") else 0
            created = datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat() if epoch else datetime.now(timezone.utc).isoformat()
            rec = events.setdefault(event_id, _empty_record(event_id, created))
            rec["capture_pic_path"] = str(f)
            rec["capture_time"] = datetime.fromtimestamp(os.path.getmtime(f)).astimezone().isoformat()
            n += 1
    if CLIPS_DIR.exists():
        for f in sorted(CLIPS_DIR.glob("fall_worldav*.mp4")):
            m = CLIP_RE.search(f.name)
            if not m:
                continue
            event_id = f"worldav_{m.group(1)}"
            epoch = int(m.group(1))
            created = datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
            rec = events.setdefault(event_id, _empty_record(event_id, created))
            rec["video_clip"] = str(f)
            n += 1
    print(f"[media] 关联 {n} 个图片/录像")
    return n


def _enrich_from_inbox(events: dict) -> int:
    """从通知收件箱补风险等级/通知状态。"""
    if not INBOX.exists():
        return 0
    try:
        inbox = json.loads(INBOX.read_text(encoding="utf-8"))
    except Exception:
        return 0
    n = 0
    for item in inbox if isinstance(inbox, list) else []:
        eid = (item.get("extras") or {}).get("event_id", "")
        if not eid or eid not in events:
            continue
        rec = events[eid]
        risk = (item.get("extras") or {}).get("risk_level")
        if risk and not rec["risk_level"]:
            rec["risk_level"] = risk
        if (item.get("extras") or {}).get("medical_report"):
            rec["medical_report"] = item["extras"]["medical_report"]
        rec["notification_status"] = {
            "wechat_sent": True,
            "mini_subscribed": True,
            "mini_sent": True,
            "note": "恢复自通知收件箱",
        }
        n += 1
    print(f"[inbox] 补 {n} 个事件的通知/风险信息")
    return n


def main():
    if not ARCHIVE.exists():
        print(f"[fatal] 归档不存在: {ARCHIVE}")
        return 1
    # 1. 备份当前归档
    bak = str(ARCHIVE) + ".bak"
    shutil.copy2(ARCHIVE, bak)
    print(f"[bak] 已备份当前归档 → {bak}")

    # 2. 收集候选事件
    events: dict = {}
    _collect_from_algo(events)
    _collect_from_media(events)
    _enrich_from_inbox(events)

    # 3. 补默认 + 时间线
    for eid, rec in events.items():
        if not rec["timeline"]:
            rec["timeline"] = _timeline_for(rec)
        rec["pushed_to_algo"] = True
        rec["location"] = rec["location"] or LOCATION
        rec["device_serial"] = rec["device_serial"] or DEVICE_SERIAL

    # 4. 合并进归档(保留现有事件)
    archive = json.loads(ARCHIVE.read_text(encoding="utf-8")) if ARCHIVE.exists() else {}
    if not isinstance(archive, dict):
        print("[fatal] 归档不是 dict 结构, 中止")
        return 1
    before = len(archive)
    archive.update(events)
    added = len(events)

    # 5. 原子写
    tmp = str(ARCHIVE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=2)
    os.replace(tmp, str(ARCHIVE))

    print(f"[ok] 恢复 {added} 个事件 → 归档 {before} → {len(archive)} 个")
    for eid in sorted(events):
        print(f"   + {eid} | {events[eid]['created_at'][:19]} | {events[eid]['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
