"""
# 算法迭代平台 - 脱敏逻辑测试
# 功能: 字段删除 / source_id 确定性 / 真值派生 / 时间线脱敏
"""
import json

from app.services.anonymize import (
    anonymize_record, make_source_id, make_event_id, DROPPED_FIELDS,
    validate_anonymized_event,
)

# 一条客户侧原始归档记录（真实字段全集）
RAW = {
    'event_id': 'b65fc1d5',
    'status': 'voice_cancelled',
    'created_at': '2026-08-03T08:04:27.427455+00:00',
    'detection_confidence': 0.93,
    'detection_latency_ms': 11.7,
    'video_window_frames': 16,
    'video_window_duration_s': 0.64,
    'touch_ground_part': 'head',
    'fall_direction': 'forward',
    'impact_velocity': 5.63,
    'body_tilt_angle': 76.27,
    'center_of_mass_velocity': 1.58,
    'risk_level': 'I',
    'risk_level_name': '高危',
    'likely_injury_types': ['颅脑损伤', '颅内出血'],
    'location': '客厅',
    'device_serial': 'CHANGE_ME_DEVICE_SERIAL',
    'scenario_key': 'head_forward',
    'scenario_name': '头部着地-向前跌倒',
    'description': '老人在客厅行走时被地毯绊倒...',
    'medical_report': '测试报告',
    'report_recommendation': '立即联系家属',
    'response_strategy': '10秒语音问询',
    'countdown_seconds': 10,
    'voice_confirm_status': 'cancelled',
    'notification_status': {'sms_sent': True, 'app_sent': False},
    'capture_pic_url': 'https://opencapture.ys7.com/xxx',
    'capture_time': '2026-08-11T10:33:18+08:00',
    'capture_pic_path': '/data/captures/b65fc1d5.jpg',
    'video_clip': '/data/clips/b65fc1d5.mp4',
    'timeline': [
        {'time': 't1', 'stage': 'detected', 'label': '跌倒检测',
         'detail': 'https://opencapture.ys7.com/xxx', 'status': 'success', 'duration_ms': 0},
        {'time': 't2', 'stage': 'archived', 'label': '归档',
         'detail': '', 'status': 'success', 'duration_ms': 2236},
    ],
    'feedback': {'is_false_alarm': True, 'comment': '我没事', 'reported_by': 'voice',
                 'reported_at': '2026-08-03T08:05:00+00:00'},
}


def test_privacy_fields_removed():
    ev = anonymize_record(dict(RAW), salt='salt')
    blob = json.dumps(ev, ensure_ascii=False)
    # 身份字段的“值”不得出现（设备序列号值、位置值、签名 URL、视频路径、描述内容）
    for value in ['CHANGE_ME_DEVICE_SERIAL', '客厅', 'opencapture.ys7.com', 'captures/b65fc1d5',
                  'clips/b65fc1d5', '老人在客厅行走时被地毯绊倒']:
        assert value not in blob, f'敏感值泄漏: {value}'
    # 审计清单应包含全部删除字段
    assert set(DROPPED_FIELDS) <= set(ev['privacy']['fields_removed'])
    assert ev['privacy']['anonymized'] is True


def test_source_id_deterministic_and_salted():
    s1 = make_source_id('CHANGE_ME_DEVICE_SERIAL', 'saltA')
    s2 = make_source_id('CHANGE_ME_DEVICE_SERIAL', 'saltA')
    s3 = make_source_id('CHANGE_ME_DEVICE_SERIAL', 'saltB')
    assert s1 == s2
    assert s1 != s3
    assert s1.startswith('src_')


def test_event_id_deterministic():
    e1 = make_event_id('src_x', 'b65fc1d5')
    e2 = make_event_id('src_x', 'b65fc1d5')
    e3 = make_event_id('src_y', 'b65fc1d5')
    assert e1 == e2
    assert e1 != e3
    assert e1.startswith('algo_')


def test_ground_truth_derivation():
    # 语音取消 → 误报
    ev = anonymize_record(dict(RAW), salt='s')
    assert ev['ground_truth']['is_fall'] is False
    assert ev['ground_truth']['label_origin'] == 'feedback'
    # 无反馈且状态为 false_alarm
    raw2 = dict(RAW); raw2.pop('feedback'); raw2['status'] = 'false_alarm'
    assert anonymize_record(raw2, salt='s')['ground_truth']['is_fall'] is False
    # 已归档且无取消 → 真实
    raw3 = dict(RAW); raw3.pop('feedback'); raw3['voice_confirm_status'] = 'timeout'
    assert anonymize_record(raw3, salt='s')['ground_truth']['is_fall'] is True


def test_timeline_scrubbed():
    ev = anonymize_record(dict(RAW), salt='s')
    ts = ev['privacy']['timeline_summary']
    assert ts['stages'] == ['detected', 'archived']
    assert ts['total_duration_ms'] == 2236
    # detail 文本不得出现在时间线摘要中
    assert 'opencapture' not in json.dumps(ts)


def test_attach_skeleton_taggessynthetic():
    ev = anonymize_record(dict(RAW), salt='s', attach_skeleton=True)
    assert ev['skeleton_sequence']['n_frames'] > 0
    assert ev['skeleton_sequence']['origin'] == 'synthetic'
    assert ev['privacy']['synthetic_skeleton'] is True
    # 骨骼形状：(T,17,2)
    assert len(ev['skeleton_sequence']['keypoints'][0]) == 17
    assert len(ev['skeleton_sequence']['keypoints'][0][0]) == 2


def test_validate_anonymized_event():
    ev = anonymize_record(dict(RAW), salt='s')
    assert validate_anonymized_event(ev) is None
    assert validate_anonymized_event({}) is not None
    bad = dict(ev); bad.pop('ground_truth')
    assert validate_anonymized_event(bad) is not None
