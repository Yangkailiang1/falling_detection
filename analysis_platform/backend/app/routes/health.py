"""
# 对应文档: 二.V1.0 - 健康检查接口
# 功能: 提供系统健康检查端点，用于监控服务状态
"""
from flask import Blueprint
from app.utils.response import success_response
from config import Config
import time

health_bp = Blueprint('health', __name__)

# 服务启动时间，用于计算运行时长
START_TIME = time.time()


# [开发文档 四.V1.0 - /api/health]
# 功能: 检查后端服务是否正常运行
# 参数: 无
# 返回: {'status': 'ok', 'uptime': 运行秒数, 'version': '1.0.0'}
@health_bp.route('/health', methods=['GET'])
def health_check():
    """系统健康检查端点
    验收标准: 返回status='ok'即表示服务正常
    """
    uptime = int(time.time() - START_TIME)
    return success_response({
        "status": "ok",
        "uptime": uptime,
        "version": "1.0.0",
        "service": "萤石平台接入分析平台",
        "ezviz_api_configured": bool(Config.EZS_APP_KEY),
    })
