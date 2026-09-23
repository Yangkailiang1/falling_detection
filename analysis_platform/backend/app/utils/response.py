"""
# 对应文档: 二.V1.0 - 统一响应格式
# 功能: 提供标准化的API响应封装，确保前后端数据格式一致
"""
from flask import jsonify


# [开发文档 六.2.2 - 编码规范: API路由命名]
# 所有API响应统一使用以下格式封装
def success_response(data=None, message="操作成功", code=200):
    """
    构建成功响应
    - 对应文档章节: 二.V1.0 - API统一响应格式
    - 参数:
        data: 响应数据体，可选
        message(str): 响应消息，默认"操作成功"
        code(int): HTTP状态码，默认200
    - 返回: Flask Response对象
    """
    body = {
        "success": True,
        "code": code,
        "message": message
    }
    if data is not None:
        body["data"] = data
    return jsonify(body), code


def error_response(message="操作失败", code=500, data=None):
    """
    构建错误响应
    - 对应文档章节: 二.V1.0 - API统一响应格式
    - 参数:
        message(str): 错误消息
        code(int): HTTP状态码，默认500
        data: 附加错误数据，可选
    - 返回: Flask Response对象
    """
    body = {
        "success": False,
        "code": code,
        "message": message
    }
    if data is not None:
        body["data"] = data
    return jsonify(body), code


def paginated_response(items, total, page, page_size):
    """
    构建分页响应
    - 对应文档章节: 二.V2.0 - 设备列表分页API
    - 参数:
        items(list): 当前页数据列表
        total(int): 总条数
        page(int): 当前页码
        page_size(int): 每页条数
    - 返回: Flask Response对象
    """
    return success_response({
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total > 0 else 0
    })
