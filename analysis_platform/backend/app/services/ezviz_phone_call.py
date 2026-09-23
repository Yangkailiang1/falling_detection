"""
# 萤石云信令-电话告警外呼服务
# 功能: 设备告警触发时，自动拨打指定手机号进行语音通知
# 对应方案书: §6.1 多通道推送 - 电话告警外呼
#
# API文档: 萤石开放平台-云信令-电话提醒服务
# 产品入口: https://open.ys7.com/cn/s/24
#
# 电话外呼接口:
#   POST /api/lapp/device/alarm/phone/call
#   参数: accessToken, devId, channel, alarmType, phone
#   返回: {"meta": {"code": 200, ...}, "desc": "呼叫成功"}
#
# 错误码:
#   200      呼叫成功
#   400      参数错误
#   500      服务端繁忙
#   70003    无可用次数
#   70004    超出频次限制
#   70005    不支持该报警类型
#   70008    外呼失败
#   70009    模板正在审核中（约1个工作日）
#
# 注意: 使用前需在萤石控制台开通"电话提醒"服务
"""
import logging
import requests
from config import Config
from app.services.ezviz_auth import token_manager

logger = logging.getLogger(__name__)

# 电话外呼 API 端点
# 注意: 此端点需要在萤石控制台开通「电话提醒」服务后才能使用
# 产品入口: https://open.ys7.com/cn/s/24
# 文档帮助: https://open.ys7.com/help/717
#
# 端点可通过环境变量 EZS_PHONE_CALL_ENDPOINT 覆盖
# 如果服务开通后端点仍 404，请在控制台 API 文档中查找确切路径
import os
from app.inference.inference_config import DEVICE_SERIAL, TEST_PHONE
PHONE_CALL_ENDPOINT = os.getenv(
    "EZS_PHONE_CALL_ENDPOINT",
    "/api/lapp/device/alarm/phone/call"  # 默认值，可能需调整
)

# 告警类型: 使用人体感应事件(10000)作为跌倒检测的通用告警
ALARM_TYPE_FALL = 10000  # 人体感应事件


def call_phone(
    phone: str,
    device_serial: str = DEVICE_SERIAL,
    alarm_type: int = ALARM_TYPE_FALL,
    channel: int = 1,
    device_name: str = "C6C跌倒检测摄像头",
) -> dict:
    """
    触发萤石电话告警外呼

    当跌倒检测触发时，通过萤石云信令服务自动拨打指定手机号。
    电话接通后播放预设的告警语音通知。

    参数:
        phone: 目标手机号（仅支持手机号，不支持座机）
        device_serial: 设备序列号
        alarm_type: 告警类型码（默认10000=人体感应）
        channel: 设备通道号
        device_name: 设备名称

    返回:
        {
            "success": bool,
            "code": str,
            "message": str,
            "phone": str,
            "provider_response": dict,
        }
    """
    access_token = token_manager.get_token()
    url = f"{Config.EZS_API_BASE_URL}{PHONE_CALL_ENDPOINT}"

    payload = {
        "accessToken": access_token,
        "devId": device_serial,
        "channel": channel,
        "alarmType": alarm_type,
        "phone": phone,
        "deviceName": device_name,
    }

    logger.info(f"[PhoneCall] Calling {phone} via device {device_serial}, alarmType={alarm_type}")

    try:
        resp = requests.post(url, data=payload, timeout=15)

        # 如果端点返回404，说明路径不对或服务未开通
        if resp.status_code == 404:
            logger.warning(
                f"[PhoneCall] 端点 {PHONE_CALL_ENDPOINT} 返回 404。"
                f"请在萤石控制台 https://open.ys7.com/cn/s/24 确认正确的电话外呼API路径。"
            )
            return {
                "success": False,
                "code": "404",
                "message": (
                    f"电话外呼端点 {PHONE_CALL_ENDPOINT} 未找到。"
                    f"请访问 https://open.ys7.com/cn/s/24 开通电话提醒服务并确认API路径。"
                ),
                "phone": phone,
                "provider_response": {"http_status": 404},
            }

        data = resp.json()

        # 萤石电话外呼API有两种响应格式
        # 格式1: {"meta": {"code": 200, "message": "操作成功"}, "desc": "呼叫成功"}
        # 格式2: {"code": "200", "msg": "操作成功!"}（标准萤石格式降级）
        meta = data.get("meta", {})
        code = str(meta.get("code", data.get("code", "")))
        message = meta.get("message", data.get("msg", data.get("desc", "")))

        if code in ("200", "0"):
            logger.info(f"[PhoneCall] ✅ 呼叫成功 → {phone}")
            return {
                "success": True,
                "code": code,
                "message": message or "呼叫成功",
                "phone": phone,
                "provider_response": data,
            }
        else:
            error_msg = _translate_error(code, message)
            logger.warning(f"[PhoneCall] ❌ 呼叫失败: {error_msg}")
            return {
                "success": False,
                "code": code,
                "message": error_msg,
                "phone": phone,
                "provider_response": data,
            }

    except requests.RequestException as e:
        logger.error(f"[PhoneCall] 网络错误: {e}")
        return {
            "success": False,
            "code": "network_error",
            "message": f"网络请求失败: {str(e)}",
            "phone": phone,
            "provider_response": {},
        }
    except Exception as e:
        logger.error(f"[PhoneCall] 未知错误: {e}")
        return {
            "success": False,
            "code": "unknown",
            "message": str(e),
            "phone": phone,
            "provider_response": {},
        }


def _translate_error(code: str, original_msg: str) -> str:
    """翻译电话外呼错误码为可读信息"""
    error_map = {
        "400": "参数错误，请检查设备序列号和手机号格式",
        "500": "萤石服务端繁忙，请稍后重试",
        "70003": "无可用呼叫次数，请在萤石控制台购买电话提醒套餐",
        "70004": "超出频次限制，同一号码短时间内呼叫过于频繁",
        "70005": f"不支持该告警类型，当前使用类型码: {ALARM_TYPE_FALL}",
        "70008": "外呼失败，请检查号码是否有效",
        "70009": "电话提醒模板正在审核中（约1个工作日），审核通过后可用",
    }
    return error_map.get(code, f"未知错误(code={code}): {original_msg}")
