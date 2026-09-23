"""
# 对应文档: 二.V1.0 - 路由注册
# 功能: 集中注册所有Blueprint路由模块，带 /api 版本前缀
"""
from app.routes.health import health_bp


# [开发文档 三.3.3 - 后端API路由设计]
def register_routes(app):
    """
    注册所有路由Blueprint到Flask应用
    - 对应文档章节: 二.V1.0 - Flask应用配置
    - 参数: app(Flask) - Flask应用实例
    - 所有路由自动添加 /api 前缀
    """
    # V1 - 健康检查
    app.register_blueprint(health_bp, url_prefix='/api')

    # V2 - 认证管理 [开发文档 二.V2.0]
    from app.routes.auth import auth_bp
    app.register_blueprint(auth_bp, url_prefix='/api')

    # V2 - 设备管理 [开发文档 二.V2.0]
    from app.routes.devices import devices_bp
    app.register_blueprint(devices_bp, url_prefix='/api')

    # V2 - 直播地址 [开发文档 二.V2.0]
    from app.routes.live import live_bp
    app.register_blueprint(live_bp, url_prefix='/api')

    # V2 - 云台控制 [开发文档 二.V2.0]
    from app.routes.ptz import ptz_bp
    app.register_blueprint(ptz_bp, url_prefix='/api')

    # V2 - 抓拍功能 [开发文档 二.V2.0]
    from app.routes.capture import capture_bp
    app.register_blueprint(capture_bp, url_prefix='/api')

    # V2 - 告警管理 [开发文档 二.V2.0]
    from app.routes.alarms import alarms_bp
    app.register_blueprint(alarms_bp, url_prefix='/api')

    # V2 - DeepSeek对话 [开发文档 二.V2.0]
    from app.routes.chat import chat_bp
    app.register_blueprint(chat_bp, url_prefix='/api')

    # V4 - 仪表盘统计 [开发文档 四.V4.0]
    from app.routes.dashboard import dashboard_bp
    app.register_blueprint(dashboard_bp, url_prefix='/api')

    # 转码服务 — H.265→H.264 实时转码
    from app.routes.transcode import transcode_bp
    app.register_blueprint(transcode_bp, url_prefix='/api')

    # V5 — 跌倒事件全流程闭环 [方案书 §3.1, §8.1, §8.2]
    from app.routes.fall_events import fall_events_bp
    app.register_blueprint(fall_events_bp, url_prefix='/api')

    # V6 — 萤石摄像头原生语音播报 [方案书 §7.2]
    from app.routes.voice import voice_bp
    app.register_blueprint(voice_bp, url_prefix='/api')

    # V7 — 实时跌倒检测模型推理 [方案书 §4.1–§4.4]
    from app.routes.inference import inference_bp
    app.register_blueprint(inference_bp, url_prefix='/api')

    # V7.1 — 萤石云信令 Webhook 消息推送接收 [方案书 §6.1]
    from app.routes.webhook import webhook_bp
    app.register_blueprint(webhook_bp, url_prefix='/api')

    # V7.2 — 管理端通知收件箱 [V7.2 替代萤石APP推送]
    from app.routes.notifications import notifications_bp
    app.register_blueprint(notifications_bp, url_prefix='/api')

    # V8.2 — YOLO 驱动的云台自动追踪
    from app.routes.ptz_tracking import ptz_tracking_bp
    app.register_blueprint(ptz_tracking_bp, url_prefix='/api')

    # 产品化 — 微信小程序家属端 [Phase1]
    from app.routes.mini_program import mini_bp
    app.register_blueprint(mini_bp, url_prefix='/api')

    return app
