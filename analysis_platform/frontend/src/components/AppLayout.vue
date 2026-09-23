<!--
  对应文档: 二.V1.0 - 主布局组件
  功能: 提供侧边栏+顶部+内容区的经典后台布局
  对应文档章节: 三.3.2 - 前端路由设计中的AppLayout容器
-->
<template>
  <el-container class="app-layout">
    <!-- 侧边栏 -->
    <el-aside :width="sidebarCollapsed ? '64px' : '220px'" class="app-sidebar">
      <SideMenu />
    </el-aside>

    <!-- 右侧主区域 -->
    <el-container>
      <!-- 顶部栏 -->
      <el-header class="app-header" height="60px">
        <div class="header-left">
          <el-button
            :icon="Fold"
            text
            @click="appStore.toggleSidebar()"
          />
          <span class="header-title">萤石平台接入分析平台</span>
        </div>
        <div class="header-right">
          <el-tag v-if="systemInfo" type="success" size="small">
            {{ systemInfo.service }} v{{ systemInfo.version }}
          </el-tag>
          <el-tag v-else type="info" size="small">连接中...</el-tag>
        </div>
      </el-header>

      <!-- 内容区 -->
      <el-main class="app-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
// [开发文档 二.V1.0 - 布局组件]
import { computed, onMounted } from 'vue'
import { Fold } from '@element-plus/icons-vue'
import { useAppStore } from '@/stores'
import apiClient from '@/api'
import SideMenu from './SideMenu.vue'

const appStore = useAppStore()
const sidebarCollapsed = computed(() => appStore.sidebarCollapsed)
const systemInfo = computed(() => appStore.systemInfo)

// [开发文档 四.V1.0 验收标准2]
// 启动时调用健康检查，获取系统状态
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
.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.header-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}
.app-main {
  background: var(--bg-color);
  padding: 20px;
  overflow-y: auto;
}
</style>
