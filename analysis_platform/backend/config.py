"""
# 对应文档: 二.V1.0 - 配置管理
# 功能: 加载.env环境变量，提供统一配置入口
# 所有配置项通过Config类属性访问，确保类型安全
"""
import os
from dotenv import load_dotenv

# [开发文档 七.开发环境搭建 - 环境变量加载]
# 加载项目根目录的.env文件
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))


class Config:
    """应用配置类，集中管理所有环境变量"""

    # === Flask配置 [开发文档 二.V1.0 - Flask框架配置] ===
    FLASK_ENV = os.getenv('FLASK_ENV', 'development')
    DEBUG = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    HOST = os.getenv('FLASK_HOST', '127.0.0.1')
    PORT = int(os.getenv('FLASK_PORT', 5000))
    SECRET_KEY = os.getenv('FLASK_SECRET_KEY') or os.urandom(32).hex()

    # === 萤石开放平台凭证 [开发文档 一.1.3 - 萤石平台凭证信息] ===
    EZS_APP_KEY = os.getenv('EZS_APP_KEY', '')
    EZS_APP_SECRET = os.getenv('EZS_APP_SECRET', '')
    EZS_ACCESS_TOKEN = os.getenv('EZS_ACCESS_TOKEN', '')
    EZS_PHONE = os.getenv('EZS_PHONE', '')
    EZS_ACCOUNT_ID = os.getenv('EZS_ACCOUNT_ID', '')

    # === 萤石API地址 [开发文档 三.3.1 - 整体架构] ===
    EZS_API_BASE_URL = os.getenv('EZS_API_BASE_URL', 'https://open.ys7.com')

    # === 萤石大模型配置 (OpenAI兼容) [2026-07-30 新增] ===
    EZS_API_KEY = os.getenv('EZS_API_KEY', '')
    EZS_OPENAI_API_URL = os.getenv('EZS_OPENAI_API_URL', 'https://openai.ezviz.com/v1')
    EZS_CHAT_MODEL = os.getenv('EZS_CHAT_MODEL', 'qwen3.5-flash')

    # === 萤石消息推送 Webhook [V7.2 新增] ===
    # 签名密钥（控制台消息推送服务配置），空=跳过签名验证
    EZS_WEBHOOK_SECRET = os.getenv('EZS_WEBHOOK_SECRET', '')

    # === 讯飞语音听写 (STT) [V7.3 新增] ===
    # 控制台 https://console.xfyun.cn/ 我的应用 → 语音听写（流式版）→ AppID/APIKey/APISecret
    XFYUN_APPID = os.getenv('XFYUN_APPID', '')
    XFYUN_API_KEY = os.getenv('XFYUN_API_KEY', '')
    XFYUN_API_SECRET = os.getenv('XFYUN_API_SECRET', '')

    # 旧接口（accessToken 方式，备用）
    EZS_CHAT_API_URL = os.getenv('EZS_CHAT_API_URL_LEGACY',
        'https://open.ys7.com/api/service/open/ezviz/v1/chat/completions')

    # === 直播流配置 [开发文档 二.V2.0 - 直播地址获取] ===
    EZS_LIVE_PROTOCOL = int(os.getenv('EZS_LIVE_PROTOCOL', '2'))

    # === Token管理 [开发文档 二.V2.0 - Token管理服务] ===
    EZS_TOKEN_REFRESH_BEFORE = int(os.getenv('EZS_TOKEN_REFRESH_BEFORE', '3600'))

    # === 前端配置 [开发文档 三.3.1 - 前后端通信] ===
    VITE_API_BASE_URL = os.getenv('VITE_API_BASE_URL', 'http://localhost:5000')
    VITE_DEV_PORT = int(os.getenv('VITE_DEV_PORT', '5173'))
