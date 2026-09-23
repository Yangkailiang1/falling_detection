// [开发文档: V3 - 直播API模块]
// 功能: 直播地址获取相关API
// 对应文档章节: 三.3.3 - 直播相关API
import apiClient from './index'

/**
 * 获取设备直播地址
 * - 对应文档: 三.3.3 GET /api/live/:deviceSerial/address
 * - 参数: deviceSerial, protocol(2=HLS,3=RTMP,4=FLV)
 */
export function getLiveAddress(deviceSerial, protocol = 2) {
  return apiClient.get(`/live/${deviceSerial}/address`, { params: { protocol } })
}

/**
 * 获取设备所有协议直播地址
 * - 对应文档: 二.V2.0 - 多协议直播
 */
export function getAllLiveAddresses(deviceSerial) {
  return apiClient.get(`/live/${deviceSerial}/channels`)
}

/**
 * 获取直播通道列表
 * - 对应文档: 二.V2.0 - 直播通道查询
 */
export function getLiveChannels(deviceSerial = null) {
  const params = {}
  if (deviceSerial) params.deviceSerial = deviceSerial
  return apiClient.get('/live/channels', { params })
}
