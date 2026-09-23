"""
# 算法迭代平台 - 合成骨骼序列生成器
# 功能: 生成演示/脱敏上传用的 COCO-17 骨骼关键点序列
#       （真实客户归档当前不持久化关键点，见 CLAUDE.md；来源显式标注 origin:"synthetic"）
# 仅依赖 numpy，供 anonymize / demo_generator / collector 使用
"""
import numpy as np

# ── COCO-17 关键点定义 ────────────────────────────────────────────────────
COCO17_NAMES = [
    'nose', 'left_eye', 'right_eye', 'left_ear', 'right_ear',
    'left_shoulder', 'right_shoulder', 'left_elbow', 'right_elbow',
    'left_wrist', 'right_wrist', 'left_hip', 'right_hip',
    'left_knee', 'right_knee', 'left_ankle', 'right_ankle',
]

# 肢连接表（前端 SVG 渲染用）：(i, j) 为 COCO-17 索引
BONES = [
    (0, 1), (0, 2), (1, 3), (2, 4),            # 面部
    (5, 6), (5, 11), (6, 12), (11, 12),        # 躯干
    (5, 7), (7, 9), (6, 8), (8, 10),           # 手臂
    (11, 13), (13, 15), (12, 14), (14, 16),    # 腿
]

# 站立模板（640×480 画布，正面站立，手自然下垂）
STANDING = np.array([
    [320, 140],   # 0 nose
    [311, 132],   # 1 left_eye
    [329, 132],   # 2 right_eye
    [302, 136],   # 3 left_ear
    [338, 136],   # 4 right_ear
    [290, 192],   # 5 left_shoulder
    [350, 192],   # 6 right_shoulder
    [272, 252],   # 7 left_elbow
    [368, 252],   # 8 right_elbow
    [258, 318],   # 9 left_wrist
    [382, 318],   # 10 right_wrist
    [304, 302],   # 11 left_hip
    [336, 302],   # 12 right_hip
    [300, 384],   # 13 left_knee
    [340, 384],   # 14 right_knee
    [295, 452],   # 15 left_ankle
    [345, 452],   # 16 right_ankle
], dtype=np.float32)

# 各跌倒方向的"躺地"姿态模板（身体横/侧躺在地面，y≈地面线）
_FALLEN = {
    'forward': np.array([
        [300, 452], [298, 450], [302, 450], [296, 449], [304, 449],
        [310, 444], [360, 444], [340, 430], [380, 432],
        [356, 416], [396, 420], [318, 440], [368, 440],
        [330, 434], [384, 436], [338, 428], [396, 430],
    ], dtype=np.float32),
    'backward': np.array([
        [340, 450], [338, 448], [342, 448], [336, 447], [344, 447],
        [350, 442], [300, 442], [380, 430], [320, 432],
        [404, 418], [344, 416], [352, 438], [302, 438],
        [390, 432], [330, 434], [404, 428], [338, 430],
    ], dtype=np.float32),
    'sideways_left': np.array([
        [120, 452], [122, 450], [118, 450], [124, 449], [116, 449],
        [150, 446], [128, 446], [220, 432], [180, 432],
        [288, 420], [232, 420], [160, 440], [140, 440],
        [220, 436], [190, 436], [290, 430], [248, 430],
    ], dtype=np.float32),
    'sideways_right': np.array([
        [520, 452], [518, 450], [522, 450], [516, 449], [524, 449],
        [490, 446], [512, 446], [420, 432], [460, 432],
        [352, 420], [408, 420], [480, 440], [500, 440],
        [420, 436], [450, 436], [350, 430], [392, 430],
    ], dtype=np.float32),
    'unknown': None,
}

# 下蹲-起身（误报动作）的臀部最低点 y 偏移
_SQUAT_HIP_Y = 400


def _smoothstep(t: np.ndarray) -> np.ndarray:
    """0→1 平滑过渡"""
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _confidence(shape, rng: np.random.RandomState) -> np.ndarray:
    """生成 0.4~1.0 的置信度（整体 0.85±0.1，个别关节偏低，模仿 MediaPipe 输出）"""
    conf = np.clip(rng.normal(0.85, 0.08, size=shape), 0.4, 1.0).astype(np.float32)
    if shape[0] > 0 and shape[1] > 0:
        # 手腕/脚踝在快速运动帧置信度略低
        low_idx = [9, 10, 15, 16]
        for j in low_idx:
            if j < shape[1]:
                conf[:, j] *= rng.uniform(0.7, 0.9)
    return np.clip(conf, 0.3, 1.0).astype(np.float32)


def _fall_sequence(fallen_pose: np.ndarray, n_frames: int, fps: int,
                   fall_frame_index: int, seed: int) -> dict:
    """站立→跌倒→躺地的通用序列生成器"""
    rng = np.random.RandomState(seed)
    T = n_frames
    keypoints = np.zeros((T, 17, 2), dtype=np.float32)
    confs = _confidence((T, 17), rng)

    # 每帧的跌倒相位（fall_frame_index 处完成约 70%）
    phase = np.zeros(T, dtype=np.float32)
    fall_start = max(0, fall_frame_index - 12)
    fall_end = min(T - 1, fall_frame_index + 6)
    for i in range(T):
        if i <= fall_start:
            phase[i] = 0.0
        elif i >= fall_end:
            phase[i] = 1.0
        else:
            phase[i] = (i - fall_start) / (fall_end - fall_start)
    phase = _smoothstep(phase)

    # 叠加插值：站立 → 躺地
    for i in range(T):
        pose = STANDING * (1 - phase[i]) + fallen_pose * phase[i]
        # 抖动（站立期轻微，跌倒期较大）
        jitter = rng.normal(0, 1.5 + 2.0 * phase[i], size=(17, 2))
        pose = pose + jitter
        keypoints[i] = np.clip(pose, 0, 640).astype(np.float32)

    return _to_payload(keypoints, confs, fall_frame_index, fps, n_frames)


def _nonfall_sequence(n_frames: int, fps: int, fall_frame_index: int, seed: int) -> dict:
    """误报动作：站立 → 下蹲/弯腰 → 起身（臀部下降但不到地）"""
    rng = np.random.RandomState(seed)
    T = n_frames
    keypoints = np.zeros((T, 17, 2), dtype=np.float32)
    confs = _confidence((T, 17), rng)

    phase = np.zeros(T, dtype=np.float32)
    squat_start = max(0, fall_frame_index - 8)
    squat_end = min(T - 1, fall_frame_index + 10)
    for i in range(T):
        if i <= squat_start:
            phase[i] = 0.0
        elif i >= squat_end:
            phase[i] = 0.0          # 起身回到站立
        else:
            phase[i] = np.sin((i - squat_start) / (squat_end - squat_start) * np.pi)

    for i in range(T):
        p = phase[i]
        pose = STANDING.copy()
        # 臀部下降、膝盖弯曲、躯干前倾（弯腰捡物）
        hip_center = (pose[11] + pose[12]) / 2
        pose[11] += np.array([-6 * p, (hip_center[1] - pose[11][1]) * p])
        pose[12] += np.array([6 * p, (hip_center[1] - pose[12][1]) * p])
        pose[:, 1] += (_SQUAT_HIP_Y - hip_center[1]) * p * 0.6   # 整体下移
        pose[5, 1] += 40 * p
        pose[6, 1] += 40 * p
        pose[13, 1] += 30 * p
        pose[14, 1] += 30 * p
        pose[9, 1] += 20 * p
        pose[10, 1] += 20 * p
        pose = pose + rng.normal(0, 1.2, size=(17, 2))
        keypoints[i] = np.clip(pose, 0, 640).astype(np.float32)

    return _to_payload(keypoints, confs, fall_frame_index, fps, n_frames)


def _to_payload(keypoints, confs, fall_frame_index, fps, n_frames) -> dict:
    """归一化为事件 schema 的 skeleton_sequence 字典（嵌套列表，便于 JSON 存储）"""
    T = keypoints.shape[0]
    ts = [(i - fall_frame_index) / fps for i in range(T)]
    return {
        'origin': 'synthetic',
        'format': 'coco17',
        'n_frames': int(n_frames),
        'fps': int(fps),
        'fall_frame_index': int(fall_frame_index),
        'timestamps_s': [round(x, 3) for x in ts],
        'keypoints': [[[round(float(x), 1), round(float(y), 1)] for x, y in frame]
                      for frame in keypoints],
        'confidences': [[round(float(c), 3) for c in frame] for frame in confs],
    }


def synthetic_skeleton_for_event(touch_ground_part: str, fall_direction: str,
                                 is_fall: bool, n_frames: int = 48, fps: int = 25,
                                 seed: int = 42) -> dict:
    """
    高层入口：按事件的触地部位/方向/真值生成一致的骨骼序列
    供 anonymize.anonymize_record(attach_skeleton=True) 与 collector 使用
    """
    direction = fall_direction if fall_direction in _FALLEN else 'forward'
    fall_frame_index = int(n_frames * 0.6)  # ≈28/48
    if is_fall:
        seq = _fall_sequence(_FALLEN[direction], n_frames, fps, fall_frame_index, seed)
    else:
        seq = _nonfall_sequence(n_frames, fps, fall_frame_index, seed)
    return seq
