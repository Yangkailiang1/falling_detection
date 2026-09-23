"""
# 算法迭代平台 - API Key 鉴权装饰器
# 功能: 写入与数据集导出接口需携带 X-API-Key（演示级共享密钥，非安全边界）
"""
from functools import wraps
from flask import request
from config import Config
from app.utils.response import error_response


def require_api_key(f):
    """要求请求头 X-API-Key 与 ALGO_API_KEY 匹配"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        key = request.headers.get('X-API-Key', '')
        if not Config.ALGO_API_KEY:
            return error_response("API Key 未配置", 503)
        if key != Config.ALGO_API_KEY:
            return error_response("无效的 API Key", 401)
        return f(*args, **kwargs)
    return wrapper
