"""
# 对应文档: 四.V2.0 - 设备API测试
# 功能: 测试设备管理相关接口注册和结构完整性
"""
import pytest
from app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


# [开发文档 四.V2.0 验收标准2]
# 测试设备路由已正确注册
def test_device_list_endpoint(client):
    """验证设备列表端点存在并返回JSON"""
    resp = client.get('/api/devices?page=0&page_size=1')
    assert resp.status_code == 200
    assert resp.content_type == 'application/json'


def test_device_detail_endpoint(client):
    """验证设备详情端点存在"""
    resp = client.get('/api/devices/nonexistent')
    assert resp.content_type == 'application/json'


def test_device_status_endpoint(client):
    """验证设备状态端点存在"""
    resp = client.get('/api/devices/nonexistent/status')
    assert resp.content_type == 'application/json'


def test_device_ability_endpoint(client):
    """验证设备能力集端点存在"""
    resp = client.get('/api/devices/nonexistent/ability')
    assert resp.content_type == 'application/json'


# 测试异常类
def test_device_not_found_exception():
    """验证设备未找到异常信息正确"""
    from app.utils.exceptions import DeviceNotFoundError
    err = DeviceNotFoundError("TEST123")
    assert "TEST123" in str(err)
    assert err.code == 404


# 测试服务模块可导入
def test_service_imports():
    """验证所有服务模块可以正常导入"""
    from app.services.ezviz_device import get_device_list, get_device_info
    from app.services.ezviz_live import get_live_address, get_all_live_addresses
    from app.services.ezviz_ptz import DIRECTION_MAP, start_ptz_move
    from app.services.ezviz_capture import capture_device
    from app.services.ezviz_alarm import get_alarm_list, get_alarm_stats
    assert callable(get_device_list)
    assert callable(get_live_address)
    assert isinstance(DIRECTION_MAP, dict)
    assert callable(capture_device)
    assert callable(get_alarm_list)
