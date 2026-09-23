"""
# 模拟跌倒事件生成器
# 功能: 生成各种跌倒场景的模拟数据，用于测试事后全流程闭环
# 对应方案书: §3.1 并行双通道检测架构 — 模拟 V-JEPA 2 检测结果 + 骨骼关键点
"""
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from app.inference.inference_config import DEVICE_SERIAL, TEST_PHONE

# === 预定义的跌倒场景 ===
FALL_SCENARIOS = {
    "head_forward": {
        "name": "头部着地-向前跌倒",
        "description": "老人在客厅行走时被地毯绊倒，向前摔出，头部首先撞击地面",
        "touch_ground_part": "head",
        "fall_direction": "forward",
        "impact_velocity": (3.5, 6.0),     # (min, max) m/s
        "confidence": (0.90, 0.99),
        "body_tilt_angle": (60, 80),
        "com_velocity": (1.5, 3.0),
        "location": "客厅",
        "risk_level_expected": "I",
    },
    "head_sideways": {
        "name": "头部着地-侧向跌倒",
        "description": "老人从马桶站起后头晕，身体向一侧倾倒，头部撞击浴室地面",
        "touch_ground_part": "head",
        "fall_direction": "sideways_right",
        "impact_velocity": (4.0, 7.0),
        "confidence": (0.92, 0.99),
        "body_tilt_angle": (70, 90),
        "com_velocity": (2.0, 4.0),
        "location": "卫生间",
        "risk_level_expected": "I",
    },
    "spine_backward": {
        "name": "脊柱着地-向后跌倒",
        "description": "老人在厨房后退时失去平衡，后仰摔倒，背部/脊柱先着地",
        "touch_ground_part": "spine",
        "fall_direction": "backward",
        "impact_velocity": (3.0, 5.0),
        "confidence": (0.88, 0.97),
        "body_tilt_angle": (50, 70),
        "com_velocity": (1.0, 2.5),
        "location": "厨房",
        "risk_level_expected": "I",
    },
    "hip_sideways": {
        "name": "髋部着地-侧向跌倒",
        "description": "老人在卧室整理床铺时脚滑，侧身摔倒，右侧髋部先着地",
        "touch_ground_part": "hip",
        "fall_direction": "sideways_right",
        "impact_velocity": (2.0, 4.0),
        "confidence": (0.85, 0.95),
        "body_tilt_angle": (30, 50),
        "com_velocity": (1.0, 2.0),
        "location": "卧室",
        "risk_level_expected": "II",
    },
    "shoulder_forward": {
        "name": "肩部着地-向前跌倒",
        "description": "老人试图扶住沙发但未成功，向前摔倒，左肩先着地",
        "touch_ground_part": "shoulder",
        "fall_direction": "forward",
        "impact_velocity": (2.5, 4.5),
        "confidence": (0.82, 0.94),
        "body_tilt_angle": (40, 60),
        "com_velocity": (1.2, 2.2),
        "location": "客厅",
        "risk_level_expected": "II",
    },
    "hand_forward": {
        "name": "手部着地-向前跌倒",
        "description": "老人在阳台浇花时失去平衡，本能伸手支撑，手掌先着地",
        "touch_ground_part": "hand",
        "fall_direction": "forward",
        "impact_velocity": (1.5, 3.0),
        "confidence": (0.75, 0.90),
        "body_tilt_angle": (20, 40),
        "com_velocity": (0.8, 1.5),
        "location": "阳台",
        "risk_level_expected": "III",
    },
    "elbow_sideways": {
        "name": "肘部着地-侧向跌倒",
        "description": "老人转身过快导致不稳，侧向摔倒，右肘支撑着地",
        "touch_ground_part": "elbow",
        "fall_direction": "sideways_right",
        "impact_velocity": (1.5, 3.0),
        "confidence": (0.70, 0.88),
        "body_tilt_angle": (25, 45),
        "com_velocity": (0.8, 1.5),
        "location": "客厅",
        "risk_level_expected": "III",
    },
    "knee_forward": {
        "name": "膝部着地-向前跌倒",
        "description": "老人下楼梯最后一级踩空，向前跄踉，双膝跪地",
        "touch_ground_part": "knee",
        "fall_direction": "forward",
        "impact_velocity": (1.0, 2.5),
        "confidence": (0.72, 0.89),
        "body_tilt_angle": (15, 35),
        "com_velocity": (0.5, 1.2),
        "location": "楼梯口",
        "risk_level_expected": "III",
    },
}


@dataclass
class FallEventSimulation:
    """模拟的跌倒事件数据
    对应方案书: §3.1 V-JEPA 2 检测通道输出 + 骨骼点通道输出
    """
    event_id: str
    scenario_key: str           # 场景类型 key
    scenario_name: str          # 场景名称
    description: str            # 场景描述

    # V-JEPA 2 检测结果（模拟）
    fall_detected: bool         # 是否检测到跌倒
    detection_confidence: float # 检测置信度
    detection_latency_ms: float # 推理延迟(ms)

    # 骨骼关键点分析结果（模拟）
    touch_ground_part: str      # 首先触地部位
    fall_direction: str         # 跌倒方向
    impact_velocity: float      # 冲击速度 (m/s)
    body_tilt_angle: float      # 身体倾角 (度)
    center_of_mass_velocity: float  # 重心移动速度 (m/s)

    # 事件元信息
    timestamp: str              # 事件时间 ISO8601
    location: str               # 发生地点
    device_serial: str          # 检测设备编号
    video_window_frames: int    # 视频窗口帧数 (16帧)
    video_window_duration_s: float  # 视频窗口时长 (0.64s)

    # [2026-08-13] 实时管线 pose 风险(serial_pose_medical: critical/high/medium)可选。
    # 真实检测事件用它覆盖"触地部位→风险"的静态映射(静态只看部位, 忽略冲击严重度)。
    # 有默认值 → 必须放在无默认字段之后
    risk_level: str = ""

    @property
    def risk_level_expected(self) -> str:
        info = FALL_SCENARIOS.get(self.scenario_key, {})
        return info.get("risk_level_expected", "II")


def generate_scenarios_meta() -> list:
    """获取所有可用场景的元信息"""
    return [
        {
            "key": key,
            "name": info["name"],
            "description": info["description"],
            "location": info["location"],
            "touch_ground_part": info["touch_ground_part"],
            "risk_level_expected": info["risk_level_expected"],
        }
        for key, info in FALL_SCENARIOS.items()
    ]


def simulate_fall_event(
    scenario_key: str = None,
    device_serial: str = DEVICE_SERIAL,
    detection_confidence: float = None,
    impact_velocity: float = None,
    body_tilt_angle: float = None,
    com_velocity: float = None,
    location: str = None,
) -> FallEventSimulation:
    """
    生成一个模拟跌倒事件

    参数:
        scenario_key: 场景类型 key，不指定则随机选择
        device_serial: 检测设备序列号
        detection_confidence: 检测置信度，不指定则随机生成
        impact_velocity: 冲击速度，不指定则随机生成
        body_tilt_angle: 身体倾角，不指定则随机生成
        com_velocity: 重心速度，不指定则随机生成
        location: 发生地点，不指定则使用场景默认值

    返回: FallEventSimulation
    """
    if scenario_key is None:
        scenario_key = random.choice(list(FALL_SCENARIOS.keys()))
    elif scenario_key not in FALL_SCENARIOS:
        available = list(FALL_SCENARIOS.keys())
        scenario_key = random.choice(available)

    info = FALL_SCENARIOS[scenario_key]

    # 在合理范围内随机生成参数
    def rand_in_range(key: str, user_val=None):
        if user_val is not None:
            return user_val
        lo, hi = info[key]
        return round(random.uniform(lo, hi), 2)

    return FallEventSimulation(
        event_id=str(uuid.uuid4())[:8],
        scenario_key=scenario_key,
        scenario_name=info["name"],
        description=info["description"],
        fall_detected=True,
        detection_confidence=rand_in_range("confidence", detection_confidence),
        detection_latency_ms=round(random.uniform(8.0, 15.0), 1),
        touch_ground_part=info["touch_ground_part"],
        fall_direction=info["fall_direction"],
        impact_velocity=rand_in_range("impact_velocity", impact_velocity),
        body_tilt_angle=rand_in_range("body_tilt_angle", body_tilt_angle),
        center_of_mass_velocity=rand_in_range("com_velocity", com_velocity),
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        location=location if location else info["location"],
        device_serial=device_serial,
        video_window_frames=16,
        video_window_duration_s=0.64,
    )


def simulate_fall_event_custom(
    touch_ground_part: str = "hip",
    fall_direction: str = "sideways_right",
    detection_confidence: float = 0.90,
    impact_velocity: float = 3.0,
    body_tilt_angle: float = 45.0,
    com_velocity: float = 1.5,
    location: str = "客厅",
    device_serial: str = DEVICE_SERIAL,
) -> FallEventSimulation:
    """
    生成自定义参数的模拟跌倒事件
    用于前端测试特定风险等级的处理逻辑
    """
    return FallEventSimulation(
        event_id=str(uuid.uuid4())[:8],
        scenario_key="custom",
        scenario_name=f"{touch_ground_part}着地-自定义",
        description=f"自定义跌倒场景: {touch_ground_part}着地, {fall_direction}方向",
        fall_detected=True,
        detection_confidence=detection_confidence,
        detection_latency_ms=9.6,
        touch_ground_part=touch_ground_part,
        fall_direction=fall_direction,
        impact_velocity=impact_velocity,
        body_tilt_angle=body_tilt_angle,
        center_of_mass_velocity=com_velocity,
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        location=location,
        device_serial=device_serial,
        video_window_frames=16,
        video_window_duration_s=0.64,
    )
