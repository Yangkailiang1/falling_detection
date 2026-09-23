"""
# 算法迭代平台 - 统一响应格式
# 功能: 标准化的 API 响应封装，保证前后端数据格式一致
# 复制自 analysis_platform backend/app/utils/response.py
"""
from flask import jsonify


def success_response(data=None, message="操作成功", code=200):
    """构建成功响应"""
    body = {
        "success": True,
        "code": code,
        "message": message
    }
    if data is not None:
        body["data"] = data
    return jsonify(body), code


def error_response(message="操作失败", code=500, data=None):
    """构建错误响应"""
    body = {
        "success": False,
        "code": code,
        "message": message
    }
    if data is not None:
        body["data"] = data
    return jsonify(body), code


def paginated_response(items, total, page, page_size):
    """构建分页响应"""
    return success_response({
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total > 0 else 0
    })
