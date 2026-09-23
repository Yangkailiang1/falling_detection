"""
微信小程序家属端 API [产品化 Phase1]

POST /api/mini/subscribe      — 家属订阅: openid 绑定设备
POST /api/mini/unsubscribe    — 取消订阅
GET  /api/mini/subscribers    — 订阅者列表 (调试)
GET  /api/mini/devices        — 已绑定设备列表 (含状态)
POST /api/mini/device/bind    — 激活码绑定设备
POST /api/mini/device/unbind  — 解绑设备

小程序登录: code → openid (wx.login → 后端 code2Session)
POST /api/mini/login          — {code} → {openid}
"""

from flask import Blueprint, request
import os
from app.utils.response import success_response, error_response
from app.inference.inference_config import DEVICE_SERIAL

mini_bp = Blueprint('mini', __name__)

# 设备 → 激活码映射 (从 .env 读取萤石激活码 EZS_ACTIVATION_CODE_1~5)
# 真实场景: 激活码印在产品包装上, 用户输入/扫码绑定
def _load_activation_codes() -> dict:
    codes = {}
    for i in range(1, 6):
        code = os.getenv(f"EZS_ACTIVATION_CODE_{i}", "").strip()
        if code:
            codes[DEVICE_SERIAL] = code  # 多激活码都绑定到当前设备
            break
    # 测试兜底: 0000 仍可用
    if not codes:
        codes[DEVICE_SERIAL] = "0000"
    return codes


_DEVICE_ACTIVATION = _load_activation_codes()


@mini_bp.route('/mini/login', methods=['POST'])
def mini_login():
    """小程序 wx.login code → openid"""
    body = request.get_json(silent=True) or {}
    code = body.get('code', '')
    if not code:
        return error_response("缺少 code", 400)
    import os
    import requests as rq
    appid = os.getenv("WX_MINI_APPID", "")
    secret = os.getenv("WX_MINI_SECRET", "")
    if not appid or not secret:
        return error_response("小程序 appid/secret 未配置", 500)
    resp = rq.get("https://api.weixin.qq.com/sns/jscode2session", params={
        "appid": appid, "secret": secret, "js_code": code,
        "grant_type": "authorization_code",
    }, timeout=10).json()
    if "openid" in resp:
        return success_response({"openid": resp["openid"], "session_key": resp.get("session_key", "")})
    return error_response(f"登录失败: {resp}", 400)


@mini_bp.route('/mini/subscribe', methods=['POST'])
def subscribe():
    """家属订阅: openid 绑定设备 (告警推送给该 openid)。"""
    body = request.get_json(silent=True) or {}
    openid = body.get('openid', '')
    device_serial = body.get('device_serial', DEVICE_SERIAL)
    nickname = body.get('nickname', '')
    if not openid:
        return error_response("缺少 openid", 400)
    from app.services.subscribe_message import subscribe_user
    info = subscribe_user(openid, device_serial, nickname)
    return success_response({"subscribed": True, "info": info})


@mini_bp.route('/mini/unsubscribe', methods=['POST'])
def unsubscribe():
    body = request.get_json(silent=True) or {}
    openid = body.get('openid', '')
    if not openid:
        return error_response("缺少 openid", 400)
    from app.services.subscribe_message import unsubscribe_user
    ok = unsubscribe_user(openid)
    return success_response({"unsubscribed": ok})


@mini_bp.route('/mini/subscribers', methods=['GET'])
def subscribers():
    """订阅者列表 (调试用)。"""
    from app.services.subscribe_message import get_subscribers
    return success_response({"subscribers": get_subscribers()})


@mini_bp.route('/mini/devices', methods=['GET'])
def devices():
    """自动发现: 返回萤石账号下所有设备 (含在线状态)。"""
    try:
        from app.services.ezviz_device import get_device_list
        devices = get_device_list(0, 100).get('list', [])
        result = [{
            "device_serial": d.get("deviceSerial", ""),
            "device_name": d.get("deviceName", ""),
            "status": "online" if d.get("status") == 1 else "offline",
            "model": d.get("model", ""),
        } for d in devices]
        return success_response({"devices": result})
    except Exception as e:
        return error_response(f"设备列表获取失败: {str(e)[:100]}", 500)


@mini_bp.route('/mini/device/bind', methods=['POST'])
def bind_device():
    """绑定设备: 校验设备在萤石账号下存在 (摄像头已用萤石App配网, 无需激活码)。"""
    body = request.get_json(silent=True) or {}
    device_serial = body.get('device_serial', '').strip()
    openid = body.get('openid', '')
    if not device_serial:
        return error_response("缺少设备序列号", 400)
    # 校验设备存在于萤石账号
    try:
        from app.services.ezviz_device import get_device_list
        devices = get_device_list(0, 100).get('list', [])
        found = any(d.get('deviceSerial') == device_serial for d in devices)
        if not found:
            return error_response("设备不存在或不在当前账号下，请检查序列号", 400)
    except Exception as e:
        return error_response(f"设备校验失败: {str(e)[:100]}", 500)
    if openid:
        from app.services.subscribe_message import subscribe_user
        subscribe_user(openid, device_serial, body.get('nickname', ''))
    return success_response({"bound": True, "device_serial": device_serial})


@mini_bp.route('/mini/device/unbind', methods=['POST'])
def unbind_device():
    body = request.get_json(silent=True) or {}
    openid = body.get('openid', '')
    if not openid:
        return error_response("缺少 openid", 400)
    from app.services.subscribe_message import unsubscribe_user
    unsubscribe_user(openid)
    return success_response({"unbound": True})
