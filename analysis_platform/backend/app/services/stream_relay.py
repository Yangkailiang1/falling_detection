"""
# 对应文档: CLAUDE.md 七 - 流代理服务
# 功能: 后端单连接拉取萤石云端流，转推 MJPEG 到前端
# 解决问题: 萤石免费账号并发观看人数限制
# 原理: 后端用1个OpenCV连接读HLS，前端多个浏览器连后端MJPEG，只占1个云端名额
"""
import cv2
import threading
import time
from app.services.ezviz_auth import token_manager, ezviz_request


class StreamRelay:
    """
    流代理管理器
    - 对应文档章节: 萤石平台/API实测报告 - 并发观看限制
    - 为每个设备维护一个OpenCV抓帧实例
    - 对外提供 MJPEG 帧流和单帧获取接口
    """
    _streams = {}  # deviceSerial → {'cap': VideoCapture, 'frame': bytes, 'lock': Lock}
    _lock = threading.Lock()

    @classmethod
    def get_stream_url(cls, device_serial, protocol=4):
        """
        获取萤石云端 FLV 地址（FLV对OpenCV最友好）
        - 参数: device_serial(str)设备序列号, protocol(int)协议默认4=FLV
        - 返回: str - FLV/HLS URL
        """
        token = token_manager.get_token()
        import requests
        resp = requests.post('https://open.ys7.com/api/lapp/v2/live/address/get', data={
            'accessToken': token,
            'deviceSerial': device_serial,
            'protocol': protocol,
            'expireTime': 7200
        }, timeout=10)
        data = resp.json()
        if data.get('code') != '200':
            raise RuntimeError(f"获取流地址失败: {data.get('msg')}")
        return data['data']['url']

    @classmethod
    def start(cls, device_serial):
        """
        启动指定设备的流代理
        - 参数: device_serial(str)设备序列号
        - 已在运行的设备不会重复启动
        """
        with cls._lock:
            if device_serial in cls._streams and cls._streams[device_serial] is not None:
                return  # 已经在运行

            # 占位，capture线程启动后更新
            cls._streams[device_serial] = None

        thread = threading.Thread(
            target=cls._capture_loop,
            args=(device_serial,),
            daemon=True,
            name=f'stream-{device_serial}'
        )
        thread.start()

    @classmethod
    def _capture_loop(cls, device_serial):
        """
        后台线程: 持续从云端抓帧
        - 对应文档: CLAUDE.md 七 - 视频流捕获
        """
        url = None
        cap = None
        retry_count = 0
        max_retries = 5

        while retry_count < max_retries:
            try:
                url = cls.get_stream_url(device_serial, protocol=4)
                cap = cv2.VideoCapture(url)
                if cap.isOpened():
                    break
            except Exception as e:
                print(f"[StreamRelay] 获取流失败 (重试{retry_count+1}/{max_retries}): {e}")
            retry_count += 1
            time.sleep(2)

        if not cap or not cap.isOpened():
            print(f"[StreamRelay] 无法打开设备 {device_serial} 的流")
            with cls._lock:
                cls._streams.pop(device_serial, None)
            return

        with cls._lock:
            cls._streams[device_serial] = cap

        print(f"[StreamRelay] 已启动设备 {device_serial} 的流代理")
        frame_count = 0
        reconnect_interval = 300  # 每300帧重建连接防止过期

        while True:
            try:
                ret, frame = cap.read()
                if not ret:
                    frame_count += 1
                    if frame_count > 30:
                        # 流断开，尝试重连
                        print(f"[StreamRelay] {device_serial} 流断开，尝试重连...")
                        cap.release()
                        url = cls.get_stream_url(device_serial, protocol=4)
                        cap = cv2.VideoCapture(url)
                        frame_count = 0
                    continue

                frame_count += 1
                # 每隔 reconnect_interval 帧重建连接，防止URL过期
                if frame_count >= reconnect_interval:
                    frame_count = 0
                    old_cap = cap
                    try:
                        url = cls.get_stream_url(device_serial, protocol=4)
                        cap = cv2.VideoCapture(url)
                        if cap.isOpened():
                            old_cap.release()
                            with cls._lock:
                                cls._streams[device_serial] = cap
                    except Exception:
                        cap = old_cap  # 回退

                time.sleep(0.03)  # ~30fps
            except Exception as e:
                print(f"[StreamRelay] 抓帧异常: {e}")
                time.sleep(1)

    @classmethod
    def get_frame(cls, device_serial):
        """
        获取指定设备的最新一帧（JPEG格式字节）
        - 参数: device_serial(str)设备序列号
        - 返回: bytes|None - JPEG编码的帧数据
        """
        cap = cls._streams.get(device_serial) if device_serial in cls._streams else None
        if cap is None or isinstance(cap, type(None)):
            return None
        try:
            ret, frame = cap.read()
            if ret:
                _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                return jpeg.tobytes()
        except Exception:
            pass
        return None

    @classmethod
    def stop(cls, device_serial):
        """
        停止指定设备的流代理
        - 参数: device_serial(str)设备序列号
        """
        with cls._lock:
            cap = cls._streams.pop(device_serial, None)
        if cap and not isinstance(cap, type(None)):
            cap.release()
            print(f"[StreamRelay] 已停止设备 {device_serial} 的流代理")

    @classmethod
    def status(cls, device_serial=None):
        """
        查询流代理状态
        - 返回: dict - {device_serial: bool}
        """
        if device_serial:
            cap = cls._streams.get(device_serial)
            return {device_serial: cap is not None and not isinstance(cap, type(None))}
        return {
            k: v is not None and not isinstance(v, type(None))
            for k, v in cls._streams.items()
        }

    @classmethod
    def running_devices(cls):
        """返回正在代理的设备列表"""
        return [k for k, v in cls._streams.items()
                if v is not None and not isinstance(v, type(None))]
