"""
# 算法迭代平台 - 后端启动入口
# 功能: Flask 应用启动脚本
# 启动方式: cd backend && python3 run.py
"""
from app import create_app
from config import Config

# [CLAUDE.md 启动] 创建 Flask 应用实例
app = create_app()

if __name__ == '__main__':
    print(f"\n{'='*50}")
    print(f"  算法迭代平台 - 后端服务")
    print(f"  地址: http://{Config.HOST}:{Config.PORT}")
    print(f"  环境: {Config.FLASK_ENV}")
    print(f"  调试模式: {Config.DEBUG}")
    print(f"  数据文件: {Config.DB_PATH}")
    print(f"{'='*50}\n")
    app.run(
        host=Config.HOST,
        port=Config.PORT,
        debug=Config.DEBUG
    )
