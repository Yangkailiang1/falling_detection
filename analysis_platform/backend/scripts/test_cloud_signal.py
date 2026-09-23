"""
# 云信令短信/电话服务验证脚本 [V7.2]
# 功能: 开通后一键实测萤石云信令 短信 + 电话外呼 通道
# 用法: cd backend && python3 scripts/test_cloud_signal.py [--phone 138xxxx]
#       (默认号码取 .env 的 EZS_PHONE，兜底 CHANGE_ME_PHONE)
#
# 输出: 每通道 成功/失败 + 错误码翻译 + 开通指引
# 说明: 云信令短信/电话服务处于内测阶段，需联系萤石客服开通
#       产品入口: https://open.ys7.com/cn/s/24
#       短信文档: https://open.ys7.com/help/570 | 电话文档: https://open.ys7.com/help/717
"""
import os
import sys
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# === 错误码翻译 ===
SMS_ERROR_HINTS = {
    "404": "短信端点未开通或路径不对 — 请在萤石控制台(open.ys7.com/cn/s/24)开通云信令-消息触达服务，并从API文档确认确切端点后更新 .env 的 EZS_SMS_ENDPOINT",
    "70003": "无可用短信次数 — 请购买短信套餐",
    "70004": "超出频次限制 — 同一号码短时间内发送过于频繁",
    "70005": "短信类型不支持 — 检查 sms_type 参数 (notification/verification/marketing)",
    "70009": "短信模板审核中（约1个工作日）",
}

PHONE_ERROR_HINTS = {
    "404": "电话外呼端点未开通或路径不对 — 请在萤石控制台(open.ys7.com/cn/s/24)开通电话提醒服务，并从API文档确认确切端点后更新 .env 的 EZS_PHONE_CALL_ENDPOINT",
    "70003": "无可用呼叫次数 — 请购买电话提醒套餐",
    "70004": "超出频次限制 — 同一号码短时间内呼叫过于频繁",
    "70005": "不支持该告警类型 — 当前使用 alarmType=10000(人体感应)",
    "70008": "外呼失败 — 检查号码是否有效",
    "70009": "电话提醒模板审核中（约1个工作日）",
}


def _hint(code, hints):
    """错误码 → 提示"""
    if str(code) in hints:
        return f"→ {hints[str(code)]}"
    if str(code) == "network_error":
        return "→ 网络请求失败，请检查网络连接"
    return "→ 未知错误，请对照萤石开放平台错误码文档"


def main():
    parser = argparse.ArgumentParser(description="萤石云信令短信/电话服务验证")
    parser.add_argument("--phone", help="测试手机号（默认取 .env EZS_PHONE）")
    parser.add_argument("--device", default="CHANGE_ME_DEVICE_SERIAL", help="设备序列号")
    parser.add_argument("--skip-sms", action="store_true", help="跳过短信测试")
    parser.add_argument("--skip-phone", action="store_true", help="跳过电话测试")
    args = parser.parse_args()

    from config import Config
    from app.services.ezviz_auth import token_manager

    phone = args.phone or getattr(Config, 'EZS_PHONE', '') or "CHANGE_ME_PHONE"
    sms_endpoint = getattr(Config, 'EZS_SMS_ENDPOINT', '/api/lapp/message/sms/send')
    phone_endpoint = getattr(Config, 'EZS_PHONE_CALL_ENDPOINT', '/api/lapp/device/alarm/phone/call')

    print("=" * 60)
    print("萤石云信令 短信/电话 服务验证")
    print("=" * 60)
    print(f"API 地址: {Config.EZS_API_BASE_URL}")
    print(f"设备: {args.device} | 测试号码: {phone}")
    print(f"短信端点: {sms_endpoint}")
    print(f"电话端点: {phone_endpoint}")
    print()

    # === 1. Token 验证 ===
    print("--- 1. Token 验证 ---")
    try:
        token = token_manager.get_token()
        expire = token_manager.get_expire_info()
        print(f"✅ Token 有效 | 过期时间戳: {expire['expire_time']} | 剩余: {expire['valid_seconds']}s")
    except Exception as e:
        print(f"❌ Token 获取失败: {e}")
        print("   → 请检查 .env 的 EZS_APP_KEY / EZS_APP_SECRET")
        sys.exit(1)
    print()

    # === 2. 短信测试 ===
    if not args.skip_sms:
        print("--- 2. 短信发送测试 ---")
        from app.services.ezviz_sms import send_sms
        content = (
            f"【跌倒预警】测试老人在客厅发生跌倒，头部着地。"
            f"风险等级I级(高危)。建议立即就医。详情请查看平台。"
        )[:150]
        try:
            result = send_sms(phone=phone, content=content, sms_type="notification")
            if result.get("success"):
                print(f"✅ 短信发送成功 → {phone}")
                print(f"   响应: {result.get('provider_response')}")
            else:
                code = result.get("code", "unknown")
                print(f"❌ 短信发送失败: code={code} | {result.get('message')}")
                print(f"   {_hint(code, SMS_ERROR_HINTS)}")
        except Exception as e:
            print(f"❌ 短信测试异常: {e}")
        print()

    # === 3. 电话外呼测试 ===
    if not args.skip_phone:
        print("--- 3. 电话外呼测试 ---")
        from app.services.ezviz_phone_call import call_phone
        try:
            result = call_phone(
                phone=phone,
                device_serial=args.device,
                alarm_type=10000,   # 人体感应事件
                channel=1,
                device_name="C6C跌倒检测摄像头",
            )
            if result.get("success"):
                print(f"✅ 电话外呼成功 → {phone}")
                print(f"   响应: {result.get('provider_response')}")
            else:
                code = result.get("code", "unknown")
                print(f"❌ 电话外呼失败: code={code} | {result.get('message')}")
                print(f"   {_hint(code, PHONE_ERROR_HINTS)}")
        except Exception as e:
            print(f"❌ 电话测试异常: {e}")
        print()

    # === 4. 开通指引 ===
    print("--- 4. 开通指引（若上方任一通道失败）---")
    print("""
  1) 短信服务: 萤石控制台 → 产品 → 云存储与云信令 → 云信令-消息触达
     入口: https://open.ys7.com/cn/s/24 （内测中，需联系萤石客服开通）
     开通后: 从控制台 API 文档确认确切端点 → 更新 .env EZS_SMS_ENDPOINT
  2) 电话提醒: 同上入口，开通"电话提醒"服务
     开通后: 更新 .env EZS_PHONE_CALL_ENDPOINT
  3) 错误码 70009 模板审核: 约1个工作日
  4) 重新运行本脚本验证: python3 scripts/test_cloud_signal.py
""")


if __name__ == "__main__":
    main()
