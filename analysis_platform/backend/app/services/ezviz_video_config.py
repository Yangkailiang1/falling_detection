"""
# 功能: 设备视频编码参数配置服务
# 调用萤石: POST /api/lapp/device/video/encode/set
# 参考: 萤石开放平台API文档 - 设备视频编码配置
"""
from app.services.ezviz_auth import ezviz_request


# 分辨率码表
RESOLUTION_MAP = {
    '720p': 19,
    '1080p': 27,
}

# 帧率码表
FRAMERATE_MAP = {
    'full': 0,    # 全帧率
    2: 6,
    4: 7,
    6: 8,
    8: 9,
    10: 10,
    12: 11,
    15: 14,
    16: 12,
    18: 15,
    20: 13,
    22: 16,
}

# 码率上限码表（单位 Kbps）
BITRATE_MAP = {
    256: 9,
    384: 11,
    512: 13,
    768: 15,
    1024: 17,
    1536: 19,
    2048: 21,
}


def set_video_encode(device_serial, stream_type_in=2, resolution='720p',
                     frame_rate=20, bit_rate=1024, encode_type=1,
                     channel_no=1):
    """
    设置设备视频编码参数（主码流/子码流的分辨率、帧率、码率、编码类型）

    参数:
        device_serial(str): 设备序列号
        stream_type_in(int): 码流类型 1=主码流, 2=子码流
        resolution(str|int): 分辨率 '720p'|'1080p' 或数字码值
        frame_rate(int|str): 帧率 2|4|6|8|10|12|15|16|18|20|22 或 'full'
        bit_rate(int): 码率上限(Kbps) 256|384|512|768|1024|1536|2048
        encode_type(int): 编码类型 1=H.264, 2=H.265
        channel_no(int|str): 通道号，默认1

    返回: dict - 萤石API原始响应
    """
    # 解析分辨率
    if isinstance(resolution, str):
        res_code = RESOLUTION_MAP.get(resolution, 19)
    else:
        res_code = resolution

    # 解析帧率
    if isinstance(frame_rate, str):
        fr_code = FRAMERATE_MAP.get(frame_rate, 10)
    else:
        fr_code = FRAMERATE_MAP.get(frame_rate, 10)

    # 解析码率
    br_code = BITRATE_MAP.get(bit_rate, 17)  # 默认1024K

    data = ezviz_request('POST', '/api/lapp/device/video/encode/set', data={
        'deviceSerial': device_serial,
        'channelNo': str(channel_no),
        'streamTypeIn': str(stream_type_in),
        'resolution': str(res_code),
        'videoFrameRate': str(fr_code),
        'videoBitRate': str(br_code),
        'encodeType': str(encode_type),
        'picQuality': '2',           # 图像质量中等
        'bitRateType': '1',          # 定码率
        'encodeComplex': '2',        # 高编码复杂度(画质优先)
        'intervalFrameI': '50',      # I帧间隔
    })
    return data


def set_substream_fall_detection(device_serial):
    """
    为跌倒检测场景优化子码流：720p@20fps H.264

    比默认的 512x288@2fps 更适合人体动作识别，
    同时保持 H.264 编码兼容主流推理框架。
    """
    return set_video_encode(
        device_serial=device_serial,
        stream_type_in=2,       # 子码流
        resolution='720p',      # 1280x720
        frame_rate=20,           # 20fps
        bit_rate=1024,           # 1Mbps
        encode_type=1,           # H.264
    )


def get_current_config(device_serial):
    """
    获取设备当前视频编码配置
    注意：萤石API可能不直接提供查询接口，
    此方法尝试通过设备能力集推断当前配置
    """
    from app.services.ezviz_device import get_device_ability
    return get_device_ability(device_serial)
