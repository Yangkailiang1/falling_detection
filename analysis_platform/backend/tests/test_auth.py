"""
# 对应文档: 四.V2.0 - 认证API测试
# 功能: 测试Token管理相关接口
"""
import pytest
from app import create_app


@pytest.fixture
def client():
    """创建测试客户端"""
    app = create_app()
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


# [开发文档 四.V2.0 验收标准1]
# 测试Token状态检查接口是否可用（不实际调用萤石API）
def test_token_check_endpoint(client):
    """验证Token检查端点存在并返回JSON"""
    response = client.get('/api/auth/token/check')
    assert response.status_code == 200
    data = response.get_json()
    assert data['success'] is True
    assert 'valid' in data['data']


# 测试获取新Token的接口结构
def test_token_refresh_endpoint(client):
    """验证Token刷新端点存在"""
    response = client.post('/api/auth/token')
    # 可能因为网络原因失败但至少要返回JSON
    assert response.content_type == 'application/json'
    assert 'success' in response.get_json()


# 测试Token管理器单例模式
def test_token_manager_singleton():
    """验证AccessTokenManager是单例"""
    from app.services.ezviz_auth import AccessTokenManager
    a = AccessTokenManager()
    b = AccessTokenManager()
    assert a is b


# 测试PTZ方向映射
def test_ptz_direction_map():
    """验证云台方向映射正确"""
    from app.services.ezviz_ptz import DIRECTION_MAP
    assert DIRECTION_MAP['up'] == 0
    assert DIRECTION_MAP['right'] == 1
    assert DIRECTION_MAP['down'] == 2
    assert DIRECTION_MAP['left'] == 3
    assert 'invalid' not in DIRECTION_MAP
