// 算法迭代平台 - 事件浏览 API
import apiClient from './index'

export const eventsApi = {
  // 事件列表（筛选+分页）
  getList(params) {
    return apiClient.get('/events', { params })
  },
  // 事件详情（含骨骼序列）
  getDetail(eventId) {
    return apiClient.get(`/events/${eventId}`)
  }
}
