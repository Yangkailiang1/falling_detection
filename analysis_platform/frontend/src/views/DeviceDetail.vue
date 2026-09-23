<!--
  对应文档: 四.V3.0 - 设备详情页面
  功能: 展示单个设备的详细信息、能力集、快速操作
-->
<template>
  <div class="page-container">
    <div class="flex-between" style="margin-bottom:20px">
      <h2 class="page-title" style="margin-bottom:0">
        <el-button text :icon="ArrowLeft" @click="$router.back()" />
        设备详情
      </h2>
    </div>

    <el-card v-loading="loading" class="page-card">
      <template v-if="device">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="设备名称">{{ device.deviceName }}</el-descriptions-item>
          <el-descriptions-item label="序列号">{{ device.deviceSerial }}</el-descriptions-item>
          <el-descriptions-item label="型号">{{ device.deviceType || '-' }}</el-descriptions-item>
          <el-descriptions-item label="固件版本">{{ device.deviceVersion || '-' }}</el-descriptions-item>
          <el-descriptions-item label="在线状态">
            <el-tag :type="device.status === 1 ? 'success' : 'danger'" effect="dark">
              {{ device.status === 1 ? '在线' : '离线' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="通道数">{{ device.channelNumber || 1 }}</el-descriptions-item>
        </el-descriptions>

        <div style="margin-top: 24px; display: flex; gap: 12px">
          <el-button type="primary" @click="$router.push(`/live/${device.deviceSerial}`)"
            :disabled="device.status !== 1">
            <el-icon><VideoPlay /></el-icon> 实时监控
          </el-button>
          <el-button type="warning" :disabled="device.status !== 1" @click="handleCapture">
            <el-icon><Camera /></el-icon> 抓拍
          </el-button>
          <el-button @click="handleRefresh">
            <el-icon><Refresh /></el-icon> 刷新状态
          </el-button>
        </div>

        <!-- 抓拍结果 -->
        <div v-if="captureUrl" style="margin-top: 20px">
          <h4>最新抓拍</h4>
          <el-image :src="captureUrl" style="max-width: 100%; max-height: 400px" fit="contain" />
        </div>
      </template>
      <el-empty v-else-if="!loading" description="设备信息加载失败" />
    </el-card>
  </div>
</template>

<script setup>
// [开发文档 四.V3.0 - 设备详情页面]
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowLeft, VideoPlay, Camera, Refresh } from '@element-plus/icons-vue'
import { getDeviceDetail } from '@/api/devices'
import { triggerCapture } from '@/api/capture'
import { ElMessage } from 'element-plus'

const route = useRoute()
const device = ref(null)
const captureUrl = ref('')
const loading = ref(false)

async function fetchDetail() {
  loading.value = true
  try {
    const res = await getDeviceDetail(route.params.deviceSerial)
    device.value = res.data
  } catch {
    ElMessage.error('获取设备详情失败')
  } finally {
    loading.value = false
  }
}

async function handleCapture() {
  try {
    const res = await triggerCapture(route.params.deviceSerial)
    captureUrl.value = res.data?.pic_url || ''
    ElMessage.success('抓拍成功')
  } catch {
    ElMessage.error('抓拍失败')
  }
}

function handleRefresh() { fetchDetail() }

onMounted(() => fetchDetail())
</script>
