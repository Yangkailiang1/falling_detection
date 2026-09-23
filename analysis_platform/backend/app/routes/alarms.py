"""
# 对应文档: 二.V2.0 - 告警路由
# 功能: 告警消息查询API端点
"""
from flask import Blueprint, request
from app.utils.response import success_response, error_response, paginated_response
from app.services import ezviz_alarm
from app.utils.exceptions import EzvizAPIError

alarms_bp = Blueprint('alarms', __name__)


# [开发文档 三.3.3 - GET /api/alarms]
# 获取告警消息列表（支持筛选）
@alarms_bp.route('/alarms', methods=['GET'])
def get_alarms():
    """
    获取告警消息列表
    - 对应文档章节: 三.3.3 - 告警列表API
    - 参数:
        page(query)页码, page_size(query)每页条数
        alarm_type(query)告警类型, status(query)状态
        start_time(query)开始时间戳, end_time(query)结束时间戳
    - 返回: 分页告警列表
    """
    try:
        page = request.args.get('page', 0, type=int)
        page_size = request.args.get('page_size', 20, type=int)
        alarm_type = request.args.get('alarm_type', None, type=int)
        status = request.args.get('status', None, type=int)
        start_time = request.args.get('start_time', None, type=int)
        end_time = request.args.get('end_time', None, type=int)

        result = ezviz_alarm.get_alarm_list(
            page, page_size, alarm_type=alarm_type,
            start_time=start_time, end_time=end_time, status=status
        )
        return paginated_response(
            result.get('list', []), result.get('total', 0), page, page_size
        )
    except EzvizAPIError as e:
        return error_response(str(e), 502)


# [开发文档 二.V2.0 - 告警统计]
# 获取按天统计的告警数量
@alarms_bp.route('/alarms/stats', methods=['GET'])
def get_alarm_statistics():
    """
    获取告警统计（按天）
    - 对应文档章节: 四.V4.0 - 仪表盘告警趋势图
    - 参数: days(query)统计天数，默认7天
    """
    try:
        days = request.args.get('days', 7, type=int)
        stats = ezviz_alarm.get_alarm_stats(days)
        return success_response(stats)
    except EzvizAPIError as e:
        return error_response(str(e), 502)
