"""
# 对应文档: 二.V2.0 - 抓拍服务
# 功能: 远程触发摄像头抓拍并获取图片
# 参考: 萤石平台/API实测报告.md - 设备抓拍
"""
from app.services.ezviz_auth import ezviz_request


# [开发文档 二.V2.0 - 设备抓拍API]
# 调用萤石: POST /api/lapp/device/capture
def capture_device(device_serial, channel_no=1):
    """
    触发设备拍摄一张图片
    - 对应文档章节: 萤石平台/API实测报告 - 设备抓拍
    - 参数:
        device_serial(str): 设备序列号
        channel_no(int): 通道号，默认1
    - 返回: {'pic_url': str, 'device_serial': str, 'capture_time': int}
    """
    data = ezviz_request('POST', '/api/lapp/device/capture', data={
        'deviceSerial': device_serial,
        'channelNo': channel_no
    })
    return {
        'pic_url': data.get('picUrl', ''),
        'device_serial': device_serial,
        'channel_no': channel_no,
        'capture_time': data.get('captureTime', 0)
    }


# [开发文档 二.V2.0 - 批量抓拍]
# 对多个设备依次抓拍
def batch_capture(device_serials):
    """
    批量对多个设备触发抓拍
    - 对应文档章节: 二.V3.0 - 抓拍管理页面
    - 参数:
        device_serials(list[str]): 设备序列号列表
    - 返回: [{'success': bool, 'device_serial': str, 'result': ...}, ...]
    """
    results = []
    for serial in device_serials:
        try:
            result = capture_device(serial)
            results.append({'success': True, 'device_serial': serial, 'result': result})
        except Exception as e:
            results.append({'success': False, 'device_serial': serial, 'error': str(e)})
    return results
