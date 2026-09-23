<!--
  对应文档: 四.V4.0 - 仪表盘页面
  功能: 系统总览，展示设备统计等关键指标
  对应文档章节: 三.3.2 - 仪表盘路由
-->
<template>
  <div class="page-container">
    <h2 class="page-title">仪表盘</h2>

    <!-- 统计卡片 -->
    <el-row :gutter="20">
      <el-col :span="6" v-for="card in statCards" :key="card.title">
        <el-card class="stat-card" shadow="hover">
          <div class="stat-content">
            <div class="stat-icon" :style="{ background: card.color }">
              <el-icon :size="28"><component :is="card.icon" /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ card.value }}</div>
              <div class="stat-title">{{ card.title }}</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 系统状态和设备列表 -->
    <el-row :gutter="20" style="margin-top: 20px">
      <el-col :span="12">
        <el-card class="page-card">
          <template #header>系统状态</template>
          <el-descriptions :column="1" size="small">
            <el-descriptions-item label="服务">萤石平台接入分析平台</el-descriptions-item>
            <el-descriptions-item label="萤石API">
              <el-tag :type="stats.api_configured ? 'success' : 'danger'" size="small">
                {{ stats.api_configured ? '已配置' : '未配置' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="Token">
              <el-tag :type="stats.token_valid ? 'success' : 'warning'" size="small">
                {{ stats.token_valid ? '有效' : '待验证' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="AI模型">{{ stats.model_name || '-' }}</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card class="page-card">
          <template #header>快速操作</template>
          <el-space direction="vertical" :size="12">
            <el-button type="primary" @click="$router.push('/devices')">
              <el-icon><VideoCamera /></el-icon> 查看设备
            </el-button>
            <el-button type="success" @click="$router.push('/chat')">
              <el-icon><ChatDotRound /></el-icon> AI对话
            </el-button>
            <el-button type="warning" @click="$router.push('/alarms')">
              <el-icon><Bell /></el-icon> 查看告警
            </el-button>
          </el-space>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
// [开发文档 四.V4.0 - 仪表盘]
import { ref, computed, onMounted } from 'vue'
import { VideoCamera, Bell, DataLine, ChatDotRound } from '@element-plus/icons-vue'
import { getDashboardStats } from '@/api/dashboard'

const stats = ref({})
const loading = ref(false)

const statCards = computed(() => [
  { title: '设备总数', value: stats.value.total_devices ?? '-', icon: VideoCamera, color: '#409eff' },
  { title: '在线设备', value: stats.value.online_devices ?? '-', icon: DataLine, color: '#67c23a' },
  { title: '离线设备', value: stats.value.offline_devices ?? '-', icon: VideoCamera, color: '#f56c6c' },
  { title: '今日告警', value: stats.value.today_alarms ?? '-', icon: Bell, color: '#e6a23c' }
])

async function fetchStats() {
  loading.value = true
  try {
    const res = await getDashboardStats()
    if (res.success) stats.value = res.data
  } catch { /* 使用默认值 */ }
  finally { loading.value = false }
}

onMounted(() => fetchStats())
</script>

<style scoped>
.stat-card { cursor: default; }
.stat-content { display: flex; align-items: center; gap: 16px; }
.stat-icon { width: 56px; height: 56px; border-radius: 12px; display: flex; align-items: center; justify-content: center; color: #fff; }
.stat-value { font-size: 28px; font-weight: bold; color: #303133; }
.stat-title { font-size: 14px; color: #909399; margin-top: 4px; }
</style>
