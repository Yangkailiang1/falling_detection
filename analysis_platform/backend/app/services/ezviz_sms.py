"""
# 萤石云信令-短信通知服务
# 功能: 通过萤石云信令平台发送告警短信到指定手机号
# 对应方案书: §6.1 多通道推送 - 短信通知
#
# 产品入口: https://open.ys7.com/cn/s/24 (云信令-消息触达)
# API文档: https://open.ys7.com/help/570
#
# 短信发送接口:
#   待确认确切端点。萤石短信服务支持:
#   - 验证码类、通知类、营销类短信
#   - 触达率最高99%
#   - 通过API或控制台发送
#
# 短信服务需在萤石控制台开通并购买套餐
"""
import logging
import requests
from config import Config
from app.services.ezviz_auth import token_manager

logger = logging.getLogger(__name__)

# 短信发送 API 端点
# 注意: 此端点需要在萤石控制台开通「云信令-消息触达」短信服务后才能使用
# 产品入口: https://open.ys7.com/cn/s/24
# 文档帮助: https://open.ys7.com/help/570
#
# 端点可通过环境变量 EZS_SMS_ENDPOINT 覆盖
# 如果服务开通后端点仍 404，请在控制台 API 文档中查找确切路径
import os
SMS_ENDPOINT = os.getenv(
    "EZS_SMS_ENDPOINT",
    "/api/lapp/message/sms/send"  # 默认值，可能需调整
)


def send_sms(phone: str, content: str, sms_type: str = "notification") -> dict:
    """
    通过萤石云信令平台发送短信

    参数:
        phone: 目标手机号
        content: 短信内容（注意各类型有字数限制）
        sms_type: 短信类型 (notification/verification/marketing)

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
    url = f"{Config.EZS_API_BASE_URL}{SMS_ENDPOINT}"

    payload = {
        "accessToken": access_token,
        "phone": phone,
        "content": content,
        "type": sms_type,
    }

    logger.info(f"[SMS] Sending to {phone}: {content[:50]}...")

    try:
        resp = requests.post(url, data=payload, timeout=15)

        # 如果端点返回404，说明路径不对
        if resp.status_code == 404:
            logger.warning(
                f"[SMS] 端点 {SMS_ENDPOINT} 返回 404。"
                f"请在萤石控制台 https://open.ys7.com/cn/s/24 确认正确的短信API路径。"
            )
            return {
                "success": False,
                "code": "404",
                "message": (
                    f"SMS端点 {SMS_ENDPOINT} 未找到。"
                    f"请访问 https://open.ys7.com/cn/s/24 开通短信服务并确认API路径。"
                ),
                "phone": phone,
                "provider_response": {"http_status": 404},
            }

        data = resp.json()
        code = str(data.get("code", data.get("meta", {}).get("code", "")))
        message = data.get("msg", data.get("meta", {}).get("message", ""))

        if code in ("200", "0"):
            logger.info(f"[SMS] ✅ 发送成功 → {phone}")
            return {
                "success": True,
                "code": code,
                "message": message or "短信发送成功",
                "phone": phone,
                "provider_response": data,
            }
        else:
            logger.warning(f"[SMS] ❌ 发送失败: code={code}, msg={message}")
            return {
                "success": False,
                "code": code,
                "message": f"短信发送失败(code={code}): {message}",
                "phone": phone,
                "provider_response": data,
            }

    except requests.RequestException as e:
        logger.error(f"[SMS] 网络错误: {e}")
        return {
            "success": False,
            "code": "network_error",
            "message": f"网络请求失败: {str(e)}",
            "phone": phone,
            "provider_response": {},
        }
    except ValueError as e:
        # JSON 解析失败（可能端点返回了非JSON响应）
        logger.error(f"[SMS] 响应解析失败: {e}。端点 {SMS_ENDPOINT} 可能不正确。")
        return {
            "success": False,
            "code": "parse_error",
            "message": (
                f"响应解析失败，端点 {SMS_ENDPOINT} 可能不正确。"
                f"请访问 https://open.ys7.com/cn/s/24 查看云信令API文档。"
            ),
            "phone": phone,
            "provider_response": {},
        }
    except Exception as e:
        logger.error(f"[SMS] 未知错误: {e}")
        return {
            "success": False,
            "code": "unknown",
            "message": str(e),
            "phone": phone,
            "provider_response": {},
        }
