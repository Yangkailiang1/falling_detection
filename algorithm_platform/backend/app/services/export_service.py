"""
# 算法迭代平台 - 数据集导出服务
# 功能:
#   build_events_json  - 导出全部脱敏事件为 JSON（供人工检查/归档）
#   build_training_zip - 导出为 s-jepa SkeletonDataset 可消费的训练数据
#                        (keypoints/{fall,normal}/{name}_keypoints.npy + _confs.npy + split.json)
"""
import io
import json
import random
import zipfile
from typing import List

import numpy as np

# s-jepa SkeletonDataset 期望的 16 帧上下文窗口
CONTEXT_FRAMES = 16


def build_events_json(events: List[dict]) -> bytes:
    """全部脱敏事件 → JSON bytes（数组，按时间倒序）"""
    return json.dumps(events, ensure_ascii=False, indent=2).encode('utf-8')


def _name_for(scene_prefix: str, event: dict) -> str:
    """导出文件名：scene_prefix + event_id；无前缀则用 source_id + event_id"""
    if scene_prefix:
        return f"{scene_prefix}_{event['event_id']}"
    return f"{event['source_id']}_{event['event_id']}"


def build_training_zip(events: List[dict], scene_prefix: str = "", seed: int = 42) -> bytes:
    """
    构建 s-jepa 训练数据集 zip（内存中）
    参数:
        events       - 脱敏事件列表（须含 skeleton_sequence）
        scene_prefix - Le2i 场景前缀（如 Home_01），用于绕过 SkeletonDataset
                       _split_name_to_kp_name 的场景前缀限制；空则用 source_id 作 scene
    返回: zip bytes（keypoints/fall|normal/*.npy + split.json）
    """
    rng = random.Random(seed)
    buf = io.BytesIO()
    split = {'metadata': {'source': 'algorithm_platform', 'scene_prefix': scene_prefix},
             'videos': {}}

    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, event in enumerate(events):
            seq = event.get('skeleton_sequence')
            if not seq:
                continue
            name = _name_for(scene_prefix, event)
            is_fall = bool(event.get('ground_truth', {}).get('is_fall'))
            subdir = 'fall' if is_fall else 'normal'

            # keypoints: (T,17,2) float32 像素坐标；confidences: (T,17) float32
            kp = np.array(seq['keypoints'], dtype=np.float32)
            conf = np.array(seq['confidences'], dtype=np.float32)
            if kp.ndim != 3 or kp.shape[1] != 17:
                continue

            zf.writestr(f"keypoints/{subdir}/{name}_keypoints.npy", _npy_bytes(kp))
            zf.writestr(f"keypoints/{subdir}/{name}_confs.npy", _npy_bytes(conf))

            # split.json 条目（对齐 prepare_le2i_split.py 输出 schema）
            n_frames = int(seq.get('n_frames', kp.shape[0]))
            fall_idx = int(seq.get('fall_frame_index', 0))
            fall_start = max(0, fall_idx - 4)
            fall_end = min(n_frames, fall_idx + 12)
            leak = i % 17  # round-robin 0..16
            ctx_start = max(0, fall_start - (CONTEXT_FRAMES - leak))
            split['videos'][name] = {
                'split': 'train' if rng.random() < 0.8 else 'test',
                'is_fall': is_fall,
                'scene': scene_prefix or event.get('source_id', ''),
                'n_frames': n_frames,
                'ctx_start': ctx_start,
                'fall_start': fall_start,
                'fall_end': fall_end,
                'leak': leak,
            }

        zf.writestr('split.json', json.dumps(split, ensure_ascii=False, indent=2))

    return buf.getvalue()


def _npy_bytes(arr: np.ndarray) -> bytes:
    """numpy 数组 → .npy 文件字节"""
    out = io.BytesIO()
    np.save(out, arr)
    return out.getvalue()
