"""
# 骨骼着地分析模块
# 功能: 根据跌倒时的骨骼关键点轨迹,识别首先触地部位、估算冲击参数、判定风险等级
# 对应方案书: §4.3 骨骼点运动分析、§3.1 骨骼点着地部位分析 → 风险等级判定
"""
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

# === COCO 17 骨骼关键点索引 ===
COCO_KEYPOINTS = [
    "nose",           # 0
    "left_eye",       # 1
    "right_eye",      # 2
    "left_ear",       # 3
    "right_ear",      # 4
    "left_shoulder",  # 5
    "right_shoulder", # 6
    "left_elbow",     # 7
    "right_elbow",    # 8
    "left_wrist",     # 9
    "right_wrist",    # 10
    "left_hip",       # 11
    "right_hip",      # 12
    "left_knee",      # 13
    "right_knee",     # 14
    "left_ankle",     # 15
    "right_ankle",    # 16
]

# 关键点分组: 触地部位 → 风险等级映射
# 对应方案书: §4.3 着地部位 → 风险等级
TOUCH_GROUND_RISK_MAP = {
    # I级高危 — 10s语音问询（无回应自动紧急联络+120）
    "head": {
        "level": "I",
        "level_name": "高危",
        "keypoints": ["nose", "left_eye", "right_eye", "left_ear", "right_ear"],
        "injury_types": ["颅脑损伤", "颅内出血", "颈椎骨折"],
        "response": "10秒语音问询确认，无回应则自动紧急联络家属 + 自动对接120急救系统",
        "countdown": 10,
    },
    "spine": {
        "level": "I",
        "level_name": "高危",
        "keypoints": ["left_shoulder", "right_shoulder"],
        "injury_types": ["脊髓损伤", "脊柱骨折", "椎体压缩性骨折"],
        "response": "10秒语音问询确认，无回应则自动紧急联络家属 + 自动对接120急救系统",
        "countdown": 10,
    },
    # II级中危 — 30s语音问询
    "hip": {
        "level": "II",
        "level_name": "中危",
        "keypoints": ["left_hip", "right_hip"],
        "injury_types": ["股骨颈骨折", "髋部骨折", "骨盆损伤"],
        "response": "30秒语音问询确认，无回应则自动紧急联络",
        "countdown": 30,
    },
    "shoulder": {
        "level": "II",
        "level_name": "中危",
        "keypoints": ["left_shoulder", "right_shoulder"],
        "injury_types": ["锁骨骨折", "肩关节脱位", "肱骨近端骨折"],
        "response": "30秒语音问询确认，无回应则自动紧急联络",
        "countdown": 30,
    },
    # III级低危 — 60s温和提示
    "hand": {
        "level": "III",
        "level_name": "低危",
        "keypoints": ["left_wrist", "right_wrist", "left_elbow", "right_elbow"],
        "injury_types": ["腕部骨折", "尺桡骨骨折", "软组织损伤"],
        "response": "60秒温和提示，建议自行检查是否受伤",
        "countdown": 60,
    },
    "elbow": {
        "level": "III",
        "level_name": "低危",
        "keypoints": ["left_elbow", "right_elbow"],
        "injury_types": ["肘关节脱位", "尺骨鹰嘴骨折", "肱骨远端骨折"],
        "response": "60秒温和提示，建议自行检查是否受伤",
        "countdown": 60,
    },
    "knee": {
        "level": "III",
        "level_name": "低危",
        "keypoints": ["left_knee", "right_knee"],
        "injury_types": ["髌骨骨折", "半月板损伤", "韧带撕裂"],
        "response": "60秒温和提示，建议自行检查是否受伤",
        "countdown": 60,
    },
}

# 跌倒方向映射
FALL_DIRECTION_MAP = {
    "forward": "向前",
    "backward": "向后",
    "sideways_left": "向左侧",
    "sideways_right": "向右侧",
}


@dataclass
class Keypoint:
    """单个骨骼关键点"""
    name: str
    idx: int
    x: float       # 图像归一化坐标 0-1
    y: float       # 图像归一化坐标 0-1
    confidence: float  # 检测置信度 0-1


@dataclass
class KeypointFrame:
    """一帧的骨骼关键点集合"""
    frame_id: int
    timestamp: float  # 相对时间(s), 0=跌倒检测时刻
    keypoints: list  # list[Keypoint]


@dataclass
class SkeletonAnalysisResult:
    """骨骼着地分析结果
    对应方案书: §4.3 着地部位分析输出
    """
    # 触地部位识别
    touch_ground_part: str          # head/spine/hip/shoulder/hand/elbow/knee
    touch_ground_keypoints: list    # 首先触地的具体关键点名称列表
    # 风险等级
    risk_level: str                 # I / II / III
    risk_level_name: str            # 高危 / 中危 / 低危
    # 冲击参数
    impact_velocity: float          # 估算冲击速度 (m/s)
    fall_direction: str             # forward/backward/sideways_left/sideways_right
    fall_direction_cn: str          # 向前/向后/向左侧/向右侧
    # 伤害推断
    likely_injury_types: list       # 可能的伤害类型列表
    body_tilt_angle: float          # 身体倾角 (度, 脊柱与地面夹角)
    center_of_mass_velocity: float  # 重心移动速度 (m/s)
    # 干预策略
    response_strategy: str          # 建议的干预策略描述
    countdown_seconds: int          # 语音问询倒计时秒数
    # 元信息
    analysis_time: str              # 分析完成时间 ISO8601
    total_frames_analyzed: int      # 分析的帧数


def _get_keypoint_by_name(frame: KeypointFrame, name: str) -> Optional[Keypoint]:
    """从帧中按名称查找关键点"""
    for kp in frame.keypoints:
        if kp.name == name:
            return kp
    return None


def _get_keypoint_group_y_min(frame: KeypointFrame, names: list) -> tuple:
    """
    获取关键点组中 y 坐标最小的点（最靠近地面）
    返回: (point_name, y_value, confidence)
    """
    min_kp = None
    min_y = float('inf')
    for name in names:
        kp = _get_keypoint_by_name(frame, name)
        if kp and kp.confidence > 0.3 and kp.y < min_y:
            min_y = kp.y
            min_kp = kp
    if min_kp:
        return (min_kp.name, min_kp.y, min_kp.confidence)
    return (None, None, None)


def analyze_touch_ground(
    frames: list,
    frame_height: float = 480.0,
    pixel_to_meter: float = 0.005
) -> SkeletonAnalysisResult:
    """
    分析骨骼关键点轨迹，识别首先触地部位并判定风险等级

    算法核心:
    1. 在跌倒检测时间窗口前后各取 N 帧骨骼数据
    2. 计算各组关键点的垂直位移速度和到达地面时间
    3. 识别首先接触地面的关键点组
    4. 估算冲击速度: v = sqrt(2*g*h) 简化模型
    5. 判定风险等级 I/II/III

    对应方案书: §4.3 着地部位分析（跌倒确认后触发，分级预警核心输入）

    参数:
        frames: 骨骼关键点帧序列（按时间排序）
        frame_height: 帧图像高度（像素）
        pixel_to_meter: 像素到米的转换系数

    返回: SkeletonAnalysisResult
    """
    if len(frames) < 2:
        return SkeletonAnalysisResult(
            touch_ground_part="unknown",
            touch_ground_keypoints=[],
            risk_level="II",
            risk_level_name="中危",
            impact_velocity=0.0,
            fall_direction="unknown",
            fall_direction_cn="未知",
            likely_injury_types=["无法判断，需人工确认"],
            body_tilt_angle=0.0,
            center_of_mass_velocity=0.0,
            response_strategy="默认30秒语音问询",
            countdown_seconds=30,
            analysis_time=datetime.now(timezone.utc).isoformat(),
            total_frames_analyzed=len(frames),
        )

    # 假设帧率为 f，时间间隔估计
    first_frame = frames[0]
    last_frame = frames[-1]

    # 1. 计算各组关键点的 y 方向位移（y 增大 = 向下移动）
    drops = {}  # part_name -> (total_drop_px, start_y, end_y, velocity_px_per_frame)
    for part_name, risk_info in TOUCH_GROUND_RISK_MAP.items():
        kp_names = risk_info["keypoints"]
        # 取该组关键点在首帧和末帧的平均 y 坐标
        start_ys = []
        end_ys = []
        for name in kp_names:
            kp_start = _get_keypoint_by_name(first_frame, name)
            kp_end = _get_keypoint_by_name(last_frame, name)
            if kp_start and kp_end and kp_start.confidence > 0.3 and kp_end.confidence > 0.3:
                start_ys.append(kp_start.y)
                end_ys.append(kp_end.y)

        if start_ys and end_ys:
            avg_start = sum(start_ys) / len(start_ys)
            avg_end = sum(end_ys) / len(end_ys)
            drop = avg_end - avg_start  # 正数=向下
            drops[part_name] = {
                "drop_px": drop,
                "start_y": avg_start,
                "end_y": avg_end,
            }

    if not drops:
        # 没有可靠的关键点数据，返回默认结果
        return SkeletonAnalysisResult(
            touch_ground_part="unknown",
            touch_ground_keypoints=[],
            risk_level="II",
            risk_level_name="中危",
            impact_velocity=0.0,
            fall_direction="unknown",
            fall_direction_cn="未知",
            likely_injury_types=["骨骼数据不足，需人工确认"],
            body_tilt_angle=0.0,
            center_of_mass_velocity=0.0,
            response_strategy="默认30秒语音问询",
            countdown_seconds=30,
            analysis_time=datetime.now(timezone.utc).isoformat(),
            total_frames_analyzed=len(frames),
        )

    # 2. 识别首先触地部位: y 坐标最大（最接近地面）的关键点组
    #    在最后一帧中找到 y 最大的组
    max_y_part = None
    max_y = -float('inf')
    for part_name, data in drops.items():
        if data["end_y"] > max_y:
            max_y = data["end_y"]
            max_y_part = part_name

    # 同时也考虑下降幅度最大的组
    max_drop_part = None
    max_drop = -float('inf')
    for part_name, data in drops.items():
        if data["drop_px"] > max_drop:
            max_drop = data["drop_px"]
            max_drop_part = part_name

    # 综合判断：优先使用下降幅度最大的，其次是 y 坐标最低的
    touch_part = max_drop_part if max_drop_part else max_y_part
    risk_info = TOUCH_GROUND_RISK_MAP.get(touch_part, TOUCH_GROUND_RISK_MAP["hip"])

    # 3. 估算冲击速度
    # h = drop_px * pixel_to_meter (估算高度变化)
    # v = sqrt(2 * g * h)  (自由落体近似)
    drop_data = drops.get(touch_part, {})
    drop_px = drop_data.get("drop_px", 100)
    estimated_height = abs(drop_px) * pixel_to_meter
    impact_velocity = math.sqrt(2 * 9.81 * max(estimated_height, 0.1))
    impact_velocity = round(impact_velocity, 1)

    # 4. 判定跌倒方向
    # 比较左右侧关键点的水平位移
    left_hip = _get_keypoint_by_name(last_frame, "left_hip")
    right_hip = _get_keypoint_by_name(last_frame, "right_hip")
    nose_kp = _get_keypoint_by_name(last_frame, "nose")

    fall_direction = "sideways_right"  # 默认
    if left_hip and right_hip and left_hip.confidence > 0.3 and right_hip.confidence > 0.3:
        hip_x_diff = right_hip.x - left_hip.x  # 正=右侧更低
        if abs(hip_x_diff) < 0.05:
            if nose_kp and left_hip:
                if nose_kp.y > left_hip.y:
                    fall_direction = "forward"  # 鼻子低于髋部 = 向前倒
                else:
                    fall_direction = "backward"
        elif hip_x_diff > 0.05:
            fall_direction = "sideways_right"
        else:
            fall_direction = "sideways_left"

    # 5. 计算脊柱倾角（脊柱端点连接线与垂直线的夹角）
    nose_kp_start = _get_keypoint_by_name(last_frame, "nose")
    hip_mid_start = None
    if left_hip and right_hip:
        hip_mid_start = Keypoint(
            name="hip_mid", idx=-1,
            x=(left_hip.x + right_hip.x) / 2,
            y=(left_hip.y + right_hip.y) / 2,
            confidence=min(left_hip.confidence, right_hip.confidence)
        )
    if nose_kp_start and hip_mid_start:
        dx = nose_kp_start.x - hip_mid_start.x
        dy = nose_kp_start.y - hip_mid_start.y  # 图像坐标系 y 向下
        tilt_deg = round(abs(math.degrees(math.atan2(dx, abs(dy)))), 1)
    else:
        tilt_deg = 0.0

    # 6. 计算重心移动速度
    com_vel = 0.0
    if hip_mid_start:
        hip_start_first = None
        left_hip_first = _get_keypoint_by_name(first_frame, "left_hip")
        right_hip_first = _get_keypoint_by_name(first_frame, "right_hip")
        if left_hip_first and right_hip_first:
            hip_start_first = (
                (left_hip_first.x + right_hip_first.x) / 2,
                (left_hip_first.y + right_hip_first.y) / 2,
            )
        if hip_start_first:
            dx = hip_mid_start.x - hip_start_first[0]
            dy = hip_mid_start.y - hip_start_first[1]
            displacement_px = math.sqrt(dx**2 + dy**2)
            displacement_m = displacement_px * pixel_to_meter
            time_s = abs(last_frame.timestamp - first_frame.timestamp)
            com_vel = round(displacement_m / max(time_s, 0.01), 2)

    # 7. 组装结果
    return SkeletonAnalysisResult(
        touch_ground_part=touch_part,
        touch_ground_keypoints=risk_info["keypoints"],
        risk_level=risk_info["level"],
        risk_level_name=risk_info["level_name"],
        impact_velocity=impact_velocity,
        fall_direction=fall_direction,
        fall_direction_cn=FALL_DIRECTION_MAP.get(fall_direction, "未知"),
        likely_injury_types=risk_info["injury_types"],
        body_tilt_angle=tilt_deg,
        center_of_mass_velocity=com_vel,
        response_strategy=risk_info["response"],
        countdown_seconds=risk_info["countdown"],
        analysis_time=datetime.now(timezone.utc).isoformat(),
        total_frames_analyzed=len(frames),
    )


def _resolve_risk_key(touch_part: str) -> str:
    """把触地部位解析到 TOUCH_GROUND_RISK_MAP 的键。

    触地部位可能来自实时 pose 的 COCO 关键点名(left_wrist/left_elbow 等),
    也可能来自规则表通用名(hand/elbow/hip)。直接 .get() 会漏掉关键点名 → 错误落到 hip 兜底
    (实测: left_wrist 触地却显示髋部骨折) [2026-08-13 修复]。
    """
    t = (touch_part or "").strip().lower()
    if t in TOUCH_GROUND_RISK_MAP:
        return t
    # 去左右前缀: left_wrist→wrist, left_shoulder→shoulder
    for prefix in ("left_", "right_"):
        if t.startswith(prefix):
            t = t[len(prefix):]
            break
    if t in TOUCH_GROUND_RISK_MAP:
        return t
    # 按 keypoints 反查 (wrist → hand): 规则表 keypoints 存的是全名(left_wrist),
    # 去前缀后的 wrist 匹配不到 → 同时用原名匹配
    for part_name, risk_info in TOUCH_GROUND_RISK_MAP.items():
        kps = risk_info["keypoints"]
        if t in kps or touch_part.lower() in kps:
            return part_name
    return "hip"  # 兜底


# [2026-08-13] 实时 pose 风险(serial_pose_medical) → V5 分级覆盖
# 真实跌倒以 pose 风险为准(考虑冲击评分/头躯干/无恢复), 不只看触地部位
RISK_LEVEL_OVERRIDE = {
    "critical": {
        "level": "I", "level_name": "高危", "countdown": 10,
        "response": "10秒语音问询确认，无回应则自动紧急联络家属 + 自动对接120急救系统",
    },
    "high": {
        "level": "II", "level_name": "中危", "countdown": 30,
        "response": "30秒语音问询确认，无回应则自动紧急联络",
    },
    "medium": {
        "level": "III", "level_name": "低危", "countdown": 60,
        "response": "60秒温和提示，建议自行检查是否受伤",
    },
}


def analyze_static(
    touch_ground_part: str,
    fall_direction: str = "sideways_right",
    impact_velocity: float = 3.0,
    body_tilt_angle: float = 45.0,
    com_vel: float = 1.5,
    risk_override: str = "",
) -> SkeletonAnalysisResult:
    """
    快速分析：直接根据已知的触地部位和参数生成分析结果
    用于模拟测试场景，不需要完整的骨骼关键点帧序列

    参数:
        touch_ground_part: 触地部位 (head/spine/hip/shoulder/hand/elbow/knee)
        fall_direction: 跌倒方向 (forward/backward/sideways_left/sideways_right)
        impact_velocity: 冲击速度 (m/s)
        body_tilt_angle: 身体倾角 (度)
        com_vel: 重心移动速度 (m/s)
        risk_override: 实时 pose 风险(critical/high/medium, 可选)。
            提供时覆盖风险等级/倒计时/策略(考虑冲击严重度), 伤害类型仍按触地部位。
    """
    risk_info = TOUCH_GROUND_RISK_MAP[_resolve_risk_key(touch_ground_part)]
    level_ov = RISK_LEVEL_OVERRIDE.get((risk_override or "").lower())
    if level_ov:
        risk_level = level_ov["level"]
        risk_level_name = level_ov["level_name"]
        countdown = level_ov["countdown"]
        response = level_ov["response"]
    else:
        risk_level = risk_info["level"]
        risk_level_name = risk_info["level_name"]
        countdown = risk_info["countdown"]
        response = risk_info["response"]
    fall_dir_cn = FALL_DIRECTION_MAP.get(fall_direction, "未知")

    return SkeletonAnalysisResult(
        touch_ground_part=touch_ground_part,
        touch_ground_keypoints=risk_info["keypoints"],
        risk_level=risk_level,
        risk_level_name=risk_level_name,
        impact_velocity=impact_velocity,
        fall_direction=fall_direction,
        fall_direction_cn=fall_dir_cn,
        likely_injury_types=risk_info["injury_types"],
        body_tilt_angle=body_tilt_angle,
        center_of_mass_velocity=com_vel,
        response_strategy=response,
        countdown_seconds=countdown,
        analysis_time=datetime.now(timezone.utc).isoformat(),
        total_frames_analyzed=1,
    )
