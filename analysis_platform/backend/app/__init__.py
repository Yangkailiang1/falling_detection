"""
# 对应文档: 二.V1.0 - Flask应用工厂
# 功能: 创建Flask应用实例，注册路由、配置CORS，提供应用工厂函数
"""
from flask import Flask
from flask_cors import CORS
from config import Config


# [开发文档 三.3.1 - 后端架构]
# 创建Flask应用工厂函数，便于测试和部署
def create_app(config=None):
    """
    创建并配置Flask应用实例
    参数: config - 可选的配置对象，默认使用Config类
    返回: 配置完成的Flask应用
    """
    app = Flask(__name__)

    if config is None:
        # [开发文档 二.技术栈 - python-dotenv]
        app.config.from_object(Config)
    else:
        app.config.from_object(config)

    # [开发文档 二.技术栈 - Flask-CORS]
    # 允许前端开发服务器跨域请求
    CORS(app, resources={
        r"/api/*": {
            "origins": ["http://localhost:5173", "http://127.0.0.1:5173"],
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization"]
        }
    })

    # [开发文档 三.3.3 - 路由注册]
    from app.routes import register_routes
    register_routes(app)

    # 恢复重启前尚未结束的正式版跌倒问询/通知流程。
    import os as _recovery_os
    if not _recovery_os.getenv("PYTEST_CURRENT_TEST"):
        from app.services.fall_inquiry import recover_inquiries
        recover_inquiries()

    # 注册错误处理器
    from app.utils.exceptions import register_error_handlers
    register_error_handlers(app)

    # [无头语音] 伺服 talk 页面（同源 → 无 CORS）[2026-08-13]
    from pathlib import Path as _Path
    _headless_voice_dir = _Path(__file__).resolve().parents[2] / "headless_voice"

    @app.route("/headless-voice")
    def _headless_voice_page():
        from flask import send_from_directory
        return send_from_directory(str(_headless_voice_dir), "talk.html")

    # [V2.2 实时链路] 自动启动 RTSP 检测（opt-in，延迟后台启动）
    # .env: AUTO_START_CAPTURE=worldpose|kd|worldav（空=关闭）, AUTO_START_CAPTURE_DELAY=10
    import os as _os
    import threading as _threading
    _mode_auto = _os.getenv("AUTO_START_CAPTURE", "").strip().lower()
    if _mode_auto and not _os.getenv("PYTEST_CURRENT_TEST"):
        _delay = float(_os.getenv("AUTO_START_CAPTURE_DELAY", "10"))

        def _boot_capture(_m: str):
            try:
                from app.inference import video_capture
                logger = app.logger if app and app.logger else None
                # worldpose: yolo_gate=False — v2.2 自带内部 YOLO-Pose,
                # 不依赖 Stage-1 门控(跌倒瞬间 YOLO11n 可能漏检躺倒的人而跳过推理) [2026-08-13]
                _yolo_gate = _m not in ("worldpose", "v2", "worldpose_v2", "spatial_v5", "v5", "spatial")
                video_capture.start_capture(mode=_m, yolo_gate=_yolo_gate)
                if logger:
                    logger.info(f"auto-start capture({_m}) OK yolo_gate={_yolo_gate}")
                # 预热 v2.2 模型（懒加载首次有人才触发; 预热避免实测时 ~40s 冷启动）
                if _m in ("worldpose", "v2", "worldpose_v2"):
                    from app.inference import world_av_pipeline

                    def _warm():
                        try:
                            world_av_pipeline._init_worldpose()
                            if logger:
                                logger.info("auto-start warm: WorldPoseV2Runtime ready")
                        except Exception as _e:
                            if logger:
                                logger.error(f"auto-start warm failed: {_e}")

                    _threading.Thread(target=_warm, daemon=True).start()
                elif _m in ("spatial_v5", "v5", "spatial"):
                    from app.inference import world_av_pipeline

                    def _warm_spatial():
                        try:
                            if world_av_pipeline._init_spatial_v5():
                                world_av_pipeline._spatial_v5_runtime.warmup()
                            if logger:
                                logger.info("auto-start warm: SpatialV5Runtime ready")
                        except Exception as _e:
                            if logger:
                                logger.error(f"auto-start Spatial v5 warm failed: {_e}")

                    _threading.Thread(target=_warm_spatial, daemon=True).start()
            except Exception as _exc:
                if app and app.logger:
                    app.logger.error(f"auto-start capture failed: {_exc}")

        _threading.Timer(_delay, _boot_capture, args=(_mode_auto,)).start()

    return app
