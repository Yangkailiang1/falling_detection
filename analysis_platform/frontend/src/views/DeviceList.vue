<!--
  对应文档: 四.V3.0 - 设备管理页面
  功能: 展示设备列表，支持查看状态、进入详情/监控
  对应文档章节: 三.3.2 - 设备管理路由
-->
<template>
  <div class="page-container">
    <div class="flex-between" style="margin-bottom:20px">
      <h2 class="page-title" style="margin-bottom:0">设备管理</h2>
      <el-button type="primary" :icon="Refresh" @click="fetchDevices" :loading="loading">
        刷新列表
      </el-button>
    </div>

    <!-- 设备卡片列表 -->
    <el-row :gutter="16">
      <el-col :span="8" v-for="device in devices" :key="device.deviceSerial" style="margin-bottom: 16px">
        <el-card class="device-card" shadow="hover">
          <template #header>
            <div class="card-header">
              <span class="device-name">{{ device.deviceName || '未命名设备' }}</span>
              <el-tag :type="device.status === 1 ? 'success' : 'danger'" size="small" effect="dark">
                {{ device.status === 1 ? '在线' : '离线' }}
              </el-tag>
            </div>
          </template>

          <div class="device-info">
            <div class="info-row">
              <span class="label">序列号</span>
              <span class="value">{{ device.deviceSerial }}</span>
            </div>
            <div class="info-row">
              <span class="label">型号</span>
              <span class="value">{{ device.deviceType || '-' }}</span>
            </div>
            <div class="info-row">
              <span class="label">通道数</span>
              <span class="value">{{ device.channelNumber || 1 }}</span>
            </div>
          </div>

          <div class="device-actions">
            <el-button size="small" @click="$router.push(`/devices/${device.deviceSerial}`)">
              <el-icon><InfoFilled /></el-icon> 详情
            </el-button>
            <el-button
              size="small" type="primary"
              :disabled="device.status !== 1"
              @click="$router.push(`/live/${device.deviceSerial}`)"
            >
              <el-icon><VideoPlay /></el-icon> 监控
            </el-button>
            <el-button size="small" type="warning" :disabled="device.status !== 1"
              @click="handleCapture(device)">
              <el-icon><Camera /></el-icon> 抓拍
            </el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 空状态 -->
    <el-empty v-if="!loading && devices.length === 0"
      description="暂无设备，请先通过萤石App添加设备">
      <el-button type="primary" @click="fetchDevices">刷新</el-button>
    </el-empty>

    <!-- 加载状态 -->
    <div v-if="loading && devices.length === 0" class="loading-center">
      <el-icon class="is-loading" :size="36"><Loading /></el-icon>
    </div>

    <!-- 分页 -->
    <el-pagination
      v-if="total > pageSize"
      style="margin-top: 20px; justify-content: center"
      :current-page="currentPage + 1"
      :page-size="pageSize"
      :total="total"
      layout="total, prev, pager, next"
      @current-change="handlePageChange"
    />
  </div>
</template>

<script setup>
// [开发文档 四.V3.0 - 设备列表页面]
import { ref, onMounted } from 'vue'
import { Refresh, InfoFilled, VideoPlay, Camera, Loading } from '@element-plus/icons-vue'
import { getDeviceList } from '@/api/devices'
import { triggerCapture } from '@/api/capture'
import { ElMessage } from 'element-plus'

const devices = ref([])
const loading = ref(false)
const currentPage = ref(0)
const pageSize = ref(20)
const total = ref(0)

// 获取设备列表
async function fetchDevices() {
  loading.value = true
  try {
    const res = await getDeviceList(currentPage.value, pageSize.value)
    if (res.success) {
      devices.value = res.data.items
      total.value = res.data.total
    }
  } catch {
    ElMessage.error('获取设备列表失败')
  } finally {
    loading.value = false
  }
}

// 触发射像头抓拍
async function handleCapture(device) {
  try {
    await triggerCapture(device.deviceSerial)
    ElMessage.success(`${device.deviceName} 抓拍成功`)
  } catch {
    ElMessage.error('抓拍失败')
  }
}

// 分页切换
function handlePageChange(page) {
  currentPage.value = page - 1
  fetchDevices()
}

onMounted(() => fetchDevices())
</script>

<style scoped>
.device-card { transition: transform 0.2s; }
.device-card:hover { transform: translateY(-2px); }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.device-name { font-weight: 600; font-size: 15px; }
.device-info { margin-bottom: 16px; }
.info-row { display: flex; justify-content: space-between; padding: 6px 0; font-size: 13px; }
.info-row .label { color: #909399; }
.info-row .value { color: #303133; font-family: monospace; }
.device-actions { display: flex; gap: 8px; justify-content: flex-end; border-top: 1px solid #ebeef5; padding-top: 12px; }
</style>
