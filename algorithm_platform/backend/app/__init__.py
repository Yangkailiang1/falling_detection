"""
# 算法迭代平台 - Flask 应用工厂
# 功能: 创建 Flask 应用实例，注册路由、配置 CORS
"""
from flask import Flask
from flask_cors import CORS
from config import Config
from app.db import init_db


def create_app(config=None):
    """创建并配置 Flask 应用实例"""
    app = Flask(__name__)

    if config is None:
        app.config.from_object(Config)
    else:
        app.config.from_object(config)

    # 请求体上限（含骨骼序列的大批量上传）
    app.config['MAX_CONTENT_LENGTH'] = Config.MAX_CONTENT_LENGTH

    # CORS：允许前端开发服务器跨域
    CORS(app, resources={
        r"/api/*": {
            "origins": Config.FRONTEND_ORIGINS,
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "X-API-Key"]
        }
    })

    # 初始化 SQLite（幂等）
    init_db()

    # 注册路由
    from app.routes import register_routes
    register_routes(app)

    return app
