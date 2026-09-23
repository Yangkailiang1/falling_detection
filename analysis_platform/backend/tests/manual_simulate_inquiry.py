"""
# 语音问询分级流程手动模拟脚本 [V7.3]
# 用法: cd backend && python3 tests/manual_simulate_inquiry.py
#
# 顺序演示 6 种情况（真实 orchestrator + 真实调度器 + 真实通知链路）:
#   1. I级(头部)  + 我没事      → 10s问询内取消，无通知
#   2. I级(头部)  + 无回应      → 10s问询超时 → 自动紧急联络(短信+APP+电话+120)
#   3. II级(髋部) + 帮我呼叫    → 立即紧急联络(短信+APP+电话)
#   4. II级(髋部) + 无回应      → 30s问询超时 → 自动紧急联络
#   5. III级(手部)+ 我没事      → 60s问询内取消，无通知
#   6. III级(手部)+ 无回应      → 60s问询超时 → 自动紧急联络(仅APP)
#
# 注: 通知走真实通道（短信/电话未开通返回404，收件箱/120真实生效），
#     timeout 用例等待真实倒计时（I级10s最快），可加 --fast 缩短。
"""
import sys
import time

sys.path.insert(0, "<RELEASE_ROOT>/analysis_platform/backend")


def _show(rec):
    from app.services.fall_event_archive import get_event
    r = get_event(rec.event_id)
    print(f"    event_id={r.event_id} | risk={r.risk_level}级 | "
          f"voice_confirm_status={r.voice_confirm_status} | status={r.status} | "
          f"countdown={r.countdown_seconds}s | notification={r.notification_status or {}}")
    if r.feedback:
        print(f"    feedback: is_false_alarm={r.feedback.get('is_false_alarm')}")


def _wait_dispatch(eid, timeout=5.0):
    """等待异步紧急联络落地（status 变为 notified/archived）"""
    from app.services.fall_event_archive import get_event
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = get_event(eid)
        if r and r.status in ("notified", "archived"):
            return
        time.sleep(0.2)


def main():
    fast = "--fast" in sys.argv
    from app.services.fall_orchestrator import quick_simulate
    from app.services.fall_inquiry import resolve_inquiry, clear_inquiries
    from app.services.notification_inbox import list_notifications

    scenarios = [
        ("head_forward", "cancel", "我没事", 0),     # 1. I级+取消
        ("head_forward", "timeout", "", 10),          # 2. I级+超时(10s)
        ("hip_sideways", "help", "帮我呼叫", 0),      # 3. II级+求助
        ("hip_sideways", "timeout", "", 30),          # 4. II级+超时(30s)
        ("hand_forward", "cancel", "我没事", 0),      # 5. III级+取消
        ("hand_forward", "timeout", "", 60),          # 6. III级+超时(60s)
    ]

    print("=" * 70)
    print("语音问询分级流程模拟（6 种情况）")
    print("=" * 70)
    for i, (scenario, action, text, wait) in enumerate(scenarios, 1):
        print(f"\n--- 情况 {i}: {scenario} | 老人回应='{text or '无回应'}' | action={action} ---")
        rec = quick_simulate(scenario_key=scenario, skip_medical_report=True)
        _show(rec)
        if action == "cancel":
            result = resolve_inquiry(rec.event_id, "cancel", text)
            print(f"    resolve_inquiry(cancel): {result}")
        elif action == "help":
            result = resolve_inquiry(rec.event_id, "help", text)
            print(f"    resolve_inquiry(help): {result}")
        else:
            if fast and wait > 12:
                # 真实超时需 30s/60s，fast 模式跳过（timeout 逻辑已由 pytest 0.5s 参数化覆盖）
                print(f"    [fast] 真实超时需 {wait}s，跳过等待（pytest 已覆盖 timeout 逻辑）")
            else:
                print(f"    等待 {wait}s 倒计时结束（超时自动联络）...")
                time.sleep(wait if not fast else wait)  # fast 模式仅 I 级 10s 可真实等待
        _wait_dispatch(rec.event_id)
        _show(rec)
        inbox = list_notifications(limit=3)
        print(f"    收件箱最新: {[n['title'] for n in inbox[:2]]}")
        clear_inquiries()
        time.sleep(0.3)

    print("\n" + "=" * 70)
    print("模拟完成。短信/电话通道未开通会返回 404（属预期，开通后生效）。")
    print("=" * 70)


if __name__ == "__main__":
    main()
