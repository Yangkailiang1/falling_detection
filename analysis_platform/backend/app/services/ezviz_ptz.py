"""
# 对应文档: 二.V2.0 - 云台控制服务
# 功能: 控制萤石云台摄像头上/下/左/右转动
# 参考: 萤石平台/API实测报告.md - 云台控制
"""
from app.services.ezviz_auth import ezviz_request


# 云台转动方向映射
# - C6C实测: code 0=上, 1=下, 2=左, 3=右（与文档 1=右,2=下,3=左 不同）
DIRECTION_MAP = {
    'up': 0,
    'right': 3,
    'down': 1,
    'left': 2,
}


# [开发文档 二.V2.0 - 云台开始控制API]
# 调用萤石: POST /api/lapp/device/ptz/start
def start_ptz_move(device_serial, direction, speed=2):
    """
    开始云台转动
    - 对应文档章节: 萤石平台/API实测报告 - 云台控制
    - 参数:
        device_serial(str): 设备序列号
        direction(str): 方向: 'up'/'down'/'left'/'right'
        speed(int): 速度等级(0-5)，默认2
    - 返回: {'success': True, 'direction': str}
    """
    direction_code = DIRECTION_MAP.get(direction.lower())
    if direction_code is None:
        valid = ', '.join(DIRECTION_MAP.keys())
        raise ValueError(f"无效方向: {direction}，有效值: {valid}")

    ezviz_request('POST', '/api/lapp/device/ptz/start', data={
        'deviceSerial': device_serial,
        'direction': direction_code,
        'speed': speed
    })
    return {'success': True, 'direction': direction, 'speed': speed}


# [开发文档 二.V2.0 - 云台停止控制API]
# 调用萤石: POST /api/lapp/device/ptz/stop
def stop_ptz_move(device_serial, channel_no=1):
    """
    停止云台转动（无方向参数，停止所有方向的运动）
    - 对应文档章节: 萤石平台/API实测报告 - 云台控制
    - 参数:
        device_serial(str): 设备序列号
        channel_no(int): 通道号，默认1
    - 返回: {'success': True}
    """
    ezviz_request('POST', '/api/lapp/device/ptz/stop', data={
        'deviceSerial': device_serial,
        'channelNo': channel_no
    })
    return {'success': True}


# [开发文档 二.V2.0 - 云台快捷控制]
# 组合操作：向某个方向转动后自动停止
def quick_ptz_move(device_serial, direction, duration_ms=500, speed=3):
    """
    快捷云台控制：转动指定时间后自动停止
    - 对应文档章节: 二.V3.0 - 云台控制面板
    - 参数:
        device_serial(str): 设备序列号
        direction(str): 方向
        duration_ms(int): 转动持续时间(毫秒)，默认500ms
        speed(int): 速度，默认3
    - 返回: {'success': True, 'duration_ms': int}
    """
    import time
    start_ptz_move(device_serial, direction, speed)
    time.sleep(duration_ms / 1000.0)
    stop_ptz_move(device_serial)
    return {'success': True, 'direction': direction, 'duration_ms': duration_ms}


# [开发文档 二.V2.0 - 获取云台能力]
# 检查设备是否支持云台控制
def get_ptz_ability(device_serial):
    """
    检查设备的云台能力
    - 对应文档章节: 二.V2.0 - 设备能力集
    - 参数:
        device_serial(str): 设备序列号
    - 返回: {'support_ptz': bool, 'support_preset': bool}
    """
    from app.services.ezviz_device import get_device_ability
    ability = get_device_ability(device_serial)

    support_ptz = 'ptz' in str(ability).lower()
    support_preset = 'preset' in str(ability).lower()

    return {
        'support_ptz': support_ptz,
        'support_preset': support_preset
    }
