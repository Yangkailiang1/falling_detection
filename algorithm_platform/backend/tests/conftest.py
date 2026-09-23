"""
# 算法迭代平台 - pytest 共享 fixture
# 功能: 使用临时 SQLite 数据库，测试间隔离
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

# 让 backend/ 可作为包导入（test 从 backend/ 目录运行时已有此路径）
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture()
def client():
    """隔离的 Flask 测试客户端（每次新临时 DB）"""
    from config import Config
    import app.db as db

    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    old_path = Config.DB_PATH
    Config.DB_PATH = tmp.name

    # 重置全局连接，保证用新 DB
    db.close_db()

    from app import create_app
    app = create_app()
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c

    db.close_db()
    Config.DB_PATH = old_path
    try:
        os.unlink(tmp.name)
        os.unlink(tmp.name + '-wal')
        os.unlink(tmp.name + '-shm')
    except OSError:
        pass
