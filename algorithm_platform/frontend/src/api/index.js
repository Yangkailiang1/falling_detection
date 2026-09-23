// 算法迭代平台 - Axios HTTP 客户端
// 功能: 创建 Axios 实例，配置响应拦截器统一处理错误
import axios from 'axios'
import { ElMessage } from 'element-plus'

const apiClient = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' }
})

apiClient.interceptors.request.use(
  config => config,
  error => Promise.reject(error)
)

// 响应拦截器：返回 response.data，统一报错
apiClient.interceptors.response.use(
  response => response.data,
  error => {
    const msg = error.response?.data?.message || '网络请求失败'
    ElMessage.error(msg)
    return Promise.reject(error)
  }
)

export default apiClient
