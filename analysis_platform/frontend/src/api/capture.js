// [开发文档: V3 - 抓拍API模块]
import apiClient from './index'

export function triggerCapture(deviceSerial, channelNo = 1) {
  return apiClient.post(`/capture/${deviceSerial}`, { channel_no: channelNo })
}

export function batchCapture(deviceSerials) {
  return apiClient.post('/capture/batch', { device_serials: deviceSerials })
}
