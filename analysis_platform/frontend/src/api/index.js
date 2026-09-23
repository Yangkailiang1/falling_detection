// [开发文档 二.V1.0 - Axios HTTP客户端]
// 功能: 创建Axios实例，配置请求/响应拦截器，统一错误处理
// 对应文档章节: 三.3.1 - 前后端通信
import axios from 'axios'
import { ElMessage } from 'element-plus'

// 创建Axios实例，配置基础URL和超时时间
const apiClient = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' }
})

// [开发文档 二.V1.0 - 请求拦截器]
// 请求拦截器：可在请求发送前添加Token等
apiClient.interceptors.request.use(
  config => config,
  error => Promise.reject(error)
)

// [开发文档 二.V1.0 - 响应拦截器]
// 响应拦截器：统一处理错误消息
apiClient.interceptors.response.use(
  response => response.data,
  error => {
    const msg = error.response?.data?.message || '网络请求失败'
    ElMessage.error(msg)
    return Promise.reject(error)
  }
)

export default apiClient
