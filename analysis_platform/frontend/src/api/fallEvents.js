// 跌倒事件 API 模块
// 对应方案书: §3.1 事后分级干预、§8.2 事件归档
import apiClient from './index'

export const fallEventApi = {
  // 获取可用场景列表
  getScenarios() {
    return apiClient.get('/fall-events/scenarios')
  },

  // 模拟一次跌倒事件（全流程处理）
  simulate(data) {
    return apiClient.post('/fall-events/simulate', data)
  },

  // 快捷模拟（URL指定场景）— 默认生成医疗报告（开发后期全流程走通）
  quickSimulate(scenarioKey, withReport = true) {
    return apiClient.post(`/fall-events/quick/${scenarioKey}?report=${withReport}`)
  },

  // 批量模拟
  batchSimulate(data) {
    return apiClient.post('/fall-events/batch', data)
  },

  // 查询事件列表
  getList(params = {}) {
    return apiClient.get('/fall-events', { params })
  },

  // 查询事件详情
  getDetail(eventId) {
    return apiClient.get(`/fall-events/${eventId}`)
  },

  // 查询事件时间线
  getTimeline(eventId) {
    return apiClient.get(`/fall-events/${eventId}/timeline`)
  },

  // 获取统计信息
  getStats() {
    return apiClient.get('/fall-events/stats')
  },

  // 提交误报反馈
  submitFeedback(eventId, data) {
    return apiClient.post(`/fall-events/${eventId}/feedback`, data)
  },

  // 语音确认状态
  voiceConfirm(eventId, data) {
    return apiClient.post(`/fall-events/${eventId}/voice/confirm`, data)
  },

  // 手动触发通知
  triggerNotify(eventId, data) {
    return apiClient.post(`/fall-events/${eventId}/notify`, data)
  },

  // 重新抓拍现场图片 (旧事件萤石签名 URL 过期后恢复用) [V9.4]
  refreshCapture(eventId) {
    return apiClient.post(`/fall-events/${eventId}/capture/refresh`)
  }
}
