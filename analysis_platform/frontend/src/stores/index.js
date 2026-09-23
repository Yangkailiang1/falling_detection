// [开发文档 二.技术栈 - Pinia状态管理]
// 功能: 全局状态管理，管理系统级状态如侧边栏折叠、加载状态等
// 对应文档章节: 三.3.1 - 前端架构
import { defineStore } from 'pinia'
import { ref } from 'vue'

// 主Store - 管理全局应用状态
export const useAppStore = defineStore('app', () => {
  // 侧边栏是否折叠
  const sidebarCollapsed = ref(false)
  // 全局加载状态
  const globalLoading = ref(false)
  // 系统信息（从健康检查获取）
  const systemInfo = ref(null)

  /**
   * 切换侧边栏折叠状态
   * - 对应文档章节: 四.V1.0 - 基础布局
   */
  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }

  /**
   * 设置全局加载状态
   * - 对应文档章节: 四.V1.0 - 基础交互
   * - 参数: loading(boolean) - 是否加载中
   */
  function setLoading(loading) {
    globalLoading.value = loading
  }

  // 导出store实例
  return { sidebarCollapsed, globalLoading, systemInfo, toggleSidebar, setLoading }
})

// 创建并导出pinia实例
import { createPinia } from 'pinia'
export const pinia = createPinia()
