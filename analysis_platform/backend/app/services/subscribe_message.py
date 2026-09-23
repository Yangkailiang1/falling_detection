"""
微信小程序订阅消息服务 [产品化 Phase1]

能力:
- access_token 获取与缓存 (小程序 appid/secret)
- 订阅关系管理: openid + template_id → 设备 (家属绑定)
- 订阅消息推送 (长期订阅模板)

配置 (.env):
  WX_MINI_APPID=小程序 appid
  WX_MINI_SECRET=小程序 secret
  WX_TEMPLATE_ID=订阅消息模板 ID (长期订阅)

推送格式 (模板「监控事件告警通知」, 字段为模板后台配置):
  {{thing1.DATA}} — 事件类型 (跌倒告警)
  {{thing2.DATA}} — 设备位置
  {{thing3.DATA}} — 设备名称
  {{time4.DATA}}  — 发生时间 (time 类型)
  {{thing5.DATA}} — 备注
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

WX_API = "https://api.weixin.qq.com"

# access_token 缓存 (进程内)
_token_cache: dict = {"token": "", "expire_at": 0}
_token_lock = threading.Lock()

# 订阅关系存储: JSON 文件 (backend/data/wx_subscriptions.json)
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
SUB_FILE = DATA_DIR / "wx_subscriptions.json"


def _get_appid() -> str:
    return os.getenv("WX_MINI_APPID", "")


def _get_secret() -> str:
    return os.getenv("WX_MINI_SECRET", "")


def _get_template_id() -> str:
    return os.getenv("WX_TEMPLATE_ID", "")


def _get_mini_state() -> str:
    """
    订阅消息跳转的小程序版本 (developer/trial/formal)。
    需与接收方手机上安装的小程序版本一致才能送达:
      developer — 开发版 (开发者工具/预览二维码)
      trial     — 体验版 (上传后体验二维码, 测试家属用)
      formal    — 正式版 (审核发布后)
    """
    state = os.getenv("WX_MINI_STATE", "trial").strip().lower()
    if state not in ("developer", "trial", "formal"):
        logger.warning(f"[WX] 非法 WX_MINI_STATE={state!r}, 回退 trial")
        return "trial"
    return state


# ---------------------------------------------------------------------------
# access_token
# ---------------------------------------------------------------------------

def get_access_token(force: bool = False) -> str:
    """获取小程序 access_token (缓存, 提前 5 分钟过期)。"""
    with _token_lock:
        if not force and _token_cache["token"] and time.time() < _token_cache["expire_at"] - 300:
            return _token_cache["token"]
        appid, secret = _get_appid(), _get_secret()
        if not appid or not secret:
            logger.warning("[WX] 小程序 appid/secret 未配置 (WX_MINI_APPID/WX_MINI_SECRET)")
            return ""
        resp = requests.get(
            f"{WX_API}/cgi-bin/token",
            params={"grant_type": "client_credential", "appid": appid, "secret": secret},
            timeout=10,
        )
        data = resp.json()
        if "access_token" in data:
            _token_cache["token"] = data["access_token"]
            _token_cache["expire_at"] = time.time() + int(data.get("expires_in", 7200))
            return _token_cache["token"]
        logger.warning(f"[WX] access_token 获取失败: {data}")
        return ""


# ---------------------------------------------------------------------------
# 订阅关系存储 (openid → 设备)
# ---------------------------------------------------------------------------

def _load_subscriptions() -> dict:
    if SUB_FILE.exists():
        try:
            return json.loads(SUB_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_subscriptions(data: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SUB_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def subscribe_user(openid: str, device_serial: str, nickname: str = "") -> dict:
    """家属订阅: 记录 openid → 设备绑定关系。"""
    subs = _load_subscriptions()
    subs[openid] = {
        "device_serial": device_serial,
        "nickname": nickname,
        "subscribed_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_subscriptions(subs)
    logger.info(f"[WX] 订阅关系已保存: {openid} → {device_serial}")
    return subs[openid]


def unsubscribe_user(openid: str) -> bool:
    subs = _load_subscriptions()
    if openid in subs:
        del subs[openid]
        _save_subscriptions(subs)
        return True
    return False


def get_subscribers(device_serial: str | None = None) -> list[dict]:
    """查询订阅者; 指定设备时只返回该设备的。"""
    subs = _load_subscriptions()
    result = []
    for openid, info in subs.items():
        if device_serial and info.get("device_serial") != device_serial:
            continue
        result.append({"openid": openid, **info})
    return result


# ---------------------------------------------------------------------------
# 订阅消息推送
# ---------------------------------------------------------------------------

def send_subscribe_message(
    openid: str,
    template_id: str,
    data: dict,
    page: str = "pages/events/detail?id=",
    event_id: str = "",
) -> dict:
    """
    推送订阅消息 (长期订阅模板)。

    Args:
        openid: 接收者 openid
        template_id: 模板 ID (默认读 .env WX_TEMPLATE_ID)
        data: 模板字段 {"thing1": {"value": "..."}, ...}
        page: 点击跳转页面 (默认事件详情)
        event_id: 事件 ID (拼接进 page)
    """
    if not openid:
        return {"success": False, "message": "openid 为空"}
    tpl = template_id or _get_template_id()
    if not tpl:
        return {"success": False, "message": "模板 ID 未配置 (WX_TEMPLATE_ID)"}
    token = get_access_token()
    if not token:
        return {"success": False, "message": "access_token 获取失败"}

    page_path = f"{page}{event_id}" if event_id else page
    payload = {
        "touser": openid,
        "template_id": tpl,
        "page": page_path,
        "miniprogram_state": _get_mini_state(),  # developer/trial/formal, 与接收方版本一致才能送达
        "lang": "zh_CN",
        "data": data,
    }
    try:
        resp = requests.post(
            f"{WX_API}/cgi-bin/message/subscribe/send?access_token={token}",
            json=payload, timeout=10)
        result = resp.json()
        if result.get("errcode") == 0:
            logger.info(f"[WX] ✅ 订阅消息推送成功 → {openid}")
            return {"success": True, "message": "ok"}
        # 43101 = 用户未订阅(需要重新授权); 40003 = openid无效
        logger.warning(f"[WX] 订阅消息推送失败: {result}")
        return {"success": False, "message": str(result)}
    except Exception as e:
        logger.warning(f"[WX] 订阅消息推送异常: {e}")
        return {"success": False, "message": str(e)}


def notify_fall_event(event_id: str, risk_level: str, location: str, description: str, touch_part: str = "") -> dict:
    """
    跌倒告警 → 推送订阅消息给所有订阅家属。

    在告警触发时调用 (fall_inquiry._dispatch_emergency 或 world_av_pipeline._on_fall_alert)。
    返回汇总: {"success": bool(至少一条成功), "sent": 成功条数, "total": 订阅人数,
             "message": 简要说明, "details": [每条的 openid/结果]}
    """
    template_id = _get_template_id()
    if not template_id:
        logger.info("[WX] 未配置订阅消息模板, 跳过小程序推送")
        return {"success": False, "sent": 0, "total": 0, "message": "未配置订阅消息模板", "details": []}
    risk_names = {"I": "Ⅰ级高危", "II": "Ⅱ级中危", "III": "Ⅲ级低危"}
    # 家属收到的是北京时间 (UTC+8)，不能直接用 UTC，否则比手机时间慢 8 小时
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    # 模板「监控事件告警通知」字段定义 (实测, 见 wxaapi/newtmpl/gettemplate):
    #   thing1=事件类型 thing2=设备位置 thing3=设备名称 time4=发生时间(time) thing5=备注
    # 填错字段 key 或漏字段都会 47003
    data = {
        "thing1": {"value": "跌倒告警"},
        "thing2": {"value": (location or "家中")[:20]},
        "thing3": {"value": "长辈守护摄像头"},
        "time4": {"value": now},
        "thing5": {"value": f"风险等级：{risk_names.get(risk_level, risk_level)}；{description or ''}"[:20]},
    }
    subs = get_subscribers()
    sent = 0
    details = []
    for sub in subs:
        r = send_subscribe_message(sub["openid"], template_id, data, event_id=event_id)
        details.append({"openid": sub["openid"], "nickname": sub.get("nickname", ""), **r})
        if r.get("success"):
            sent += 1
    if not subs:
        return {"success": False, "sent": 0, "total": 0,
                "message": "暂无订阅家属（需在小程序授权订阅）", "details": []}
    return {"success": sent > 0, "sent": sent, "total": len(subs),
            "message": f"已推送 {sent}/{len(subs)} 位家属" if sent else "推送失败（订阅一次性配额或状态不符）",
            "details": details}
