"""
Windows 侧独立小程序 API 后端 (联调用)

背景: WSL 网络隔离(mirrored失效), 小程序模拟器(Windows进程)无法访问 WSL 里的后端。
此文件在 Windows 侧运行, 提供小程序需要的 API, 数据与 WSL 共享(D盘)。

运行 (Windows):
    python standalone_mini_api.py

端口: 5002 (微信小程序专用 Windows API)
依赖: 见 requirements-mini-api.txt

数据共享 (D盘, WSL 与 Windows 同文件):
    上级 analysis_platform/.env       — 配置（按本文件位置自动查找）
    backend/data/wx_subscriptions.json    — 订阅关系
    backend/data/fall_events_archive.json — 事件归档
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

# ---- 路径 (Windows) ----
# 默认按本文件位置推导正式平台根目录，支持解压到任意目录。
# 如需显式指定，可设置 ANALYSIS_PLATFORM_ROOT，但不再依赖个人机器路径。
BASE = Path(os.getenv("ANALYSIS_PLATFORM_ROOT", Path(__file__).resolve().parents[1])).resolve()
load_dotenv(BASE / ".env")
load_dotenv(BASE / "backend" / ".env")

DATA_DIR = BASE / "backend" / "data"
SUB_FILE = DATA_DIR / "wx_subscriptions.json"
ARCHIVE_FILE = DATA_DIR / "fall_events_archive.json"

EZS_API = "https://open.ys7.com"
DEVICE_SERIAL = os.getenv("DEVICE_SERIAL", "CHANGE_ME_DEVICE_SERIAL")

app = Flask(__name__)


def ok(data):
    return jsonify({"code": 200, "success": True, "data": data, "message": "操作成功"})


def err(msg, code=400):
    return jsonify({"code": code, "success": False, "data": None, "message": msg}), code


# ---------------------------------------------------------------------------
# 萤石 accessToken (缓存)
# ---------------------------------------------------------------------------
_token = {"token": "", "expire": 0}


def get_ezviz_token():
    import time
    if _token["token"] and time.time() < _token["expire"] - 300:
        return _token["token"]
    resp = requests.post(f"{EZS_API}/api/lapp/token/get", data={
        "appKey": os.getenv("EZS_APP_KEY", ""),
        "appSecret": os.getenv("EZS_APP_SECRET", ""),
    }, timeout=10).json()
    if resp.get("code") == "200" and resp.get("data"):
        _token["token"] = resp["data"]["accessToken"]
        _token["expire"] = time.time() + 6 * 3600
        return _token["token"]
    raise Exception(f"萤石 token 获取失败: {resp.get('msg')}")


def ezviz_request(method, path, data=None):
    token = get_ezviz_token()
    resp = requests.request(method, f"{EZS_API}{path}", data=data or {}, params={
        "accessToken": token}, timeout=10)
    body = resp.json()
    if body.get("code") != "200":
        raise Exception(f"萤石错误: {body.get('msg')}")
    return body.get("data")


# ---------------------------------------------------------------------------
# 订阅关系
# ---------------------------------------------------------------------------
def load_subs():
    if SUB_FILE.exists():
        try:
            return json.loads(SUB_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_subs(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SUB_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_archive():
    """读取共享归档 JSON, 统一 dict/list 结构。"""
    if not ARCHIVE_FILE.exists():
        return []
    try:
        raw = json.loads(ARCHIVE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(raw, dict):
        return raw
    return raw


def _atomic_write_archive(raw):
    """原子写归档 JSON（tmp + os.replace）[2026-08-13]。

    原 write_text 先截断再写, 与 WSL 后端 _save_to_disk 的 os.replace 并发时
    可能被读到空/半截文件 → 加载失败 → 内存空 → 下次落盘覆盖清空归档。改原子写消除。
    """
    tmp = Path(str(ARCHIVE_FILE) + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(ARCHIVE_FILE))


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.route("/api/health", methods=["GET"])
def health():
    return ok({"service": "standalone-mini-api", "ezviz_api_configured": bool(os.getenv("EZS_APP_KEY"))})


@app.route("/api/mini/login", methods=["POST"])
def mini_login():
    body = request.get_json(silent=True) or {}
    code = body.get("code", "")
    appid = os.getenv("WX_MINI_APPID", "")
    secret = os.getenv("WX_MINI_SECRET", "")
    resp = requests.get("https://api.weixin.qq.com/sns/jscode2session", params={
        "appid": appid, "secret": secret, "js_code": code,
        "grant_type": "authorization_code"}, timeout=10).json()
    if "openid" in resp:
        return ok({"openid": resp["openid"], "session_key": resp.get("session_key", "")})
    return err(f"登录失败: {resp}", 400)


def _normalize_devices(data):
    """萤石设备列表可能是 list 或 {'deviceList': [...]}。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("deviceList", data.get("list", []))
    return []


@app.route("/api/mini/devices", methods=["GET"])
def mini_devices():
    try:
        data = ezviz_request("POST", "/api/lapp/device/list", {"pageStart": 0, "pageSize": 100})
        devices = _normalize_devices(data)
        result = [{
            "device_serial": d.get("deviceSerial", ""),
            "device_name": d.get("deviceName", ""),
            "status": "online" if d.get("status") == 1 else "offline",
            "model": d.get("model", ""),
        } for d in devices]
        return ok({"devices": result})
    except Exception as e:
        return err(f"设备列表获取失败: {str(e)[:100]}", 500)


@app.route("/api/mini/device/bind", methods=["POST"])
def mini_bind():
    body = request.get_json(silent=True) or {}
    serial = body.get("device_serial", "").strip()
    openid = body.get("openid", "")
    if not serial:
        return err("缺少设备序列号", 400)
    try:
        data = ezviz_request("POST", "/api/lapp/device/list", {"pageStart": 0, "pageSize": 100})
        devices = _normalize_devices(data)
        if not any(d.get("deviceSerial") == serial for d in devices):
            return err("设备不存在或不在当前账号下", 400)
    except Exception as e:
        return err(f"设备校验失败: {str(e)[:100]}", 500)
    if openid:
        subs = load_subs()
        subs[openid] = {"device_serial": serial, "subscribed_at": None}
        save_subs(subs)
    return ok({"bound": True, "device_serial": serial})


@app.route("/api/mini/subscribe", methods=["POST"])
def mini_subscribe():
    body = request.get_json(silent=True) or {}
    openid = body.get("openid", "")
    serial = body.get("device_serial", DEVICE_SERIAL)
    if not openid:
        return err("缺少 openid", 400)
    import datetime
    subs = load_subs()
    subs[openid] = {"device_serial": serial, "nickname": body.get("nickname", ""),
                    "subscribed_at": datetime.datetime.now().isoformat()}
    save_subs(subs)
    return ok({"subscribed": True})


@app.route("/api/mini/subscribers", methods=["GET"])
def mini_subscribers():
    return ok({"subscribers": list(load_subs().values())})


# ---------------------------------------------------------------------------
# 事件 (读共享归档 JSON)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 摄像头取流 (ezplayer 插件)
# ---------------------------------------------------------------------------

@app.route("/api/mini/stream-url", methods=["GET"])
def mini_stream_url():
    """返回萤石播放 URL (实时预览)。"""
    serial = request.args.get("serial", DEVICE_SERIAL)
    channel = request.args.get("channel", "1")
    vtype = request.args.get("type", "live")  # live/local/cloud/cloudRecord
    url = f"rtmp://open.ys7.com/{serial}/{channel}/{vtype}"
    return ok({"url": url, "device_serial": serial, "channel": channel, "type": vtype})


@app.route("/api/mini/stream-token", methods=["GET"])
def mini_stream_token():
    """返回萤石 accessToken (播放/对讲鉴权)。"""
    try:
        token = get_ezviz_token()
        return ok({"accessToken": token, "expire_in": 6 * 3600})
    except Exception as e:
        return err(f"token获取失败: {str(e)[:100]}", 500)


@app.route("/api/mini/notify-test", methods=["POST"])
def mini_notify_test():
    """手动触发订阅消息推送 (验证用) — 推给所有已订阅家属。"""
    body = request.get_json(silent=True) or {}
    openid = body.get("openid", "")  # 指定 openid, 空则推全部
    event_id = body.get("event_id", "")  # 可选: 事件详情页跳转参数 (对齐真实推送 page)
    appid = os.getenv("WX_MINI_APPID", "")
    secret = os.getenv("WX_MINI_SECRET", "")
    template_id = os.getenv("WX_TEMPLATE_ID", "")
    # 与 subscribe_message._get_mini_state 保持一致的版本配置 (developer/trial/formal)
    mini_state = os.getenv("WX_MINI_STATE", "trial").strip().lower()
    if mini_state not in ("developer", "trial", "formal"):
        mini_state = "trial"
    if not (appid and secret and template_id):
        return err("小程序配置缺失 (WX_MINI_APPID/SECRET/TEMPLATE_ID)", 500)
    # access_token
    import time as _t
    token_resp = requests.get("https://api.weixin.qq.com/cgi-bin/token", params={
        "grant_type": "client_credential", "appid": appid, "secret": secret}, timeout=10).json()
    if "access_token" not in token_resp:
        return err(f"access_token失败: {token_resp}", 500)
    at = token_resp["access_token"]
    # 订阅者
    subs = load_subs()
    targets = [openid] if openid else list(subs.keys())
    sent = 0
    for oid in targets:
        page_path = f"pages/events/detail?id={event_id}" if event_id else "pages/events/events"
        payload = {
            "touser": oid,
            "template_id": template_id,
            "page": page_path,
            "miniprogram_state": mini_state,
            "lang": "zh_CN",
            "data": {
                "thing1": {"value": "跌倒告警"},
                "thing2": {"value": "家中"},
                "thing3": {"value": "长辈守护摄像头"},
                "time4": {"value": _t.strftime("%Y-%m-%d %H:%M")},
                "thing5": {"value": "风险等级：Ⅱ级中危"},
            },
        }
        r = requests.post(f"https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token={at}",
                          json=payload, timeout=10).json()
        if r.get("errcode") == 0:
            sent += 1
        else:
            print(f"[notify-test] {oid}: {r}")
    return ok({"sent": sent, "total": len(targets)})


# ---------------------------------------------------------------------------
# 事件 (读共享归档 JSON)
# ---------------------------------------------------------------------------

CLIPS_DIR = DATA_DIR / "clips"
CAPTURES_DIR = DATA_DIR / "captures"  # 本地持久化的抓拍图 backend/data/captures/<event_id>.jpg


def _capture_pic_url(event_id: str, rec: dict) -> str:
    """事件图片 URL: 本地已持久化 → 相对路径 /api/mini/capture/<id>; 否则返回云端签名 URL。

    萤石签名 URL 24h 过期(403) → 本地文件永久可用, 小程序端需前缀 apiBase 拼接。"""
    if event_id:
        local = CAPTURES_DIR / f"{event_id}.jpg"
        if local.is_file():
            return f"/api/mini/capture/{event_id}"
    rec_path = rec.get("capture_pic_path") or ""
    if rec_path:
        import ntpath
        name = ntpath.basename(str(rec_path).replace("\\", "/"))
        if name and (CAPTURES_DIR / name).is_file():
            return f"/api/mini/capture/{name.removesuffix('.jpg')}"
    return rec.get("capture_pic_url") or ""


def _clip_video_url(clip_path: str, event_id: str) -> str:
    """根据事件视频路径/事件ID, 生成小程序可访问的视频 URL。

    优先取归档里记录的 video_clip 路径; 否则按事件ID前缀匹配 clips 目录里的
    fall_{event8}_*.mp4 (兼容历史事件)。
    """
    if event_id:
        # 按事件ID前缀匹配
        prefix = f"fall_{event_id[:8]}_"
        try:
            if CLIPS_DIR.exists():
                for f in CLIPS_DIR.iterdir():
                    if f.name.startswith(prefix) and f.suffix == ".mp4":
                        return f"/api/mini/clip/{event_id}"
        except Exception:
            pass
    if clip_path:
        # 从路径提取文件名
        import ntpath
        name = ntpath.basename(str(clip_path).replace("\\", "/"))
        if name:
            return f"/api/mini/clip/{name}"
    return ""


def _archive_record_to_detail(rec: dict) -> dict:
    """
    将归档的扁平记录转换为嵌套结构 (对齐 WSL 后端 fall_events._record_to_dict)。

    归档字段是扁平的 (touch_ground_part / risk_level / location 在顶层)，
    而前端/小程序按嵌套结构访问 (skeleton_analysis.touch_ground_part 等)。
    """
    return {
        "event_id": rec.get("event_id"),
        "status": rec.get("status"),
        "created_at": rec.get("created_at"),
        # 检测参数
        "detection": {
            "confidence": rec.get("detection_confidence"),
            "latency_ms": rec.get("detection_latency_ms"),
            "video_window_frames": rec.get("video_window_frames"),
            "video_window_duration_s": rec.get("video_window_duration_s"),
        },
        # 骨骼分析
        "skeleton_analysis": {
            "touch_ground_part": rec.get("touch_ground_part"),
            "fall_direction": rec.get("fall_direction"),
            "impact_velocity": rec.get("impact_velocity"),
            "body_tilt_angle": rec.get("body_tilt_angle"),
            "center_of_mass_velocity": rec.get("center_of_mass_velocity"),
        },
        # 风险等级
        "risk": {
            "level": rec.get("risk_level"),
            "level_name": rec.get("risk_level_name"),
            "likely_injury_types": rec.get("likely_injury_types"),
        },
        # 医疗报告
        "medical_report": {
            "full_text": rec.get("medical_report"),
            "recommendation": rec.get("report_recommendation"),
        },
        # 响应策略
        "response": {
            "strategy": rec.get("response_strategy"),
            "countdown_seconds": rec.get("countdown_seconds"),
            "voice_confirm_status": rec.get("voice_confirm_status"),
        },
        # 通知状态
        "notification": rec.get("notification_status"),
        # 事件上下文
        "context": {
            "location": rec.get("location"),
            "device_serial": rec.get("device_serial"),
            "scenario_key": rec.get("scenario_key"),
            "scenario_name": rec.get("scenario_name"),
            "description": rec.get("description"),
        },
        # 时间线
        "timeline": rec.get("timeline") or [],
        # 反馈
        "feedback": rec.get("feedback"),
        # 现场图片 (V7.2) — 本地持久化优先(相对路径), 避免萤石签名 URL 过期 [V9.4]
        "capture_pic_url": _capture_pic_url(rec.get("event_id") or "", rec),
        "capture_time": rec.get("capture_time") or "",
        # 现场视频 (V9.3) — 问询录制片段, 供小程序回放
        "video_url": _clip_video_url(rec.get("video_clip") or "", rec.get("event_id") or ""),
    }


@app.route("/api/fall-events", methods=["GET"])
def fall_events():
    if not ARCHIVE_FILE.exists():
        return ok({"events": [], "total": 0})
    raw = json.loads(ARCHIVE_FILE.read_text(encoding="utf-8"))
    # 归档可能是 dict(event_id→record) 或 list
    if isinstance(raw, dict):
        raw = list(raw.values())
    # 按时间倒序 (最新在前), 与 WSL 后端行为一致
    raw.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    # 分页 (limit/offset), 小程序列表/向导页用
    limit = request.args.get("limit", type=int, default=100)
    offset = request.args.get("offset", type=int, default=0)
    page_items = raw[offset:offset + limit] if limit else raw
    events = [_archive_record_to_detail(e) for e in page_items]
    return ok({"events": events, "total": len(raw)})


@app.route("/api/fall-events/<event_id>", methods=["GET"])
def fall_event_detail(event_id):
    if not ARCHIVE_FILE.exists():
        return err("事件不存在", 404)
    raw = json.loads(ARCHIVE_FILE.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        if event_id in raw:
            return ok(_archive_record_to_detail(raw[event_id]))
    else:
        for e in raw:
            if e.get("event_id") == event_id:
                return ok(_archive_record_to_detail(e))
    return err("事件不存在", 404)


@app.route("/api/mini/clip/<name>", methods=["GET"])
def mini_clip(name):
    """返回事件视频片段 (mp4)。name 为 event_id 或文件名。"""
    from flask import send_file
    if not CLIPS_DIR.exists():
        return err("视频目录不存在", 404)
    # 若传入 event_id, 按前缀匹配
    if not name.endswith(".mp4"):
        prefix = f"fall_{name[:8]}_"
        for f in CLIPS_DIR.iterdir():
            if f.name.startswith(prefix) and f.suffix == ".mp4":
                return send_file(str(f), mimetype="video/mp4")
        return err("事件无视频片段", 404)
    # 直接文件名
    target = CLIPS_DIR / name
    if target.exists() and target.suffix == ".mp4":
        return send_file(str(target), mimetype="video/mp4")
    return err("视频不存在", 404)


@app.route("/api/mini/capture/<event_id>", methods=["GET"])
def mini_capture(event_id):
    """返回事件现场抓拍图 (本地持久化 backend/data/captures/<id>.jpg)。"""
    from flask import send_file
    target = CAPTURES_DIR / f"{event_id}.jpg"
    if target.exists():
        return send_file(str(target), mimetype="image/jpeg")
    # 兼容带扩展名/路径
    for f in CAPTURES_DIR.iterdir() if CAPTURES_DIR.exists() else []:
        if f.name.startswith(event_id) and f.suffix.lower() in (".jpg", ".jpeg", ".png"):
            return send_file(str(f), mimetype="image/jpeg")
    return err("图片不存在或已过期", 404)


@app.route("/api/mini/capture/refresh", methods=["POST"])
def mini_capture_refresh():
    """重新抓拍现场图片并持久化本地 (旧事件云端签名 URL 过期后恢复用)。"""
    body = request.get_json(silent=True) or {}
    event_id = (body.get("event_id") or "").strip()
    if not event_id:
        return err("缺少 event_id", 400)
    try:
        data = ezviz_request("POST", "/api/lapp/device/capture", {
            "deviceSerial": DEVICE_SERIAL, "channelNo": 1})
        pic_url = (data or {}).get("picUrl", "")
        if not pic_url:
            return err("抓拍失败(设备可能离线)", 502)
        # 下载并持久化本地
        r = requests.get(pic_url, timeout=8)
        if r.status_code != 200:
            return err(f"图片下载失败 HTTP {r.status_code}", 502)
        CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        (CAPTURES_DIR / f"{event_id}.jpg").write_bytes(r.content)
        # 更新归档 (capture_pic_url/path/capture_time)
        import datetime
        raw = _load_archive()
        if raw:
            if isinstance(raw, dict):
                if event_id in raw:
                    raw[event_id]["capture_pic_url"] = pic_url
                    raw[event_id]["capture_pic_path"] = str(CAPTURES_DIR / f"{event_id}.jpg")
                    raw[event_id]["capture_time"] = datetime.datetime.now().astimezone().isoformat()
            else:
                for e in raw:
                    if e.get("event_id") == event_id:
                        e["capture_pic_url"] = pic_url
                        e["capture_pic_path"] = str(CAPTURES_DIR / f"{event_id}.jpg")
                        e["capture_time"] = datetime.datetime.now().astimezone().isoformat()
            _atomic_write_archive(raw)
        return ok({"capture_pic_url": f"/api/mini/capture/{event_id}", "local_persisted": True})
    except Exception as e:
        return err(f"重新抓拍失败: {str(e)[:100]}", 502)


@app.route("/api/fall-events/<event_id>/feedback", methods=["POST"])
def fall_feedback(event_id):
    """
    家属在事件详情页的确认/误报反馈（写入共享归档 JSON）。

    与 WSL 后端 submit_feedback 行为对齐: 误报 → status=false_alarm; 确认 → 保持终态。
    **同步机制**: 本进程在 Windows 侧, 因 WSL 网络隔离无法直连算法平台/WSL 后端,
    只负责把反馈 + pushed_to_algo=False 写进共享归档; WSL 后端有定时重载线程
    (fall_event_archive._start_sync_poller) 会兜底重推到算法平台 (upsert 覆盖标签)。
    """
    body = request.get_json(silent=True) or {}
    is_false_alarm = bool(body.get("is_false_alarm", False))
    comment = body.get("comment", "") or ""
    if not ARCHIVE_FILE.exists():
        return err("事件不存在", 404)
    try:
        raw = json.loads(ARCHIVE_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            if event_id not in raw:
                return err("事件不存在", 404)
            rec = raw[event_id]
        else:
            rec = next((e for e in raw if e.get("event_id") == event_id), None)
            if not rec:
                return err("事件不存在", 404)
            # 保持 dict 存储形态；若是 list 转换回 dict 便于按 id 写回
            raw = {e["event_id"]: e for e in raw}

        rec["feedback"] = {
            "is_false_alarm": is_false_alarm,
            "comment": comment,
            "reported_at": datetime.now(timezone.utc).isoformat(),
        }
        if is_false_alarm:
            rec["status"] = "false_alarm"
        elif rec.get("status") == "false_alarm":
            # 家属确认是真实跌倒 → 从误报状态还原为已归档 [2026-08-12]
            rec["status"] = "archived"
        # 手动反馈改变 ground truth → 重置推送标记, 由 WSL 后端重载重推 [2026-08-12]
        rec["pushed_to_algo"] = False
        _atomic_write_archive(raw)
        return ok({"event_id": event_id, "is_false_alarm": is_false_alarm,
                   "status": rec.get("status")})
    except Exception as e:
        return err(f"反馈写入失败: {str(e)[:100]}", 500)


# ---------------------------------------------------------------------------
# 新用户接入向导 (演示)
# ---------------------------------------------------------------------------

def _extract_webhook_key(val):
    """从 webhook key 或完整 URL 中提取 key。"""
    val = (val or "").strip()
    if "key=" in val:
        return val.split("key=", 1)[1].split("&")[0].strip()
    return val


@app.route("/api/mini/setup/prefill", methods=["GET"])
def mini_setup_prefill():
    """返回接入向导演示预填值 (真实值来自 .env)。"""
    return ok({
        "device_serial": os.getenv("DEVICE_SERIAL", "CHANGE_ME_DEVICE_SERIAL"),
        "validate_code": os.getenv("EZS_ACTIVATION_CODE_1", ""),
        "wecom_webhook_key": _extract_webhook_key(os.getenv("WECOM_WEBHOOK_KEY", "")),
        "pushplus_token": os.getenv("PUSHPLUS_TOKEN", ""),
        "ezviz_api_configured": bool(os.getenv("EZS_APP_KEY")),
    })


@app.route("/api/mini/device/add", methods=["POST"])
def mini_device_add():
    """添加摄像头到萤石账号并确认能取流。

    参数: device_serial + validate_code (设备验证码)
    流程: 1) 若设备不在账号下则 device/add 添加  2) 获取直播地址确认能取流
    """
    body = request.get_json(silent=True) or {}
    serial = body.get("device_serial", "").strip()
    vcode = body.get("validate_code", "").strip()
    if not serial:
        return err("缺少设备序列号", 400)
    if not vcode:
        return err("缺少设备验证码（机身标签 / 萤石App设备信息）", 400)
    try:
        # 1. 设备已在账号下则跳过添加
        data = ezviz_request("POST", "/api/lapp/device/list", {"pageStart": 0, "pageSize": 100})
        exists = any(d.get("deviceSerial") == serial for d in _normalize_devices(data))
        if not exists:
            token = get_ezviz_token()
            r = requests.post(f"{EZS_API}/api/lapp/device/add",
                              data={"accessToken": token, "deviceSerial": serial, "validateCode": vcode},
                              timeout=15).json()
            if r.get("code") != "200":
                return err(f"设备添加失败: {r.get('msg', r)}", 500)
        # 2. 获取直播地址 → 确认平台能取流
        live = ezviz_request("POST", "/api/lapp/v2/live/address/get",
                             {"deviceSerial": serial, "protocol": int(os.getenv("EZS_LIVE_PROTOCOL", "2")),
                              "quality": 2, "supportH265": 0})
        stream_url = live.get("url", "") if isinstance(live, dict) else ""
        if not stream_url:
            return err("设备已接入，但取流地址获取失败（请确认设备在线）", 500)
        return ok({"device_serial": serial, "added": True, "stream_verified": True})
    except Exception as e:
        return err(f"设备接入失败: {str(e)[:120]}", 500)


@app.route("/api/mini/wecom/test", methods=["POST"])
def mini_wecom_test():
    """向企业微信群机器人发送一条测试消息，验证告警通道联通。"""
    body = request.get_json(silent=True) or {}
    webhook_key = _extract_webhook_key(body.get("webhook_key", "")) or os.getenv("WECOM_WEBHOOK_KEY", "")
    if not webhook_key:
        return err("缺少企业微信 webhook key", 400)
    try:
        resp = requests.post(
            f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={webhook_key}",
            json={"msgtype": "text", "text": {
                "content": "✅ 长辈守护 · 告警通道配置成功！这是一条测试消息，收到即代表已联通。"}},
            timeout=10,
        ).json()
        if resp.get("errcode") == 0:
            return ok({"sent": True})
        return err(f"企业微信返回错误: {resp.get('errmsg', resp)}", 500)
    except Exception as e:
        return err(f"测试消息发送失败: {str(e)[:120]}", 500)


if __name__ == "__main__":
    port = int(os.getenv("MINI_API_PORT", "5002"))
    print(f"Standalone Mini API 启动: http://0.0.0.0:{port}")
    print(f"  .env: {BASE / '.env'}")
    app.run(host="0.0.0.0", port=port, debug=False)
