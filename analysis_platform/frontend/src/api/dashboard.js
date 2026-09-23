// [开发文档: V4 - 仪表盘API]
// 功能: 系统统计相关API
import apiClient from './index'

export function getDashboardStats() {
  return apiClient.get('/dashboard/stats')
}

export function getAlarmStats(days = 7) {
  return apiClient.get('/alarms/stats', { params: { days } })
}
