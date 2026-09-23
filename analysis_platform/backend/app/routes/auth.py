"""
# 对应文档: 二.V2.0 - 认证路由
# 功能: Token管理API端点
"""
from flask import Blueprint
from app.utils.response import success_response, error_response
from app.services.ezviz_auth import token_manager
from app.inference.inference_config import DEVICE_SERIAL

auth_bp = Blueprint('auth', __name__)


# [开发文档 三.3.3 - POST /api/auth/token]
# 获取/刷新萤石AccessToken
@auth_bp.route('/auth/token', methods=['POST'])
def get_token():
    """
    获取当前的萤石AccessToken信息
    - 对应文档章节: 二.V2.0 - Token管理
    - 返回: Token预览和过期信息
    """
    try:
        token_manager.refresh()  # 强制刷新
        info = token_manager.get_expire_info()
        return success_response(info)
    except Exception as e:
        return error_response(f"获取Token失败: {str(e)}", 502)


# 检查Token是否有效
@auth_bp.route('/auth/token/check', methods=['GET'])
def check_token():
    """
    检查当前Token是否有效
    - 对应文档章节: 二.V2.0 - Token状态检查
    """
    return success_response({
        'valid': token_manager.is_valid(),
        'info': token_manager.get_expire_info()
    })


# 获取当前 accessToken 值（供 EZUIKit SDK 使用）
@auth_bp.route('/auth/token/ezuikit', methods=['GET'])
def get_ezuikit_token():
    """
    返回当前 accessToken 给 EZUIKit SDK 使用
    - 返回 { accessToken, deviceSerial: str }
    """
    try:
        token_manager.refresh()
        return success_response({
            'accessToken': token_manager.get_token(),
            'deviceSerial': DEVICE_SERIAL,
        })
    except Exception as e:
        return error_response(f"获取Token失败: {str(e)}", 502)
