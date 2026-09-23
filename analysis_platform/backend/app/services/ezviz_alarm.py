"""
# 对应文档: 二.V2.0 - 告警服务
# 功能: 查询设备告警消息列表
# 参考: 萤石平台/API实测报告.md - 告警消息列表
"""
from app.services.ezviz_auth import ezviz_request
from datetime import datetime, timedelta


# [开发文档 二.V2.0 - 告警消息列表API]
# 调用萤石: POST /api/lapp/alarm/list
def get_alarm_list(page_start=0, page_size=20, alarm_type=None,
                   start_time=None, end_time=None, status=None):
    """
    获取告警消息列表（支持多种筛选条件）
    - 对应文档章节: 萤石平台/API实测报告 - 告警消息列表
    - 参数:
        page_start(int): 起始页码
        page_size(int): 每页数量
        alarm_type(int|None): 告警类型: 10000=移动侦测, 10001=人形检测等
        start_time(int|None): 开始时间戳(秒)
        end_time(int|None): 结束时间戳(秒)
        status(int|None): 告警状态: 1=已读, 2=未读
    - 返回: {'list': [...], 'total': int, 'page': int}
    """
    req_data = {
        'pageStart': page_start,
        'pageSize': page_size
    }
    if alarm_type is not None:
        req_data['alarmType'] = alarm_type
    if start_time is not None:
        req_data['startTime'] = start_time
    if end_time is not None:
        req_data['endTime'] = end_time
    if status is not None:
        req_data['status'] = status

    data = ezviz_request('POST', '/api/lapp/alarm/list', data=req_data)
    if isinstance(data, list):
        return {'list': data, 'total': len(data), 'page': 0}
    return {
        'list': data.get('alarmList', data.get('list', [])),
        'total': data.get('total', 0),
        'page': data.get('page', page_start)
    }


# [开发文档 二.V2.0 - 按时间范围查询告警]
# 便捷方法：按天数查询告警
def get_alarms_by_days(days=7, alarm_type=None):
    """
    查询最近N天的告警列表
    - 对应文档章节: 二.V3.0 - 告警中心页面
    - 参数:
        days(int): 天数，默认7天
        alarm_type(int|None): 可选告警类型过滤
    - 返回: 告警列表数据
    """
    end_time = int(datetime.now().timestamp())
    start_time = int((datetime.now() - timedelta(days=days)).timestamp())
    return get_alarm_list(
        page_start=0, page_size=500,
        start_time=start_time, end_time=end_time,
        alarm_type=alarm_type
    )


# [开发文档 二.V2.0 - 告警统计]
# 按日期统计告警数量（用于仪表盘图表）
def get_alarm_stats(days=7):
    """
    按天统计最近N天的告警数量
    - 对应文档章节: 四.V4.0 - 仪表盘告警趋势图
    - 参数:
        days(int): 统计天数
    - 返回: [{'date': str, 'count': int}, ...]
    """
    alarms = get_alarms_by_days(days)
    stats = {}
    for alarm in alarms.get('list', []):
        alarm_time = alarm.get('alarmTime', alarm.get('alarm_time', 0))
        if isinstance(alarm_time, str):
            date = alarm_time[:10]
        else:
            date = datetime.fromtimestamp(alarm_time / 1000).strftime('%Y-%m-%d')
        stats[date] = stats.get(date, 0) + 1

    result = []
    for i in range(days - 1, -1, -1):
        date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        result.append({'date': date, 'count': stats.get(date, 0)})
    return result
