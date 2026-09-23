"""
# 对应文档: 三.V1.0 - 后端启动入口
# 功能: Flask应用启动脚本
# 启动方式: python run.py
"""
from app import create_app
from config import Config

# [开发文档 七.7.1 - 后端启动]
# 创建Flask应用实例并启动开发服务器
app = create_app()

if __name__ == '__main__':
    print(f"\n{'='*50}")
    print(f"  萤石平台接入分析平台 - 后端服务")
    print(f"  地址: http://{Config.HOST}:{Config.PORT}")
    print(f"  环境: {Config.FLASK_ENV}")
    print(f"  调试模式: {Config.DEBUG}")
    print(f"{'='*50}\n")
    app.run(
        host=Config.HOST,
        port=Config.PORT,
        debug=Config.DEBUG
    )
