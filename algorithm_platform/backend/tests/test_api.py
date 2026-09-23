"""
# 算法迭代平台 - API 集成测试
# 功能: 通过 Flask 测试客户端验证主要端点与鉴权
"""
from app.services.demo_generator import generate_demo_events

from config import Config

API_KEY = 'test-only-key'
Config.ALGO_API_KEY = API_KEY


def _auth(**kw):
    kw.setdefault('headers', {})['X-API-Key'] = API_KEY
    return kw


def test_health(client):
    r = client.get('/api/health')
    assert r.status_code == 200
    assert r.json['data']['status'] == 'ok'


def test_ingest_requires_key(client):
    r = client.post('/api/ingest/events', json={'events': []})
    assert r.status_code == 401


def test_ingest_batch_and_dedup(client):
    events = generate_demo_events(count=3, seed=3)
    r = client.post('/api/ingest/events', **_auth(json={'events': events}))
    assert r.status_code == 200
    assert r.json['data']['inserted'] == 3
    # 重传 → 全部去重
    r = client.post('/api/ingest/events', **_auth(json={'events': events}))
    assert r.json['data']['skipped_duplicates'] == 3


def test_ingest_rejects_invalid(client):
    r = client.post('/api/ingest/events', **_auth(json={'events': [{'bad': 1}]}))
    assert r.status_code == 200
    assert r.json['data']['errors'] == 1


def test_seed_demo(client):
    r = client.post('/api/seed/demo', **_auth(json={'count': 10}))
    assert r.status_code == 200
    assert r.json['data']['inserted'] == 10
    # 再次播种同 seed → 去重
    r = client.post('/api/seed/demo', **_auth(json={'count': 10}))
    assert r.json['data']['inserted'] == 0


def test_events_list_and_detail(client):
    client.post('/api/seed/demo', **_auth(json={'count': 5}))
    r = client.get('/api/events?limit=2')
    assert r.status_code == 200
    d = r.json['data']
    assert d['total'] == 5
    assert len(d['items']) == 2
    # 列表不含骨骼
    assert 'skeleton_sequence' not in d['items'][0]
    # 详情含骨骼
    eid = d['items'][0]['event_id']
    r = client.get(f'/api/events/{eid}')
    assert r.status_code == 200
    assert r.json['data']['skeleton_sequence']
    # 404
    assert client.get('/api/events/nonexistent').status_code == 404


def test_stats_and_trend(client):
    client.post('/api/seed/demo', **_auth(json={'count': 10}))
    r = client.get('/api/stats')
    assert r.json['data']['total_events'] == 10
    r = client.get('/api/stats/trend?days=7')
    assert len(r.json['data']) == 7


def test_export_requires_key(client):
    assert client.get('/api/export/events').status_code == 401
    assert client.get('/api/export/training').status_code == 401


def test_export_endpoints(client):
    client.post('/api/seed/demo', **_auth(json={'count': 5}))
    r = client.get('/api/export/events', headers={'X-API-Key': API_KEY})
    assert r.status_code == 200
    assert 'falling_events_anonymized.json' in r.headers.get('Content-Disposition', '')
    r = client.get('/api/export/training?scene_prefix=Home_01', headers={'X-API-Key': API_KEY})
    assert r.status_code == 200
    assert r.mimetype == 'application/zip'
