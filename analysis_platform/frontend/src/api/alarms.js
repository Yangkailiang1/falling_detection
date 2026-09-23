// [开发文档: V3 - 告警API模块]
import apiClient from './index'

export function getAlarmList(params = {}) {
  return apiClient.get('/alarms', { params })
}

export function getAlarmStats(days = 7) {
  return apiClient.get('/alarms/stats', { params: { days } })
}
