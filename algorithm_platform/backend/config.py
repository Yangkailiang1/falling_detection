"""
# 对应文档: CLAUDE.md - 环境变量配置
# 功能: 加载 .env 环境变量，提供统一配置入口
# 所有配置项通过 Config 类属性访问，确保类型安全
"""
import os
import secrets
from pathlib import Path
from dotenv import load_dotenv

# [CLAUDE.md 启动] 加载项目根目录的 .env 文件（后端目录的上一级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / '.env')


class Config:
    """应用配置类，集中管理所有环境变量"""

    # === Flask 配置 ===
    FLASK_ENV = os.getenv('FLASK_ENV', 'development')
    DEBUG = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    HOST = os.getenv('FLASK_HOST', '127.0.0.1')
    PORT = int(os.getenv('FLASK_PORT', 5003))
    SECRET_KEY = os.getenv('FLASK_SECRET_KEY') or secrets.token_hex(32)
    # 写入/导出接口的共享 API Key（演示级，非安全边界）
    ALGO_API_KEY = os.getenv('ALGO_API_KEY') or secrets.token_urlsafe(32)
    # 脱敏时 source_id 的确定性加盐（同一部署内映射稳定）
    ALGO_SOURCE_SALT = os.getenv('ALGO_SOURCE_SALT') or secrets.token_hex(32)
    # 前端开发服务器地址（CORS）
    FRONTEND_ORIGINS = [
        'http://localhost:5174',
        'http://127.0.0.1:5174',
    ]

    # === 存储 ===
    # SQLite 数据库文件（运行时生成，gitignore）→ backend/data/
    DATA_DIR = _BACKEND_DIR / 'data'
    DB_PATH = os.getenv('ALGO_DB_PATH', str(DATA_DIR / 'algorithm_platform.db'))
    SCHEMA_PATH = os.path.join(os.path.dirname(__file__), 'app', 'schema.sql')

    # === 上传限制 ===
    # 单次批量上传事件上限 / 请求体上限
    MAX_BATCH_EVENTS = int(os.getenv('ALGO_MAX_BATCH_EVENTS', 500))
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB（含骨骼序列的大批量）
