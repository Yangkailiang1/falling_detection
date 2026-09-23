"""
# 算法迭代平台 - 存储层测试
# 功能: 去重入库 / 筛选分页 / 统计与趋势
"""
from datetime import datetime, timedelta, timezone

from app.services.anonymize import anonymize_record
from app.services.demo_generator import generate_demo_events
from app.models import event_store


def _base_event(event_id='ev_1', source='src_a', is_fall=True, created_at=None):
    return {
        'event_id': event_id,
        'source_id': source,
        'status': 'archived',
        'created_at': created_at or '2026-08-01T10:00:00+00:00',
        'ingested_at': '2026-08-12T00:00:00+00:00',
        'ground_truth': {'is_fall': is_fall, 'label_origin': 'detection'},
        'detection': {'confidence': 0.9, 'latency_ms': 10, 'video_window_frames': 16,
                      'video_window_duration_s': 0.64},
        'skeleton_analysis': {'touch_ground_part': 'head', 'fall_direction': 'forward',
                              'impact_velocity': 5.0, 'body_tilt_angle': 70.0,
                              'center_of_mass_velocity': 1.5},
        'risk': {'level': 'I', 'level_name': '高危', 'likely_injury_types': ['颅脑损伤']},
        'medical_report': {'full_text': '', 'recommendation': ''},
        'response': {'strategy': '', 'countdown_seconds': 10, 'voice_confirm_status': 'pending'},
        'feedback': None,
        'context': {'scenario_key': 'head_forward', 'scenario_name': '头部着地'},
        'privacy': {'anonymized': True, 'version': 1, 'fields_removed': [],
                    'timeline_summary': {'stages': [], 'total_duration_ms': 0}},
    }


def test_insert_dedup(client):
    e1 = _base_event('ev_1', 'src_a')
    inserted, skipped, updated = event_store.insert_many([e1, e1])
    assert (inserted, skipped, updated) == (1, 1, 0)
    # 第二个不同事件
    inserted, skipped, updated = event_store.insert_many([_base_event('ev_2', 'src_b')])
    assert (inserted, skipped, updated) == (1, 0, 0)
    assert event_store.count() == 2


def test_insert_updates_changed_label(client):
    """家属改判真实/误报后重推: 同一 event_id 状态/标签变化 → updated=1, 覆盖旧标签 [2026-08-12]"""
    e = _base_event('ev_upd', 'src_a', is_fall=False)   # 误报
    inserted, _, _ = event_store.insert_many([e])
    assert inserted == 1
    e2 = _base_event('ev_upd', 'src_a', is_fall=True)   # 家属确认真实跌倒
    inserted, skipped, updated = event_store.insert_many([e2])
    assert (inserted, skipped, updated) == (0, 0, 1)
    rows = event_store.list_events({'event_id': 'ev_upd'}, limit=1)
    assert rows[0][0]['ground_truth']['is_fall'] is True
    # 完全相同重推 → skipped (不触发更新)
    inserted, skipped, updated = event_store.insert_many([e2])
    assert (inserted, skipped, updated) == (0, 1, 0)


def test_list_filters(client):
    event_store.insert_many([
        _base_event('ev_1', 'src_a', True),
        _base_event('ev_2', 'src_b', False),
    ])
    items, total = event_store.list_events({'risk_level': 'I'})
    assert total == 2
    items, total = event_store.list_events({'source_id': 'src_b'})
    assert total == 1 and items[0]['event_id'] == 'ev_2'
    items, total = event_store.list_events({'is_fall': False})
    assert total == 1
    # 列表项默认不含骨骼序列
    assert 'skeleton_sequence' not in items[0]


def test_date_filter(client):
    e1 = _base_event('ev_1', created_at='2026-08-01T00:00:00+00:00')
    e2 = _base_event('ev_2', created_at='2026-08-10T00:00:00+00:00')
    event_store.insert_many([e1, e2])
    items, total = event_store.list_events({'date_from': '2026-08-05T00:00:00+00:00'})
    assert total == 1 and items[0]['event_id'] == 'ev_2'


def test_stats_and_trend(client):
    events = generate_demo_events(count=40, seed=1)
    event_store.insert_many(events)
    s = event_store.stats()
    assert s['total_events'] == 40
    assert s['total_sources'] == 3
    assert s['total_with_skeleton'] == 40
    assert s['by_risk_level']['I'] + s['by_risk_level']['II'] + s['by_risk_level']['III'] == 40
    assert 0 < s['false_alarm_rate'] < 1
    t = event_store.trend(days=30)
    assert len(t) == 30
    assert sum(x['total'] for x in t) == 40
    # 每天字段齐全
    assert {'date', 'total', 'falls', 'false_alarms'} == set(t[0].keys())


def test_all_payloads_with_skeleton(client):
    events = generate_demo_events(count=5, seed=2)
    event_store.insert_many(events)
    payloads = event_store.all_payloads()
    assert len(payloads) == 5
    assert all(p.get('skeleton_sequence') for p in payloads)
