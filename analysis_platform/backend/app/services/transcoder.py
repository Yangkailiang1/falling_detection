"""
# 对应文档: CLAUDE.md 七 - 视频转码服务
# 功能: 用 ffmpeg 实时将 C6C 的 H.265 转码为浏览器可播的 H.264/HLS
# 解决问题: C6C 固件锁定 H.265，浏览器不支持解码
"""
import subprocess
import threading
import os
import tempfile
import shutil
import time
from app.services.ezviz_auth import token_manager


class StreamTranscoder:
    """
    H.265 → H.264 实时转码器
    每个设备一个转码进程，通过本地 HLS 对外提供 H.264 流
    """
    _instances = {}  # deviceSerial → {'process': Popen, 'hls_dir': str, 'port': int}
    _lock = threading.Lock()
    _next_port = 5100

    @classmethod
    def get_source_url(cls, device_serial):
        """获取萤石云端 FLV 地址作为转码输入源"""
        import requests
        token = token_manager.get_token()
        resp = requests.post('https://open.ys7.com/api/lapp/v2/live/address/get', data={
            'accessToken': token,
            'deviceSerial': device_serial,
            'protocol': 4,  # FLV
            'expireTime': 7200
        }, timeout=10)
        data = resp.json()
        if data.get('code') != '200':
            raise RuntimeError(f"获取转码源失败: {data.get('msg')}")
        return data['data']['url']

    @classmethod
    def start(cls, device_serial):
        """
        启动转码进程
        返回: {'hls_url': str, 'port': int, 'ready': bool}
        """
        cls._lock.acquire()
        try:
            inst = cls._instances.get(device_serial)

            # 如果正在初始化（另一个线程创建中），等待完成
            if inst and inst.get('_starting'):
                hls_url = inst.get('hls_url', f'/api/transcode/{device_serial}/index.m3u8')
                port = inst.get('port', 0)
                cls._lock.release()
                # 等待另一线程完成（最多30秒）
                for _ in range(30):
                    time.sleep(1)
                    cls._lock.acquire()
                    try:
                        inst = cls._instances.get(device_serial)
                        if inst and not inst.get('_starting'):
                            proc = inst.get('process')
                            if proc and proc.poll() is None:
                                ready = os.path.exists(
                                    os.path.join(inst['hls_dir'], 'index.m3u8'))
                                return {'hls_url': inst.get('hls_url', hls_url),
                                        'port': inst.get('port', port), 'ready': ready}
                            # 已死，清理后跳到重新创建
                            shutil.rmtree(inst.get('hls_dir', ''), ignore_errors=True)
                            del cls._instances[device_serial]
                            break
                    finally:
                        cls._lock.release()
                    time.sleep(0.5)
                # 超时或启动失败，回退到重新创建
                cls._lock.acquire()
                # 上面的循环释放了锁，这里重新获取
                if device_serial in cls._instances:
                    return cls.start(device_serial)  # 递归重试
                # 实例已被清理，继续往下创建

            if inst:
                proc = inst.get('process')
                if proc and proc.poll() is None:
                    # 返回已有实例，包含 ready 状态
                    ready = os.path.exists(os.path.join(inst['hls_dir'], 'index.m3u8'))
                    return {'hls_url': inst['hls_url'], 'port': inst['port'], 'ready': ready}
                # 进程已死，清理旧实例
                shutil.rmtree(inst['hls_dir'], ignore_errors=True)
                del cls._instances[device_serial]

            # 创建临时目录并设置 _starting 标记，防止并发创建
            hls_dir = tempfile.mkdtemp(prefix=f'ezviz_hls_{device_serial}_')
            port = cls._next_port
            cls._next_port += 1
            hls_url = f'/api/transcode/{device_serial}/index.m3u8'
            cls._instances[device_serial] = {
                '_starting': True,
                'hls_dir': hls_dir,
                'port': port,
                'hls_url': hls_url,
            }
        finally:
            cls._lock.release()

        # 获取视频源
        try:
            source_url = cls.get_source_url(device_serial)
        except Exception as e:
            shutil.rmtree(hls_dir, ignore_errors=True)
            with cls._lock:
                cls._instances.pop(device_serial, None)
            raise RuntimeError(f"获取源流地址失败: {e}")

        # ffmpeg 命令: FLV(H.265) → H.264 HLS
        cmd = cls._build_ffmpeg_cmd(source_url, hls_dir)

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        # 启动错误日志读取线程
        def log_errors():
            for line in proc.stderr:
                line_str = line.decode('utf-8', errors='replace').strip()
                if 'error' in line_str.lower()[:50]:
                    print(f"[Transcoder:{device_serial}] {line_str[:200]}")

        err_thread = threading.Thread(target=log_errors, daemon=True)
        err_thread.start()

        # 快速检查 ffmpeg 是否成功启动（不等候完整输出）
        time.sleep(3)
        if proc.poll() is not None:
            # ffmpeg 进程已死，尝试读取错误
            stderr_output = b''
            try:
                stderr_output = proc.stderr.read(2048)
            except Exception:
                pass
            shutil.rmtree(hls_dir, ignore_errors=True)
            err_msg = stderr_output.decode('utf-8', errors='replace')[:300]
            with cls._lock:
                cls._instances.pop(device_serial, None)
            raise RuntimeError(f"ffmpeg 转码进程启动失败: {err_msg}")

        # 检查首段是否已就绪
        m3u8_path = os.path.join(hls_dir, 'index.m3u8')
        ready = False
        if os.path.exists(m3u8_path):
            try:
                with open(m3u8_path, 'r') as f:
                    content = f.read(2048)
                if '#EXTINF' in content:
                    ready = True
                    print(f"[Transcoder] {device_serial} 首段已就绪")
            except Exception:
                pass

        with cls._lock:
            cls._instances[device_serial] = {
                'process': proc,
                'hls_dir': hls_dir,
                'port': port,
                'hls_url': hls_url,
                '_starting': False,
            }

        print(f"[Transcoder] {device_serial} 转码已启动 → {hls_url} (ready={ready})")

        # 启动进程守护线程：ffmpeg 死后自动重启
        cls._start_watchdog(device_serial, hls_dir)

        return {'hls_url': hls_url, 'port': port, 'ready': ready}

    @classmethod
    def _build_ffmpeg_cmd(cls, source_url, hls_dir):
        """构建 ffmpeg 命令行（stream copy 模式，萤石 FLV 已为 H.264）"""
        return [
            'ffmpeg',
            '-probesize', '32',
            '-analyzeduration', '0',
            '-reconnect', '1',
            '-reconnect_at_eof', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', '10',
            '-rw_timeout', '10000000',
            '-i', source_url,
            '-c', 'copy',                     # 直接流复制，无需重新编码
            '-f', 'hls',
            '-hls_time', '2',
            '-hls_list_size', '5',
            '-hls_flags', 'delete_segments+omit_endlist',
            '-hls_segment_filename', os.path.join(hls_dir, 'seg_%d.ts'),
            os.path.join(hls_dir, 'index.m3u8')
        ]

    @classmethod
    def _start_watchdog(cls, device_serial, hls_dir):
        """启动守护线程：监控 ffmpeg 进程，死后自动重启"""

        def watchdog():
            restart_count = 0
            while True:
                with cls._lock:
                    inst = cls._instances.get(device_serial)
                    if not inst:
                        return  # stop() 已清理
                    proc = inst.get('process')
                    if not proc:
                        return

                # 等待进程退出
                exit_code = proc.wait()

                with cls._lock:
                    inst = cls._instances.get(device_serial)
                    if not inst:
                        return
                    if inst.get('_stopping'):
                        return

                restart_count += 1
                print(f"[Transcoder] {device_serial} ffmpeg 退出(exit={exit_code})，"
                      f"第 {restart_count} 次自动重启...")

                # 失败等2秒重试，连续失败等更久
                if exit_code != 0:
                    time.sleep(min(restart_count * 5, 30))

                try:
                    new_source = cls.get_source_url(device_serial)
                    cmd = cls._build_ffmpeg_cmd(new_source, hls_dir)
                    new_proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    with cls._lock:
                        inst = cls._instances.get(device_serial)
                        if inst and not inst.get('_stopping'):
                            inst['process'] = new_proc
                            print(f"[Transcoder] {device_serial} 重启成功 (pid={new_proc.pid})")
                        else:
                            new_proc.terminate()
                            return
                except Exception as e:
                    print(f"[Transcoder] {device_serial} 重启失败: {e}")

        t = threading.Thread(target=watchdog, daemon=True, name=f'watchdog-{device_serial}')
        t.start()

        with cls._lock:
            inst = cls._instances.get(device_serial)
            if inst:
                inst['_watchdog'] = t
                inst['_stopping'] = False

    @classmethod
    def get_hls_path(cls, device_serial):
        """
        获取本地 HLS 文件目录
        优先从内存查找（同进程场景），回退到磁盘扫描（跨进程/重启场景）
        """
        # 1. 优先尝试内存缓存（同进程内启动的转码）
        inst = cls._instances.get(device_serial)
        if inst:
            hls_dir = inst.get('hls_dir')
            if hls_dir and os.path.exists(os.path.join(hls_dir, 'index.m3u8')):
                return hls_dir

        # 2. 回退：从磁盘扫描目录（跨进程、Flask reloader fork等场景）
        import glob
        pattern = os.path.join(
            tempfile.gettempdir(),
            f'ezviz_hls_{device_serial}_*'
        )
        # 按 index.m3u8 修改时间排序，优先返回最新的
        dirs = sorted(
            glob.glob(pattern),
            key=lambda d: os.path.getmtime(os.path.join(d, 'index.m3u8'))
            if os.path.exists(os.path.join(d, 'index.m3u8')) else 0,
            reverse=True
        )
        for d in dirs:
            m3u8_path = os.path.join(d, 'index.m3u8')
            if os.path.exists(m3u8_path):
                # 验证 m3u8 至少有片段引用（不是空的）
                try:
                    with open(m3u8_path, 'r') as f:
                        content = f.read(4096)
                    if '#EXTINF' in content:
                        # 验证 ts 片段也存在
                        # 读取 m3u8 中的 ts 文件名
                        ts_count = content.count('.ts')
                        if ts_count > 0:
                            return d
                except Exception:
                    continue
        return None

    @classmethod
    def status(cls, device_serial=None):
        """查询转码状态（内存 + 磁盘双重检查）"""
        if device_serial:
            # 1. 先检查内存中的活跃进程
            inst = cls._instances.get(device_serial)
            if inst:
                proc = inst.get('process')
                if proc and proc.poll() is None:
                    return {device_serial: True}

            # 2. 回退到磁盘检查
            hls_dir = cls.get_hls_path(device_serial)
            if hls_dir:
                # 磁盘上有 HLS 产物但进程不在内存中（跨进程场景）
                return {device_serial: True, 'note': 'disk_only'}

            return {device_serial: False}

        # 全部状态：合并内存和磁盘
        import glob
        all_status = {}
        # 内存中的活跃设备
        for k, v in cls._instances.items():
            proc = v.get('process')
            all_status[k] = proc is not None and proc.poll() is None
        # 磁盘扫描补充
        pattern = os.path.join(tempfile.gettempdir(), 'ezviz_hls_*')
        for d in sorted(glob.glob(pattern)):
            serial = d.split('ezviz_hls_')[-1].rsplit('_', 1)[0]
            if serial not in all_status:
                if os.path.exists(os.path.join(d, 'index.m3u8')):
                    all_status[serial] = True
        return all_status

    @classmethod
    def stop(cls, device_serial):
        """停止转码进程并清理缓存文件"""
        # 标记停止，防止 watchdog 自动重启
        with cls._lock:
            inst = cls._instances.get(device_serial)
            if inst:
                inst['_stopping'] = True

        # 1. 停止内存中跟踪的进程
        with cls._lock:
            inst = cls._instances.pop(device_serial, None)
        if inst:
            proc = inst.get('process')
            if proc:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            shutil.rmtree(inst['hls_dir'], ignore_errors=True)

        # 2. 清理磁盘上所有该设备的 HLS 目录（跨进程场景）
        import glob
        pattern = os.path.join(
            tempfile.gettempdir(),
            f'ezviz_hls_{device_serial}_*'
        )
        for d in glob.glob(pattern):
            shutil.rmtree(d, ignore_errors=True)

        print(f"[Transcoder] {device_serial} 转码已停止")
