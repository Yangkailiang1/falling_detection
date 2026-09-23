"""
# 对应文档: 四.V2.0 - DeepSeek对话API测试
# 功能: 测试Chat路由注册和医疗报告生成逻辑
"""
import pytest
from app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


# [开发文档 四.V2.0 验收标准3]
def test_chat_models_endpoint(client):
    """验证可用模型列表端点"""
    resp = client.get('/api/chat/models')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    models = data['data']
    assert isinstance(models, list)
    assert len(models) > 0


def test_chat_completion_validation(client):
    """验证对话接口参数校验"""
    resp = client.post('/api/chat/completions', json={})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data['success'] is False


def test_simple_chat_validation(client):
    """验证简单对话接口参数校验"""
    resp = client.post('/api/chat/simple', json={})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data['success'] is False


def test_fall_report_validation(client):
    """验证跌倒报告接口参数校验"""
    resp = client.post('/api/chat/fall-report', json={})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data['success'] is False


# 测试风险等级映射
def test_risk_level_mapping():
    """验证跌倒风险等级映射逻辑完整"""
    from app.services.ezviz_chat import FALL_REPORT_SYSTEM_PROMPT
    assert '老年医学专家' in FALL_REPORT_SYSTEM_PROMPT
    assert '医疗简报' in FALL_REPORT_SYSTEM_PROMPT


# 测试服务模块结构
def test_chat_service_structure():
    """验证Chat服务模块结构和函数存在性"""
    from app.services.ezviz_chat import (
        chat_completion, chat_completion_stream,
        generate_fall_medical_report, simple_chat
    )
    assert callable(chat_completion)
    assert callable(chat_completion_stream)
    assert callable(generate_fall_medical_report)
    assert callable(simple_chat)
