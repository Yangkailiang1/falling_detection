<!--
  多路监控 — 从设备列表拉取实际在线设备
-->
<template>
  <div class="page-container">
    <div class="flex-between" style="margin-bottom:20px">
      <h2 class="page-title" style="margin-bottom:0">多路监控</h2>
      <el-tag v-if="onlineCount" type="success" size="small">{{ onlineCount }} 台在线</el-tag>
    </div>

    <el-alert v-if="onlineList.length === 0 && !loading" type="info" show-icon :closable="false" style="margin-bottom:16px">
      暂未发现在线设备。请确保摄像头已配网并成功绑定到萤石账号。
    </el-alert>

    <el-row :gutter="12" v-loading="loading">
      <el-col
        v-for="device in displayList"
        :key="device.deviceSerial || device._idx"
        :xs="24" :sm="12" :lg="12"
        style="margin-bottom:12px"
      >
        <el-card class="monitor-card" :body-style="{ padding: '8px' }">
          <template #header>
            <div class="card-header">
              <span>{{ device.deviceName || '设备 ' + device.deviceSerial }}</span>
              <el-tag :type="device.status === 1 ? 'success' : 'info'" size="small">
                {{ device.status === 1 ? '在线' : '离线' }}
              </el-tag>
            </div>
          </template>
          <div v-if="device.status === 1 && token" class="monitor-mini">
            <VideoPlayerMini
              :deviceSerial="device.deviceSerial"
              :accessToken="token"
              :key="device.deviceSerial"
            />
          </div>
          <div v-else class="monitor-placeholder">
            <el-icon :size="32"><VideoCamera /></el-icon>
            <p>{{ device.status === 1 ? '正在连接...' : '设备离线' }}</p>
            <p style="font-size:11px;color:#909399">{{ device.deviceSerial }}</p>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { VideoCamera } from '@element-plus/icons-vue'
import apiClient from '@/api'
import VideoPlayerMini from '@/components/VideoPlayer.vue'

const loading = ref(false)
const devices = ref([])
const token = ref('')

const onlineList = computed(() => devices.value.filter(d => d.status === 1))
const onlineCount = computed(() => onlineList.value.length)
// 最多显示4路，之后用onlineList
const displayList = computed(() => {
  const list = [...devices.value]
  while (list.length < 2) list.push({ _idx: list.length, deviceSerial: '---', status: 0, deviceName: '待添加' })
  return list.slice(0, 4)
})

async function fetchDevices() {
  loading.value = true
  try {
    const res = await apiClient.get('/devices?page=0&page_size=20')
    devices.value = res.data.items || res.data.list || []
  } catch { /* ignore */ }
  finally { loading.value = false }
}

async function fetchToken() {
  try {
    const res = await apiClient.get('/auth/token/ezuikit')
    token.value = res.data.accessToken
  } catch {}
}

onMounted(() => {
  fetchToken()
  fetchDevices()
})
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.monitor-mini {
  background: #000;
  border-radius: 4px;
  overflow: hidden;
}
.monitor-placeholder {
  height: 200px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #c0c4cc;
  background: #fafafa;
  border-radius: 6px;
  gap: 4px;
}
</style>
