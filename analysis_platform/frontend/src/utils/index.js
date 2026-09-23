// [开发文档 二.V1.0 - 前端工具函数]
// 功能: 通用工具函数集合
// 对应文档章节: 六.文件结构 - utils/

/**
 * 格式化日期时间
 * - 参数: dateStr(string) - ISO日期字符串
 * - 返回: 格式化后的中文字符串，如 "2026-07-27 14:30:00"
 */
export function formatDateTime(dateStr) {
  if (!dateStr) return '-'
  const d = new Date(dateStr)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/**
 * 格式化设备状态
 * - 参数: status(int) - 设备状态码 0=离线, 1=在线
 * - 返回: 状态文本
 */
export function formatDeviceStatus(status) {
  const map = { 0: '离线', 1: '在线', 2: '休眠' }
  return map[status] || '未知'
}

/**
 * 格式化告警类型
 * - 参数: type(int) - 告警类型码
 * - 返回: 告警类型中文描述
 */
export function formatAlarmType(type) {
  const map = {
    10000: '移动侦测', 10001: '人形检测', 10005: '人体感应',
    10006: '越界侦测', 10007: '区域入侵'
  }
  return map[type] || `未知告警(${type})`
}

/**
 * 格式化文件大小
 * - 参数: bytes(number) - 字节数
 * - 返回: 带单位的大小字符串
 */
export function formatFileSize(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let size = bytes
  while (size >= 1024 && i < units.length - 1) {
    size /= 1024
    i++
  }
  return `${size.toFixed(1)} ${units[i]}`
}

/**
 * 防抖函数
 * - 参数: fn(Function) - 目标函数, delay(number) - 延迟毫秒
 * - 返回: 防抖后函数
 */
export function debounce(fn, delay = 300) {
  let timer = null
  return function (...args) {
    clearTimeout(timer)
    timer = setTimeout(() => fn.apply(this, args), delay)
  }
}
