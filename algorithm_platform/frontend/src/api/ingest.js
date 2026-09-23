// 算法迭代平台 - 数据接入 / 播种 / 导出 API
import apiClient from './index'
import { ElMessage } from 'element-plus'

export const ingestApi = {
  // 采集接口信息（端点/key/示例命令）
  getInfo() {
    return apiClient.get('/ingest/info')
  },
  // 批量接收脱敏事件
  ingest(events, sourceId, batchId) {
    return apiClient.post('/ingest/events',
      { source_id: sourceId, batch_id: batchId, events },
      { headers: { 'X-API-Key': localStorage.getItem('algo_api_key') || '' } })
  },
  // 生成演示数据
  seedDemo(count = 40, force = false) {
    return apiClient.post('/seed/demo', { count, force },
      { headers: { 'X-API-Key': localStorage.getItem('algo_api_key') || '' } })
  },
  // 导出脱敏事件 JSON
  exportEvents() {
    return apiClient.get('/export/events', {
      responseType: 'blob',
      headers: { 'X-API-Key': localStorage.getItem('algo_api_key') || '' }
    })
  },
  // 导出 s-jepa 训练数据集 zip
  exportTraining(scenePrefix = '') {
    return apiClient.get('/export/training', {
      params: scenePrefix ? { scene_prefix: scenePrefix } : {},
      responseType: 'blob',
      headers: { 'X-API-Key': localStorage.getItem('algo_api_key') || '' }
    })
  }
}

// 下载 Blob（导出接口用；header 无法走 <a href>，必须手动建 URL）
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

// 导出包装：带错误提示
export async function downloadWithKey(fetcher, filename) {
  try {
    const blob = await fetcher()
    downloadBlob(blob, filename)
    ElMessage.success(`已导出 ${filename}`)
  } catch (e) {
    // 错误已在拦截器提示
  }
}
