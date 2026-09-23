"""
# 跌倒事件事后响应编排器
# 功能: 串联 跌倒检测→骨骼分析→医疗报告→分级干预→事件归档 全流程
# 对应方案书: §3.1 分级干预与全闭环处置、§8.1 响应机制
"""
import time
import threading
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from app.services.fall_event_simulator import (
    FallEventSimulation, simulate_fall_event, simulate_fall_event_custom,
    generate_scenarios_meta,
)
from app.services.skeleton_analyzer import (
    analyze_static, SkeletonAnalysisResult,
)
from app.services.fall_event_archive import (
    FallEventRecord, TimelineEntry, create_event, get_event, update_event,
    add_timeline_entry, submit_feedback, list_events, get_stats,
    _events as archive_events,
)


def process_fall_event(
    event: FallEventSimulation,
    skip_medical_report: bool = False,
    inquiry_first: bool = False,
) -> FallEventRecord:
    """
    处理跌倒事件的全流程编排

    对应方案书 §3.1 事后响应流程:
    1. 跌倒确认 — V-JEPA 2 检测到跌倒信号
    2. 骨骼着地分析 — 识别触地部位 + 冲击参数
    3. 风险等级判定 — I/II/III 级 → 驱动分级干预
    4. 医疗报告生成 — 萤石大模型生成结构化医疗简报
    5. 分级干预决策 — 根据风险等级决定响应策略
    6. 事件归档 — 完整时间线 + 结构化报告

    参数:
        event: 模拟跌倒事件
        skip_medical_report: 跳过医疗报告生成（用于快速测试）
        inquiry_first: 先启动语音问询，再异步完成抓拍/录像/医疗简报

    返回: FallEventRecord（归档记录）
    """
    tl_start = time.time()

    # === Stage 1: 跌倒检测确认 ===
    detect_time = datetime.now(timezone.utc)
    record = FallEventRecord(
        event_id=event.event_id,
        status="detected",
        created_at=detect_time.isoformat(),
        detection_confidence=event.detection_confidence,
        detection_latency_ms=event.detection_latency_ms,
        video_window_frames=event.video_window_frames,
        video_window_duration_s=event.video_window_duration_s,
        touch_ground_part="",
        fall_direction=event.fall_direction,
        impact_velocity=0,
        body_tilt_angle=0,
        center_of_mass_velocity=0,
        risk_level="",
        risk_level_name="",
        location=event.location,
        device_serial=event.device_serial,
        scenario_key=event.scenario_key,
        scenario_name=event.scenario_name,
        description=event.description,
        timeline=[
            TimelineEntry(
                time=detect_time.isoformat(),
                stage="detected",
                label="跌倒检测",
                detail=f"V-JEPA 2 检测到跌倒, 置信度 {event.detection_confidence:.1%}, "
                       f"推理延迟 {event.detection_latency_ms}ms",
                status="success",
                duration_ms=0,
            )
        ],
    )
    if create_event(record) is None:
        raise RuntimeError("已有跌倒处置流程进行中，忽略新的事件")

    # === Stage 2: 骨骼着地分析 ===
    stage2_start = time.time()
    skeleton_result = analyze_static(
        touch_ground_part=event.touch_ground_part,
        fall_direction=event.fall_direction,
        impact_velocity=event.impact_velocity,
        body_tilt_angle=event.body_tilt_angle,
        com_vel=event.center_of_mass_velocity,
        risk_override=getattr(event, "risk_level", "") or "",
    )
    stage2_duration = int((time.time() - stage2_start) * 1000)

    record.touch_ground_part = skeleton_result.touch_ground_part
    record.risk_level = skeleton_result.risk_level
    record.risk_level_name = skeleton_result.risk_level_name
    record.impact_velocity = skeleton_result.impact_velocity
    record.body_tilt_angle = skeleton_result.body_tilt_angle
    record.center_of_mass_velocity = skeleton_result.center_of_mass_velocity
    record.likely_injury_types = skeleton_result.likely_injury_types
    record.response_strategy = skeleton_result.response_strategy
    record.countdown_seconds = skeleton_result.countdown_seconds

    touch_keypoints = ", ".join(skeleton_result.touch_ground_keypoints[:3])
    add_timeline_entry(record.event_id, TimelineEntry(
        time=datetime.now(timezone.utc).isoformat(),
        stage="analyzed",
        label="骨骼分析",
        detail=f"{skeleton_result.touch_ground_part}首先触地(关键点: {touch_keypoints}), "
               f"冲击速度 ~{skeleton_result.impact_velocity}m/s, "
               f"{skeleton_result.fall_direction_cn}方向, "
               f"风险等级 {skeleton_result.risk_level}级({skeleton_result.risk_level_name})",
        status="success",
        duration_ms=stage2_duration,
    ))
    record.status = "analyzed"
    update_event(record.event_id,
        status="analyzed",
        touch_ground_part=record.touch_ground_part,
        risk_level=record.risk_level,
        risk_level_name=record.risk_level_name,
        impact_velocity=record.impact_velocity,
        body_tilt_angle=record.body_tilt_angle,
        center_of_mass_velocity=record.center_of_mass_velocity,
        likely_injury_types=record.likely_injury_types,
        response_strategy=record.response_strategy,
        countdown_seconds=record.countdown_seconds,
    )

    inquiry_first_started = False
    if inquiry_first:
        try:
            from app.services.fall_inquiry import start_inquiry
            inquiry_result = start_inquiry(
                event_id=record.event_id,
                risk_level=skeleton_result.risk_level,
                countdown_seconds=skeleton_result.countdown_seconds,
            )
            inquiry_first_started = bool(inquiry_result.get("started"))
            if inquiry_first_started:
                record.status = "inquiring"
                update_event(record.event_id, status="inquiring")
                logger.info("[Orchestrator] inquiry-first started: %s", record.event_id)
            else:
                logger.warning("[Orchestrator] inquiry-first not started: %s | %s", record.event_id, inquiry_result)
        except Exception as exc:
            logger.error("[Orchestrator] inquiry-first failed: %s", exc)

    # === Stage 3: 现场抓拍联动 [V7.2] ===
    # 先抓拍现场图, 让医疗简报能结合画面生成 (V9.5: 文字检测数据 + 现场图发视觉大模型)
    stage_cap_start = time.time()
    capture_pic_url = ""
    capture_time = ""
    local_capture_path = ""
    try:
        from app.services.ezviz_capture import capture_device
        result = {}
        cap_thread = threading.Thread(
            target=lambda: result.update(capture_device(record.device_serial, channel_no=1) or {}),
            daemon=True,
        )
        cap_thread.start()
        cap_thread.join(timeout=3)  # 最多等 3s，不阻塞主流程
        capture_pic_url = result.get("pic_url", "")
        raw_capture_time = result.get("capture_time", 0)
        # 萤石抓拍接口的 captureTime 常为 0/缺失 → 用当前时间作为抓拍时间 [V9.3]
        capture_time = ""
        try:
            ct = int(raw_capture_time)
            if ct > 0:
                # 毫秒时间戳 → ISO (本地时区)
                capture_time = datetime.fromtimestamp(ct / 1000).astimezone().isoformat()
        except (TypeError, ValueError):
            pass
        if not capture_time:
            capture_time = datetime.now().astimezone().isoformat()
        if capture_pic_url:
            record.capture_pic_url = capture_pic_url
            record.capture_time = capture_time
            # 下载并持久化本地 (萤石签名 URL 24h 过期 → 存本地避免后续加载失败) [V9.4]
            try:
                from app.services.capture_assets import persist_capture_pic
                local_capture_path = persist_capture_pic(record.event_id, capture_pic_url)
            except Exception:
                local_capture_path = ""
            if local_capture_path:
                record.capture_pic_path = local_capture_path
            update_event(record.event_id,
                capture_pic_url=capture_pic_url, capture_time=capture_time,
                capture_pic_path=local_capture_path)
            add_timeline_entry(record.event_id, TimelineEntry(
                time=datetime.now(timezone.utc).isoformat(),
                stage="capturing",
                label="现场抓拍",
                detail=f"已抓拍现场图片并关联事件: {capture_pic_url[:80]}",
                status="success",
                duration_ms=int((time.time() - stage_cap_start) * 1000),
            ))
            logger.info(f"[Orchestrator] 现场抓拍成功: {capture_pic_url[:80]}")
        else:
            logger.warning(f"[Orchestrator] 抓拍失败或设备离线: {record.device_serial}")
    except Exception as e:
        logger.warning(f"[Orchestrator] 抓拍异常(不影响主流程): {e}")

    # === Stage 4: 分级语音问询（V7.3）===
    # Ⅰ/Ⅱ/Ⅲ 级统一进入语音问询倒计时（10s/30s/60s），不立即通知；
    # 通知由问询调度器在"帮我呼叫"或"超时"时触发（I级四通道/II级三通道/III级APP）
    # [2026-08-13] 问询提前到医疗报告之前 → 检测即播问询, 不阻塞于 LLM 报告(~10s)
    stage_inquiry_start = time.time()
    level_strategy = _get_level_strategy(skeleton_result.risk_level)

    try:
        from app.services.fall_inquiry import start_inquiry, record_event_clip
        if not inquiry_first_started:
            # 无条件触发事件录像 (幂等: 与 start_inquiry 内部同入口去重, 每个事件只录一次)
            record_event_clip(record.event_id, seconds=12)
            inquiry_result = start_inquiry(
                event_id=record.event_id,
                risk_level=skeleton_result.risk_level,
                countdown_seconds=skeleton_result.countdown_seconds,
            )
            if not inquiry_result.get("started"):
                logger.warning(f"[Orchestrator] 问询未启动: {record.event_id} | {inquiry_result}")
        else:
            # 问询已先启动；录像从后台线程完成，不延迟首句播报。
            threading.Thread(
                target=record_event_clip,
                args=(record.event_id,),
                kwargs={"seconds": 12},
                daemon=True,
            ).start()
    except Exception as e:
        logger.error(f"[Orchestrator] 问询启动异常: {e}")

    stage_inquiry_duration = int((time.time() - stage_inquiry_start) * 1000)

    # 事件进入问询中状态（终态由调度器在 cancel/help/timeout 时写入）
    record.status = "inquiring"
    update_event(record.event_id, status="inquiring")

    # === Stage 5: 医疗报告生成 (文字检测数据 + 现场抓拍图, 视觉大模型) ===
    # [2026-08-13] 后台异步生成: 不阻塞问询播报, 也不阻塞 process_fall_event 返回
    # → ①语音秒起 ②_dump_fall_clip 及时捕捉跌倒帧(帧缓冲不滚走)
    if not skip_medical_report:
        _report_event_id = record.event_id
        _report_data = {
            "landing_part": skeleton_result.touch_ground_part,
            "time": detect_time.strftime("%Y-%m-%d %H:%M:%S"),
            "confidence": event.detection_confidence,
            "impact_velocity": skeleton_result.impact_velocity,
            "fall_direction": skeleton_result.fall_direction_cn,
            "risk_level": skeleton_result.risk_level,  # 实际分级(含 pose 覆盖) [2026-08-13]
        }
        _report_image_path = local_capture_path

        def _gen_medical_report():
            try:
                from app.services.ezviz_chat import generate_fall_medical_report
                _r = generate_fall_medical_report(_report_data, image_path=_report_image_path)
                _text = _r.get("report", "")
                _rec = _r.get("recommendation", "")
            except Exception as e:
                _text = f"[医疗报告生成失败: {str(e)}]"
                _rec = "报告生成异常，建议人工评估"
            update_event(_report_event_id, medical_report=_text, report_recommendation=_rec)
            add_timeline_entry(_report_event_id, TimelineEntry(
                time=datetime.now(timezone.utc).isoformat(),
                stage="reporting",
                label="医疗报告生成",
                detail=f"萤石大模型已生成结构化医疗简报(含现场图)" if _text else "医疗报告生成已跳过",
                status="success" if _text else "pending",
                duration_ms=0,
            ))

        threading.Thread(target=_gen_medical_report, daemon=True).start()

    # === Stage 6: 初步归档（问询中） ===
    total_duration = int((time.time() - tl_start) * 1000)
    add_timeline_entry(record.event_id, TimelineEntry(
        time=datetime.now(timezone.utc).isoformat(),
        stage="archived",
        label="事件已受理",
        detail=f"检测→分析→抓拍→报告(含现场图)→问询启动 全流程耗时 {total_duration}ms，等待老人回应（{skeleton_result.countdown_seconds}s）",
        status="success",
        duration_ms=total_duration,
    ))

    return record


def _get_level_strategy(risk_level: str) -> str:
    """根据风险等级返回干预策略
    对应方案书: §8.1 响应机制
    """
    strategies = {
        "I": "I级高危 — 10秒语音问询，无回应自动通知家属（企业微信+小程序订阅）；120急救为预留能力",
        "II": "II级中危 — 30秒语音问询确认，无回应则自动通知家属（企业微信+小程序订阅）",
        "III": "III级低危 — 60秒温和提示，建议自行检查是否受伤",
    }
    return strategies.get(risk_level, "默认处理策略")


def quick_simulate(scenario_key: str = None, skip_medical_report: bool = False) -> FallEventRecord:
    """
    快速模拟：一键生成并处理跌倒事件
    参数:
        scenario_key: 场景类型，不指定则随机
        skip_medical_report: 跳过医疗报告生成
    返回: FallEventRecord
    """
    event = simulate_fall_event(scenario_key=scenario_key)
    return process_fall_event(event, skip_medical_report=skip_medical_report)


def batch_simulate(scenario_keys: list = None, count: int = 5, skip_medical_report: bool = False):
    """
    批量模拟多个跌倒事件
    参数:
        scenario_keys: 指定场景列表，不指定则随机选
        count: 模拟数量
        skip_medical_report: 跳过医疗报告生成（开发后期默认 False，全流程走通）
    """
    if scenario_keys is None:
        from app.services.fall_event_simulator import FALL_SCENARIOS
        scenario_keys = list(FALL_SCENARIOS.keys())

    results = []
    for i in range(count):
        scenario = scenario_keys[i % len(scenario_keys)]
        try:
            record = quick_simulate(scenario_key=scenario, skip_medical_report=skip_medical_report)
            results.append({"event_id": record.event_id, "status": "success"})
        except Exception as e:
            results.append({"scenario": scenario, "status": "error", "error": str(e)})
    return results
