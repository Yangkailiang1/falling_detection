// [开发文档: V3 - 设备API模块]
// 功能: 封装设备管理相关API调用
// 对应文档章节: 三.3.3 - 设备相关API
import apiClient from './index'

/**
 * 获取设备列表（分页）
 * - 对应文档: 三.3.3 GET /api/devices
 * - 参数: page, page_size
 */
export function getDeviceList(page = 0, pageSize = 20) {
  return apiClient.get('/devices', { params: { page, page_size: pageSize } })
}

/**
 * 获取设备详情
 * - 对应文档: 三.3.3 GET /api/devices/:deviceSerial
 */
export function getDeviceDetail(deviceSerial) {
  return apiClient.get(`/devices/${deviceSerial}`)
}

/**
 * 获取设备在线状态
 * - 对应文档: 三.3.3 GET /api/devices/:deviceSerial/status
 */
export function getDeviceStatus(deviceSerial) {
  return apiClient.get(`/devices/${deviceSerial}/status`)
}

/**
 * 获取设备能力集
 * - 对应文档: 二.V2.0 - 设备能力集
 */
export function getDeviceAbility(deviceSerial) {
  return apiClient.get(`/devices/${deviceSerial}/ability`)
}
