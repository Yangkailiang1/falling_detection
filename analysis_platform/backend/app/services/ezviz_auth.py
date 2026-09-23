"""
# 对应文档: 二.V2.0 - Token管理服务
# 功能: 管理萤石平台AccessToken的获取、缓存与自动刷新
# 参考: 萤石平台/API实测报告.md - Token管理章节
# Token有效期7天，过期前1小时自动刷新
"""
import time
import requests
from config import Config
from app.utils.exceptions import EzvizAPIError


class AccessTokenManager:
    """
    Token管理器 - 单例模式
    - 对应文档章节: 二.V2.0 - Token管理服务
    - 功能: 获取萤石AccessToken，支持缓存和自动刷新
    - 参数: 无(从Config读取appKey和appSecret)
    """
    _instance = None
    _token = None
    _expire_time = 0

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # [开发文档 二.V2.0 - Token获取]
    # 调用萤石: POST /api/lapp/token/get
    def _fetch_token(self):
        """
        从萤石平台获取新的AccessToken
        - 对应文档: 萤石平台/API实测报告 - Token管理
        - 返回: {'accessToken': str, 'expireTime': int} 或 抛出异常
        """
        url = f"{Config.EZS_API_BASE_URL}/api/lapp/token/get"
        try:
            resp = requests.post(url, data={
                'appKey': Config.EZS_APP_KEY,
                'appSecret': Config.EZS_APP_SECRET
            }, timeout=10)
            data = resp.json()
            if data.get('code') != '200':
                raise EzvizAPIError(
                    f"获取Token失败: {data.get('msg', '未知错误')}",
                    ezviz_code=data.get('code')
                )
            result = data['data']
            self._token = result['accessToken']
            # expireTime是毫秒时间戳
            self._expire_time = result['expireTime'] / 1000
            return self._token
        except requests.RequestException as e:
            raise EzvizAPIError(f"Token API网络错误: {str(e)}")

    # [开发文档 二.V2.0 - Token自动刷新]
    # 检查Token是否在过期前1小时内，是则刷新
    def get_token(self):
        """
        获取当前有效的AccessToken，自动处理过期刷新
        - 对应文档章节: 二.V2.0 - Token自动刷新机制
        - 返回: str - 有效的AccessToken
        """
        refresh_before = Config.EZS_TOKEN_REFRESH_BEFORE
        now = time.time()
        if self._token is None or (now + refresh_before) >= self._expire_time:
            self._fetch_token()
        return self._token

    # 强制刷新Token
    def refresh(self):
        """
        强制刷新Token，不计当前状态
        - 对应文档章节: 二.V2.0 - Token管理
        """
        return self._fetch_token()

    # 检查Token是否有效
    def is_valid(self):
        """
        检查当前缓存的Token是否仍然有效
        - 返回: bool
        """
        return self._token is not None and time.time() < self._expire_time

    # 获取Token过期时间
    def get_expire_info(self):
        """
        获取Token过期信息
        - 返回: {'token': str, 'expire_time': int, 'valid_seconds': int}
        """
        if not self._token:
            return None
        return {
            'token_preview': self._token[:20] + '...',
            'expire_time': int(self._expire_time),
            'valid_seconds': max(0, int(self._expire_time - time.time()))
        }


# 全局单例实例
token_manager = AccessTokenManager()


# [开发文档 二.V2.0 - API请求助手]
# 封装带Token的萤石API请求
def ezviz_request(method, path, **kwargs):
    """
    发送带Token认证的萤石API请求
    - 对应文档章节: 二.V2.0 - 统一API请求封装
    - 参数:
        method(str): HTTP方法 (GET/POST)
        path(str): API路径 (如 /api/lapp/device/list)
        **kwargs: 其他requests参数
    - 返回: dict - 萤石API响应数据
    """
    url = f"{Config.EZS_API_BASE_URL}{path}"
    token = token_manager.get_token()

    if 'data' not in kwargs:
        kwargs['data'] = {}
    kwargs['data']['accessToken'] = token
    kwargs.setdefault('timeout', 15)

    try:
        response = requests.request(method, url, **kwargs)
        data = response.json()
        if data.get('code') != '200':
            raise EzvizAPIError(
                f"萤石API错误[{path}]: {data.get('msg', '未知错误')}",
                ezviz_code=data.get('code')
            )
        return data.get('data', data)
    except requests.RequestException as e:
        raise EzvizAPIError(f"萤石API网络错误[{path}]: {str(e)}")
