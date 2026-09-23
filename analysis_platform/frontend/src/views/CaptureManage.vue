<!--
  对应文档: 四.V3.0 - 抓拍管理页面
  功能: 远程触发设备抓拍并查看图片
-->
<template>
  <div class="page-container">
    <h2 class="page-title">抓拍管理</h2>

    <el-card class="page-card">
      <el-form :inline="true" size="small">
        <el-form-item label="选择设备">
          <el-select v-model="selectedDevice" placeholder="请选择设备" style="width:250px" @change="onDeviceChange">
            <el-option
              v-for="d in devices"
              :key="d.deviceSerial"
              :label="`${d.deviceName} (${d.deviceSerial.slice(0,8)}...)`"
              :value="d.deviceSerial"
              :disabled="d.status !== 1"
            />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="doCapture" :disabled="!selectedDevice" :loading="capturing">
            <el-icon><Camera /></el-icon> 立即抓拍
          </el-button>
          <el-button @click="refreshAll" :loading="refreshing">
            <el-icon><Refresh /></el-icon> 刷新
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card class="page-card">
      <template #header>抓拍记录</template>
      <el-empty v-if="captures.length === 0 && !capturing" description='点击"立即抓拍"拍摄照片' />
      <div v-if="captures.length > 0" class="capture-grid">
        <div v-for="(cap, idx) in captures" :key="idx" class="capture-item">
          <el-image :src="cap.pic_url" fit="cover" style="width:100%;aspect-ratio:16/9;border-radius:4px"
            :preview-src-list="[cap.pic_url]" />
          <div class="capture-info">
            <span>{{ cap.device_serial?.slice(0, 12) }}...</span>
            <span>{{ new Date(cap.capture_time * 1000).toLocaleString('zh-CN') }}</span>
          </div>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
// [开发文档 四.V3.0 - 抓拍管理]
import { ref, onMounted } from 'vue'
import { getDeviceList } from '@/api/devices'
import { triggerCapture } from '@/api/capture'
import { Camera, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

const devices = ref([])
const selectedDevice = ref('')
const captures = ref([])
const capturing = ref(false)
const refreshing = ref(false)

async function loadDevices() {
  try {
    const res = await getDeviceList(0, 100)
    if (res.success) devices.value = res.data.items || []
  } catch { /* 静默处理 */ }
}

async function doCapture() {
  capturing.value = true
  try {
    const res = await triggerCapture(selectedDevice.value)
    captures.value.unshift({
      ...res.data,
      device_serial: selectedDevice.value,
      capture_time: Math.floor(Date.now() / 1000)
    })
    ElMessage.success('抓拍成功')
  } catch {
    ElMessage.error('抓拍失败')
  } finally {
    capturing.value = false
  }
}

function onDeviceChange() {}
function refreshAll() { refreshing.value = true; loadDevices().finally(() => { refreshing.value = false }) }

onMounted(() => loadDevices())
</script>

<style scoped>
.capture-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }
.capture-item { border: 1px solid #ebeef5; border-radius: 8px; overflow: hidden; }
.capture-info { display: flex; justify-content: space-between; padding: 8px 12px; font-size: 12px; color: #909399; }
</style>
