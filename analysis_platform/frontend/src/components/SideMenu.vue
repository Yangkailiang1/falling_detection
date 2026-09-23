<!--
  对应文档: 二.V1.0 - 侧边菜单组件
  功能: 提供系统导航菜单，支持折叠/展开
  对应文档章节: 三.3.2 - 前端路由设计
-->
<template>
  <div class="side-menu">
    <!-- Logo区域 -->
    <div class="logo-area">
      <span v-if="!appStore.sidebarCollapsed" class="logo-text">分析平台</span>
      <span v-else class="logo-icon">🛡</span>
    </div>

    <!-- 导航菜单 -->
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

      <el-sub-menu index="devices-group">
        <template #title>
          <el-icon><VideoCamera /></el-icon>
          <span>设备管理</span>
        </template>
        <el-menu-item index="/devices">设备列表</el-menu-item>
        <el-menu-item index="/monitor">多路监控</el-menu-item>
      </el-sub-menu>

      <el-menu-item index="/alarms">
        <template #title>
          <el-icon><Bell /></el-icon>
          <span>告警中心</span>
        </template>
      </el-menu-item>

      <el-menu-item index="/fall-events">
        <template #title>
          <el-icon><WarningFilled /></el-icon>
          <span>跌倒事件</span>
        </template>
      </el-menu-item>

      <el-menu-item index="/captures">
        <template #title>
          <el-icon><Picture /></el-icon>
          <span>抓拍管理</span>
        </template>
      </el-menu-item>

      <el-menu-item index="/chat">
        <template #title>
          <el-icon><ChatDotRound /></el-icon>
          <span>AI对话</span>
        </template>
      </el-menu-item>

      <el-menu-item index="/settings">
        <template #title>
          <el-icon><Setting /></el-icon>
          <span>系统设置</span>
        </template>
      </el-menu-item>
    </el-menu>

    <!-- 底部版本信息 -->
    <div class="sidebar-footer" v-if="!appStore.sidebarCollapsed">
      <span class="version-text">v1.0.0</span>
    </div>
  </div>
</template>

<script setup>
// [开发文档 二.V1.0 - 侧边菜单]
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAppStore } from '@/stores'
import { Odometer, VideoCamera, Bell, Picture, ChatDotRound, Setting, WarningFilled } from '@element-plus/icons-vue'

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
  font-size: 18px;
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
