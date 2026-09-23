"""
# 算法迭代平台 - 脱敏转换服务
# 功能: 把客户侧分析平台的扁平 FallEventRecord 转换为脱敏后的嵌套事件字典
# 原则: 只上传骨骼数据 + 跌倒相关分析，删除一切可识别身份的字段
#       （设备序列号/家庭位置/自由文本描述/抓拍图/视频/通知内部状态/时间线明细）
# 纯 stdlib 实现，供 collector/seed 工具独立 import（无需 Flask 环境）
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

# 脱敏时被删除的原始字段（自证审计用，写入 privacy.fields_removed）
DROPPED_FIELDS = [
    'device_serial',        # 设备序列号 = 设备身份
    'location',             # 家庭位置（"客厅"）
    'description',          # 自由文本（含家庭/人员描述）
    'capture_pic_url',      # 抓拍图 URL（含人脸/室内画面）
    'capture_time',
    'capture_pic_path',
    'video_clip',           # 现场视频
    'notification_status',  # 内部通知通道状态，无 ML 价值
    'timeline_detail',      # 时间线明细文本（含抓拍签名 URL）
]

ANON_VERSION = 1


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def make_source_id(device_serial: str, salt: str = '') -> str:
    """设备序列号 → 确定性站点别名（脱敏，加盐防反推）"""
    return 'src_' + _sha256((device_serial or '') + salt)[:8]


def make_event_id(source_id: str, raw_event_id: str) -> str:
    """原始事件ID → 确定性脱敏事件ID（去重安全）"""
    return 'algo_' + _sha256(f'{source_id}:{raw_event_id}')[:10]


def _derive_ground_truth(raw: dict) -> dict:
    """由反馈/状态/语音结果派生真实跌倒标签。

    **手动反馈是权威 ground truth** (2026-08-12): 家属/医生在小程序或网页端
    明确标注过 (feedback 含 is_false_alarm 键) 时, 以其为准——即使与语音问询结果相反
    (如语音取消但家属确认跌倒, 或语音求救但家属标记误报), 也以手动标注为准。
    """
    feedback = raw.get('feedback') or {}
    voice_status = raw.get('voice_confirm_status', '')
    status = raw.get('status', '')

    if feedback and 'is_false_alarm' in feedback:
        is_fall = not bool(feedback.get('is_false_alarm'))
        origin = 'feedback'
    elif status == 'false_alarm':
        is_fall, origin = False, 'status'
    elif voice_status == 'cancelled':
        is_fall, origin = False, 'voice'
    elif voice_status and voice_status != 'pending':
        is_fall, origin = True, 'voice'
    else:
        is_fall, origin = True, 'detection'
    return {'is_fall': is_fall, 'label_origin': origin}


def _timeline_summary(raw: dict) -> dict:
    """时间线脱敏：仅保留阶段序列 + 总耗时，删除 detail 文本"""
    timeline = raw.get('timeline') or []
    stages = []
    total_ms = 0
    for entry in timeline:
        if isinstance(entry, dict):
            if entry.get('stage'):
                stages.append(entry['stage'])
            total_ms += int(entry.get('duration_ms') or 0)
    return {'stages': stages, 'total_duration_ms': total_ms}


def anonymize_record(raw: dict, salt: str = '', attach_skeleton: bool = False) -> dict:
    """
    原始 FallEventRecord → 脱敏事件字典
    参数:
        raw(dict)            - 客户侧扁平归档记录（fall_event_archive.json 中一条）
        salt(str)            - 站点别名加盐（ALGO_SOURCE_SALT）
        attach_skeleton(bool) - 是否附加合成骨骼序列（真实事件当前无关键点，见 CLAUDE.md）
    返回: 符合 /api/ingest/events 的事件 schema 的 dict
    """
    source_id = make_source_id(raw.get('device_serial', ''), salt)
    event_id = make_event_id(source_id, raw.get('event_id', ''))
    created_at = raw.get('created_at') or datetime.now(timezone.utc).isoformat()
    ingested_at = datetime.now(timezone.utc).isoformat()

    event = {
        'event_id': event_id,
        'source_id': source_id,
        'status': raw.get('status', 'archived'),
        'created_at': created_at,
        'ingested_at': ingested_at,
        'ground_truth': _derive_ground_truth(raw),
        'detection': {
            'confidence': raw.get('detection_confidence'),
            'latency_ms': raw.get('detection_latency_ms'),
            'video_window_frames': raw.get('video_window_frames'),
            'video_window_duration_s': raw.get('video_window_duration_s'),
        },
        'skeleton_analysis': {
            'touch_ground_part': raw.get('touch_ground_part', 'unknown'),
            'fall_direction': raw.get('fall_direction', 'unknown'),
            'impact_velocity': raw.get('impact_velocity'),
            'body_tilt_angle': raw.get('body_tilt_angle'),
            'center_of_mass_velocity': raw.get('center_of_mass_velocity'),
        },
        'risk': {
            'level': raw.get('risk_level'),
            'level_name': raw.get('risk_level_name'),
            'likely_injury_types': raw.get('likely_injury_types', []),
        },
        'medical_report': {
            'full_text': raw.get('medical_report', ''),
            'recommendation': raw.get('report_recommendation', ''),
        },
        'response': {
            'strategy': raw.get('response_strategy', ''),
            'countdown_seconds': raw.get('countdown_seconds'),
            'voice_confirm_status': raw.get('voice_confirm_status', 'pending'),
        },
        'feedback': _clean_feedback(raw.get('feedback')),
        'context': {
            'scenario_key': raw.get('scenario_key', ''),
            'scenario_name': raw.get('scenario_name', ''),
        },
        'privacy': {
            'anonymized': True,
            'version': ANON_VERSION,
            'fields_removed': list(DROPPED_FIELDS),
            'timeline_summary': _timeline_summary(raw),
            'synthetic_skeleton': False,
        },
    }

    # 可选：附加合成骨骼序列（真实客户归档当前不持久化关键点）
    if attach_skeleton:
        from app.services.skeleton_gen import synthetic_skeleton_for_event
        seq = synthetic_skeleton_for_event(
            touch_ground_part=event['skeleton_analysis']['touch_ground_part'],
            fall_direction=event['skeleton_analysis']['fall_direction'],
            is_fall=event['ground_truth']['is_fall'],
        )
        event['skeleton_sequence'] = seq
        event['privacy']['synthetic_skeleton'] = True

    return event


def _clean_feedback(feedback):
    """反馈字段保留（voice/manual 来源非身份信息），补齐默认键"""
    if not isinstance(feedback, dict):
        return None
    return {
        'is_false_alarm': bool(feedback.get('is_false_alarm')),
        'comment': feedback.get('comment', ''),
        'reported_by': feedback.get('reported_by', ''),
        'reported_at': feedback.get('reported_at', ''),
    }


# ── 校验：入库前最小 schema 检查 ──────────────────────────────────────────
REQUIRED_KEYS = ['event_id', 'source_id', 'created_at', 'ground_truth']
ALLOWED_STATUSES = {'detected', 'analyzed', 'reported', 'inquiring',
                    'archived', 'voice_cancelled', 'false_alarm', 'notified'}


def validate_anonymized_event(event: dict) -> Optional[str]:
    """返回错误信息；合法返回 None"""
    if not isinstance(event, dict):
        return '事件必须是 JSON 对象'
    for key in REQUIRED_KEYS:
        if key not in event:
            return f'缺少必填字段: {key}'
    if not isinstance(event['event_id'], str) or not event['event_id']:
        return 'event_id 非法'
    if not isinstance(event['source_id'], str) or not event['source_id']:
        return 'source_id 非法'
    gt = event.get('ground_truth')
    if not isinstance(gt, dict) or 'is_fall' not in gt:
        return 'ground_truth.is_fall 必填'
    return None


def dumps(event: dict) -> str:
    """序列化为 JSON 字符串（存储用）"""
    return json.dumps(event, ensure_ascii=False)
