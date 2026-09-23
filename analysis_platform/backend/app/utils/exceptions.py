"""
# 对应文档: 二.V1.0 - 异常处理
# 功能: 定义自定义异常类，统一Flask错误处理
"""
from werkzeug.exceptions import HTTPException
from app.utils.response import error_response


class AppError(Exception):
    """应用自定义异常基类"""
    def __init__(self, message="应用异常", code=500):
        self.message = message
        self.code = code
        super().__init__(message)


# [开发文档 二.V2.0 - 萤石API集成错误处理]
class EzvizAPIError(AppError):
    """萤石平台API调用异常
    - 对应文档章节: 二.V2.0 - 错误处理
    - 用于封装萤石平台返回的错误信息
    """
    def __init__(self, message="萤石API调用失败", code=502, ezviz_code=None):
        super().__init__(message, code)
        self.ezviz_code = ezviz_code


# [开发文档 二.V2.0 - Token管理]
class TokenExpiredError(EzvizAPIError):
    """Token过期异常
    - 对应文档章节: 二.V2.0 - Token自动刷新
    """
    def __init__(self):
        super().__init__("AccessToken已过期", code=10002, ezviz_code=10002)


class DeviceNotFoundError(AppError):
    """设备未找到异常
    - 对应文档章节: 二.V3.0 - 设备管理
    """
    def __init__(self, device_serial=""):
        super().__init__(
            f"设备未找到: {device_serial}" if device_serial else "设备未找到",
            code=404
        )


def register_error_handlers(app):
    """注册全局错误处理器
    - 对应文档章节: 二.V1.0 - 全局异常处理
    - 参数: app(Flask) - Flask应用实例
    """
    @app.errorhandler(400)
    def bad_request(e):
        return error_response("请求参数错误", 400)

    @app.errorhandler(404)
    def not_found(e):
        return error_response("资源未找到", 404)

    @app.errorhandler(500)
    def internal_error(e):
        return error_response("服务器内部错误", 500)

    @app.errorhandler(AppError)
    def handle_app_error(e):
        return error_response(e.message, e.code)

    @app.errorhandler(EzvizAPIError)
    def handle_ezviz_error(e):
        return error_response(
            e.message, e.code,
            data={"ezviz_code": e.ezviz_code} if e.ezviz_code else None
        )
