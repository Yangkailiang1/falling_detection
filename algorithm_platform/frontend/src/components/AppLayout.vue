<template>
  <el-container class="app-layout">
    <el-aside :width="sidebarCollapsed ? '64px' : '220px'" class="app-sidebar">
      <SideMenu />
    </el-aside>

    <el-container>
      <el-header class="app-header" height="60px">
        <div class="header-left">
          <el-button :icon="Fold" text @click="appStore.toggleSidebar()" />
          <span class="header-title">算法迭代平台</span>
        </div>
        <div class="header-right">
          <el-tag v-if="systemInfo" type="success" size="small">
            {{ systemInfo.service }} · {{ systemInfo.db_count }} 事件
          </el-tag>
          <el-tag v-else type="info" size="small">连接中...</el-tag>
        </div>
      </el-header>

      <el-main class="app-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { Fold } from '@element-plus/icons-vue'
import { useAppStore } from '@/stores'
import apiClient from '@/api'
import SideMenu from './SideMenu.vue'

const appStore = useAppStore()
const sidebarCollapsed = computed(() => appStore.sidebarCollapsed)
const systemInfo = computed(() => appStore.systemInfo)

onMounted(async () => {
  try {
    const res = await apiClient.get('/health')
    appStore.systemInfo = res.data
  } catch {
    appStore.systemInfo = null
  }
})
</script>

<style scoped>
.app-layout {
  height: 100vh;
}
.app-sidebar {
  background: #304156;
  overflow: hidden;
  transition: width 0.3s;
}
.app-header {
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
}
.header-title {
  font-size: 18px;
  font-weight: 600;
  color: #303133;
}
.app-main {
  background: #f5f7fa;
  padding: 20px;
  overflow-y: auto;
}
</style>
