"""
# 对应文档: 二.V2.0 - 直播服务
# 功能: 获取萤石设备直播流地址，支持HLS/RTMP/FLV协议
# 参考: 萤石平台/API实测报告.md - 直播地址获取
"""
from app.services.ezviz_auth import ezviz_request
from config import Config


# [开发文档 二.V2.0 - 直播地址获取API]
# 调用萤石: POST /api/lapp/v2/live/address/get
def get_live_address(device_serial, protocol=None, quality=2, support_h265=0):
    """
    获取设备直播流地址（默认子码流H264）
    - 对应文档章节: 萤石平台/API实测报告 - 直播地址获取
    - 参数:
        device_serial(str): 设备序列号
        protocol(int|None): 协议: 2=HLS,3=RTMP,4=FLV
        quality(int): 1=高清(H265主码流), 2=流畅(H264子码流)
        support_h265(int): 播放端是否支持H265, 0=不支持(要求H264)
    - C6C主码流为H265浏览器无法播放，改用子码流H264
    """
    if protocol is None:
        protocol = Config.EZS_LIVE_PROTOCOL

    protocol_names = {2: 'HLS', 3: 'RTMP', 4: 'FLV'}
    data = ezviz_request('POST', '/api/lapp/v2/live/address/get', data={
        'deviceSerial': device_serial,
        'protocol': protocol,
        'quality': quality,
        'supportH265': support_h265
    })
    return {
        'address': data.get('url', ''),
        'protocol': protocol,
        'protocol_name': protocol_names.get(protocol, '未知'),
        'expire_time': data.get('expireTime'),
        'device_serial': device_serial
    }


# [开发文档 二.V2.0 - 多协议直播地址]
# 一次性获取设备所有协议的直播地址
def get_all_live_addresses(device_serial):
    """
    获取设备所有可用协议的直播地址
    - 对应文档章节: 二.V2.0 - 直播地址获取（支持多协议）
    - 参数:
        device_serial(str): 设备序列号
    - 返回: [{'address': str, 'protocol': int, 'protocol_name': str}, ...]
    """
    results = []
    for protocol in [2, 3, 4]:  # HLS, RTMP, FLV
        try:
            result = get_live_address(device_serial, protocol)
            results.append(result)
        except Exception:
            continue
    return results


# [开发文档 二.V2.0 - 直播通道查询]
# 调用萤石: POST /api/lapp/live/v2/list
def get_live_channels(device_serial=None):
    """
    获取直播通道列表
    - 对应文档章节: 二.V2.0 - 直播地址获取
    - 参数:
        device_serial(str|None): 可选，指定设备序列号
    - 返回: channels列表
    """
    req_data = {}
    if device_serial:
        req_data['deviceSerial'] = device_serial
    data = ezviz_request('POST', '/api/lapp/live/v2/list', data=req_data)
    if isinstance(data, list):
        return data
    return data.get('list', data.get('channels', []))
