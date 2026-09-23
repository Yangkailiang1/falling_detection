// 算法迭代平台 - Pinia 全局状态
import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useAppStore = defineStore('app', () => {
  const sidebarCollapsed = ref(false)
  const globalLoading = ref(false)
  const systemInfo = ref(null)

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }
  function setLoading(loading) {
    globalLoading.value = loading
  }

  return { sidebarCollapsed, globalLoading, systemInfo, toggleSidebar, setLoading }
})

import { createPinia } from 'pinia'
export const pinia = createPinia()
