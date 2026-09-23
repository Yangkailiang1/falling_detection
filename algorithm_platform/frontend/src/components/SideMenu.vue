<template>
  <div class="side-menu">
    <div class="logo-area">
      <span v-if="!appStore.sidebarCollapsed" class="logo-text">算法迭代平台</span>
      <span v-else class="logo-icon">🧠</span>
    </div>

    <el-menu
      :default-active="activeMenu"
      :collapse="appStore.sidebarCollapsed"
      background-color="#304156"
      text-color="#bfcbd9"
      active-text-color="#409eff"
      router
    >
      <el-menu-item index="/dashboard">
        <template #title>
          <el-icon><Odometer /></el-icon>
          <span>仪表盘</span>
        </template>
      </el-menu-item>

      <el-menu-item index="/events">
        <template #title>
          <el-icon><List /></el-icon>
          <span>事件浏览</span>
        </template>
      </el-menu-item>

      <el-menu-item index="/ingest">
        <template #title>
          <el-icon><Upload /></el-icon>
          <span>数据接入</span>
        </template>
      </el-menu-item>
    </el-menu>

    <div class="sidebar-footer" v-if="!appStore.sidebarCollapsed">
      <span class="version-text">v1.0.0 · 脱敏数据 · 无画面</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAppStore } from '@/stores'
import { Odometer, List, Upload } from '@element-plus/icons-vue'

const route = useRoute()
const appStore = useAppStore()
const activeMenu = computed(() => route.path)
</script>

<style scoped>
.side-menu {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.logo-area {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}
.logo-text {
  color: #fff;
  font-size: 16px;
  font-weight: bold;
}
.logo-icon {
  font-size: 24px;
}
.el-menu {
  border-right: none;
  flex: 1;
}
.sidebar-footer {
  padding: 12px;
  text-align: center;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
}
.version-text {
  color: #666;
  font-size: 12px;
}
</style>
