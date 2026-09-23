"""
# 对应文档: 四.V1.0 - 验收标准1: /api/health返回正常
# 功能: 测试健康检查接口是否正常工作
"""
import pytest
from app import create_app


@pytest.fixture
def client():
    """创建测试客户端
    - 对应文档章节: 四.V5.0 - 后端单元测试
    """
    app = create_app()
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


# [开发文档 四.V1.0 验收标准1]
# 测试: GET /api/health 返回 {"status": "ok"}
def test_health_check_returns_ok(client):
    """验收标准: 健康检查接口返回status=ok"""
    response = client.get('/api/health')
    assert response.status_code == 200
    data = response.get_json()
    assert data['success'] is True
    assert data['data']['status'] == 'ok'
    assert 'uptime' in data['data']
    assert 'version' in data['data']


# 测试: 健康检查返回JSON格式
def test_health_check_content_type(client):
    """验收标准: 健康检查返回Content-Type为application/json"""
    response = client.get('/api/health')
    assert response.status_code == 200
    assert 'application/json' in response.content_type


# 测试: 404路由返回错误响应
def test_not_found_route(client):
    """验收标准: 未定义路由返回404错误统一格式"""
    response = client.get('/api/nonexistent')
    assert response.status_code == 404
    data = response.get_json()
    assert data['success'] is False
