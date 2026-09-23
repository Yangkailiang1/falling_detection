// [开发文档: V3 - 云台控制API模块]
// 功能: 云台控制相关API
// 对应文档章节: 三.3.3 - 云台控制API
import apiClient from './index'

export function startPtz(deviceSerial, direction, speed = 2) {
  return apiClient.post(`/ptz/${deviceSerial}/start`, { direction, speed })
}

export function stopPtz(deviceSerial) {
  return apiClient.post(`/ptz/${deviceSerial}/stop`)
}

export function quickPtz(deviceSerial, direction, durationMs = 500, speed = 3) {
  return apiClient.post(`/ptz/${deviceSerial}/quick`, { direction, duration_ms: durationMs, speed })
}

export function getPtzAbility(deviceSerial) {
  return apiClient.get(`/ptz/${deviceSerial}/ability`)
}
