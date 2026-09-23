"""
# 算法迭代平台 - SQLite 连接管理
# 功能: 单例连接 + 写锁 + 幂等建表
"""
import sqlite3
import threading
from pathlib import Path
from config import Config

_conn = None
_lock = threading.Lock()


def get_conn():
    """获取全局 sqlite3 连接（进程内单例）"""
    global _conn
    if _conn is None:
        # 确保数据目录存在
        Path(Config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(Config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


def get_write_lock():
    """返回写锁（多线程写保护，镜像 fall_event_archive._lock 惯例）"""
    return _lock


def init_db():
    """幂等初始化：执行 schema.sql 建表"""
    schema = Path(Config.SCHEMA_PATH).read_text(encoding='utf-8')
    conn = get_conn()
    with get_write_lock():
        conn.executescript(schema)
        conn.commit()


def close_db():
    """关闭连接（测试用）"""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None
