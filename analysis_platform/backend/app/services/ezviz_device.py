"""
# 对应文档: 二.V2.0 - 设备管理服务
# 功能: 封装萤石设备管理相关API
# 参考: 萤石平台/API实测报告.md - 设备列表/详情/状态
"""
from app.services.ezviz_auth import ezviz_request
from app.utils.exceptions import DeviceNotFoundError


# [开发文档 二.V2.0 - 设备列表API]
# 调用萤石: POST /api/lapp/device/list
def get_device_list(page_start=0, page_size=20):
    """
    获取账号下的设备列表（分页）
    - 对应文档章节: 萤石平台/API实测报告 - 设备列表
    - 参数:
        page_start(int): 起始页码，从0开始
        page_size(int): 每页数量
    - 返回: {'list': [...], 'total': int, 'page': int}
    """
    data = ezviz_request('POST', '/api/lapp/device/list', data={
        'pageStart': page_start,
        'pageSize': page_size
    })
    # 如果data是列表，说明没有分页信息
    if isinstance(data, list):
        return {'list': data, 'total': len(data), 'page': 0}
    return {
        'list': data.get('deviceList', data.get('list', [])),
        'total': data.get('total', 0),
        'page': data.get('page', page_start)
    }


# [开发文档 二.V2.0 - 设备详情API]
# 调用萤石: POST /api/lapp/device/info
def get_device_info(device_serial):
    """
    获取指定设备详细信息
    - 对应文档章节: 萤石平台/API实测报告 - 设备详细信息
    - 参数:
        device_serial(str): 设备序列号
    - 返回: dict - 设备信息 {'deviceSerial': ..., 'deviceName': ..., 'status': ...}
    """
    try:
        data = ezviz_request('POST', '/api/lapp/device/info', data={
            'deviceSerial': device_serial
        })
        return data
    except Exception:
        # 降级：从设备列表中查找
        devices = get_device_list(0, 100)
        for d in devices.get('list', []):
            if d.get('deviceSerial') == device_serial:
                return d
        raise DeviceNotFoundError(device_serial)


# [开发文档 二.V2.0 - 设备状态查询API]
# 调用萤石: POST /api/lapp/device/status/get
def get_device_status(device_serial):
    """
    获取设备在线状态
    - 对应文档章节: 萤石平台/API实测报告 - 设备状态查询
    - 参数:
        device_serial(str): 设备序列号
    - 返回: {'status': int, 'status_name': str}
    """
    # 从设备详情中获取状态
    info = get_device_info(device_serial)
    status = info.get('status', 0)
    status_map = {0: '离线', 1: '在线', 2: '休眠'}
    return {
        'status': status,
        'status_name': status_map.get(status, '未知')
    }


# [开发文档 二.V2.0 - 设备能力集API]
# 调用萤石: POST /api/lapp/device/ability/get
def get_device_ability(device_serial):
    """
    获取设备能力集（支持的云台、告警等功能）
    - 对应文档章节: 萤石平台/API实测报告 - 设备能力集
    - 参数:
        device_serial(str): 设备序列号
    - 返回: dict - 能力集信息
    """
    return ezviz_request('POST', '/api/lapp/device/ability/get', data={
        'deviceSerial': device_serial
    })
