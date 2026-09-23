// 算法迭代平台 - 仪表盘统计 API
import apiClient from './index'

export const dashboardApi = {
  getStats() {
    return apiClient.get('/stats')
  },
  getTrend(days = 30) {
    return apiClient.get('/stats/trend', { params: { days } })
  }
}
