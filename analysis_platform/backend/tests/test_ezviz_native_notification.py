#!/usr/bin/env python3
"""
测试萤石原生通知 API（电话外呼 + 短信）

使用前请确认:
  1. 后端服务运行中: cd backend && python run.py
  2. 萤石 accessToken 有效 (检查 .env 中的 EZS_ACCESS_TOKEN)
  3. 电话提醒服务已在萤石控制台开通 (https://open.ys7.com/cn/s/24)

测试流程:
  1. 测试电话外呼 → 应收到来自萤石的告警电话
  2. 测试短信发送 → 验证短信 API 端点
  3. 测试全流程通知编排 → 模拟完整跌倒事件通知

运行:
  cd /Users/yangkailiang/Documents/falling/analysis_platform/backend
  python tests/test_ezviz_native_notification.py
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.chdir(os.path.join(os.path.dirname(__file__), '..'))

from config import Config

TEST_PHONE = "CHANGE_ME_PHONE"
DEVICE_SERIAL = "CHANGE_ME_DEVICE_SERIAL"


def test_phone_call():
    """测试电话外呼"""
    print("\n" + "=" * 60)
    print("  测试 1/4: 萤石电话告警外呼")
    print(f"  目标号码: {TEST_PHONE}")
    print(f"  设备序列号: {DEVICE_SERIAL}")
    print("=" * 60)

    try:
        from app.services.ezviz_phone_call import call_phone

        result = call_phone(
            phone=TEST_PHONE,
            device_serial=DEVICE_SERIAL,
            alarm_type=10000,  # 人体感应
        )

        print(f"\n结果:")
        print(f"  成功: {result['success']}")
        print(f"  状态码: {result['code']}")
        print(f"  消息: {result['message']}")
        print(f"  原始响应: {result['provider_response']}")

        return result["success"]
    except Exception as e:
        print(f"\n❌ 异常: {e}")
        return False


def test_sms():
    """测试短信发送"""
    print("\n" + "=" * 60)
    print("  测试 2/4: 萤石短信通知")
    print(f"  目标号码: {TEST_PHONE}")
    print("=" * 60)

    try:
        from app.services.ezviz_sms import send_sms, SMS_ENDPOINT

        print(f"  短信API端点: {Config.EZS_API_BASE_URL}{SMS_ENDPOINT}")

        result = send_sms(
            phone=TEST_PHONE,
            content="【跌倒检测测试】这是一条来自萤石跌倒检测系统的测试短信。系统检测到跌倒事件，请关注。",
            sms_type="notification",
        )

        print(f"\n结果:")
        print(f"  成功: {result['success']}")
        print(f"  状态码: {result['code']}")
        print(f"  消息: {result['message']}")
        print(f"  原始响应: {result['provider_response']}")

        if not result["success"] and "404" in str(result.get("code", "")):
            print("\n  ⚠️  短信端点返回404，请按以下步骤排查:")
            print(f"   1. 访问 https://open.ys7.com/cn/s/24 开通云信令-短信服务")
            print("   2. 在萤石控制台API文档中查找正确的短信发送路径")
            print("   3. 更新 ezviz_sms.py 中的 SMS_ENDPOINT 变量")

        return result["success"]
    except Exception as e:
        print(f"\n❌ 异常: {e}")
        return False


def test_notification_service():
    """测试完整通知服务编排"""
    print("\n" + "=" * 60)
    print("  测试 3/4: 通知服务编排（电话 + 短信 + APP）")
    print("=" * 60)

    try:
        from app.services.notification_service import (
            NotificationRequest, dispatch_notifications
        )

        notif = NotificationRequest(
            event_id="test-001",
            risk_level="II",
            risk_level_name="中危",
            touch_ground_part="髋部",
            impact_velocity=3.5,
            fall_direction_cn="侧方",
            location="客厅",
            timestamp="2026-07-30 14:30:00",
            medical_report="测试医疗报告：髋部着地，建议X光检查排除骨折。",
            recommendation="建议立即就医检查，避免自行活动加重损伤。",
            emergency_contact_name="测试联络人",
            emergency_contact_phone=TEST_PHONE,
            elderly_name="测试老人",
            home_address="萤石跌倒检测测试环境",
        )

        print(f"  风险等级: II级(中危)")
        print(f"  通知通道: SMS + APP + 电话")
        print(f"  目标号码: {TEST_PHONE}")

        results = dispatch_notifications(notif, channels=["sms", "app", "phone"])

        print(f"\n通知结果:")
        all_success = True
        for channel, result in results.items():
            status = "✅" if result.success else "❌"
            print(f"  {status} {channel}: {result.message}")
            if not result.success:
                all_success = False
                print(f"     详情: {result.provider_response}")

        return all_success
    except Exception as e:
        print(f"\n❌ 异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_api_endpoint_probe():
    """探测正确的API端点"""
    print("\n" + "=" * 60)
    print("  测试 4/4: 探测电话外呼API端点")
    print("=" * 60)

    import requests
    from app.services.ezviz_auth import token_manager

    token = token_manager.get_token()
    base_url = Config.EZS_API_BASE_URL

    # 尝试多个可能的端点路径
    candidates = [
        "/api/lapp/device/alarm/phone/call",
        "/api/lapp/alarm/phone/call",
        "/api/lapp/voice/phone/call",
        "/api/lapp/phone/call",
        "/api/lapp/alarm/phone",
    ]

    for path in candidates:
        url = f"{base_url}{path}"
        try:
            resp = requests.post(url, data={
                "accessToken": token,
                "devId": DEVICE_SERIAL,
                "alarmType": 10000,
                "phone": TEST_PHONE,
                "channel": 1,
            }, timeout=10)

            status = resp.status_code
            try:
                body = resp.json()
                code = body.get("code", body.get("meta", {}).get("code", ""))
                msg = body.get("msg", body.get("meta", {}).get("message", ""))
            except:
                code = ""
                msg = resp.text[:100]

            if status == 200 or code in ("200", "0"):
                print(f"  ✅ {path} → HTTP {status} | code={code} | {msg}")
            elif status == 404:
                print(f"  ❌ {path} → HTTP 404 (端点不存在)")
            else:
                print(f"  ⚠️  {path} → HTTP {status} | code={code} | {msg}")

        except requests.RequestException as e:
            print(f"  🔌 {path} → 连接失败: {e}")


def main():
    print("\n" + "=" * 60)
    print("  萤石原生通知 API 测试套件")
    print(f"  测试号码: {TEST_PHONE}")
    print(f"  设备: {DEVICE_SERIAL}")
    print("=" * 60)

    # 显示配置信息
    print(f"\n配置检查:")
    print(f"  API Key: {'✓ 已配置' if Config.EZS_API_KEY else '✗ 未配置'}")
    print(f"  Token有效期: ...{Config.EZS_ACCESS_TOKEN[-8:] if Config.EZS_ACCESS_TOKEN else 'N/A'}")

    results = {}

    # Test 1: Phone call
    results["phone_call"] = test_phone_call()

    # Test 2: SMS
    results["sms"] = test_sms()

    # Test 3: Full notification
    results["notification"] = test_notification_service()

    # Test 4: Endpoint probe
    test_api_endpoint_probe()

    # Summary
    print("\n" + "=" * 60)
    print("  测试总结")
    print("=" * 60)
    for name, success in results.items():
        status = "✅ 通过" if success else "❌ 失败"
        print(f"  {name}: {status}")

    print("\n  💡 提示:")
    print("  - 电话外呼需要在萤石控制台开通「电话提醒」服务")
    print("    入口: https://open.ys7.com/cn/s/24")
    print("  - 短信服务可能存在不同的端点路径")
    print("    请根据 Test 4 的探测结果更新 ezviz_sms.py 中的 SMS_ENDPOINT")
    print("  - 如果返回 70003，需要购买电话提醒套餐")


if __name__ == "__main__":
    main()
