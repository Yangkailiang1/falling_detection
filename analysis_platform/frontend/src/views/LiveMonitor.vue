<template>
  <div class="live-monitor" ref="pageRef" tabindex="0">
    <!-- 标题栏 -->
    <div class="top-bar">
      <h2>实时监控</h2>
      <div class="top-right">
        <el-tag v-if="demoVideoUrl" :type="demoPlaying ? 'danger' : 'warning'" size="small" effect="dark">
          {{ demoPlaying ? '演示运行中' : '按 F8 播放一次' }}
        </el-tag>
        <el-tag v-if="connected" type="success" size="small" effect="dark">已连接</el-tag>
        <el-tag v-else-if="loading" type="warning" size="small" effect="dark">连接中</el-tag>
        <el-tag v-else type="danger" size="small" effect="dark">未连接</el-tag>
        <el-button :icon="Refresh" size="small" @click="reconnect" :loading="loading">重连</el-button>
      </div>
    </div>

    <!-- 主体：左视频区 + 右侧栏 -->
    <div class="main-content">
      <!-- 左：视频 + 工具栏 + WASD -->
      <div class="video-section">
        <div class="video-card">
          <div class="stream-bar" v-if="demoVideoUrl || accessToken">
            <span class="stream-dot"></span>
            <span v-if="demoVideoUrl">视频流 · 检测链路运行中</span>
            <span v-else-if="streamInfo.video">{{ streamInfo.video.width }}×{{ streamInfo.video.height }} {{ streamInfo.video.videoFormatName }} @{{ streamInfo.video.frameRate }}fps</span>
            <span v-else>检测中…</span>
          </div>

          <img v-if="demoRawImage" class="demo-video" :src="demoRawImage" />

          <VideoPlayer
            v-else-if="accessToken" ref="videoPlayerRef"
            :deviceSerial="deviceSerial" :accessToken="accessToken"
            :key="reconnectKey" :width="960" :height="540"
            @streamInfo="onStreamInfo"
            @recordingState="onRecording" @talkingState="onTalking" @soundState="onSound"
          />

          <div v-else-if="loading" class="loading-zone">
            <el-icon class="is-loading" :size="32"><Loading /></el-icon><span>获取凭证…</span>
          </div>
          <div v-else class="loading-zone"><span>无法获取播放凭证</span></div>

          <!-- 工具栏 -->
          <div class="toolbar" v-if="accessToken && !demoVideoUrl">
            <el-button-group>
              <el-button size="small" @click="handleCapture"><el-icon><Camera /></el-icon>截图</el-button>
              <el-button size="small" :type="isRecording?'danger':''" @click="handleRecord">
                <el-icon><VideoCamera /></el-icon>{{ isRecording?'停止':'录制' }}
              </el-button>
              <el-button size="small" :type="isTalking?'warning':''" @click="handleTalk">
                <el-icon><Microphone /></el-icon>{{ isTalking?'停止':'对讲' }}
              </el-button>
              <el-button size="small" @click="handleSound">
                <el-icon><Headset /></el-icon>{{ isSoundOn?'静音':'声音' }}
              </el-button>
            </el-button-group>
          </div>

          <!-- WASD 云台 — 键盘布局 -->
          <div class="wasd-panel" v-if="accessToken && !demoVideoUrl">
            <div class="wasd-row">
              <span class="wasd-key" :class="{active:activeKey==='w'}" data-key="w">W</span>
            </div>
            <div class="wasd-row">
              <span class="wasd-key" :class="{active:activeKey==='a'}" data-key="a">A</span>
              <span class="wasd-key" :class="{active:activeKey==='s'}" data-key="s">S</span>
              <span class="wasd-key" :class="{active:activeKey==='d'}" data-key="d">D</span>
            </div>
            <p class="wasd-tip">点击视频区后，键盘 WASD 控制云台</p>
          </div>
        </div>
      </div>

      <!-- 右侧栏 -->
      <div class="sidebar">
        <FallInferencePanel
          :deviceSerial="deviceSerial"
          :sharedFrame="!!demoRawImage"
          :sharedFrameUrl="demoRawImage"
        />
        <el-card class="info-card" shadow="never">
          <template #header><span class="card-title">设备信息</span></template>
          <div class="info-list">
            <div class="info-item"><span class="lbl">设备</span><span class="val">家庭摄像头 A</span></div>
            <div class="info-item"><span class="lbl">型号</span><span class="val">CS-C6c-V220-1Q8WFL</span></div>
            <div class="info-item"><span class="lbl">输入</span><span class="val">{{ demoVideoUrl ? '视频流' : 'EZUIKit SDK（子码流）' }}</span></div>
            <div class="info-item" v-if="streamInfo.video"><span class="lbl">视频</span><span class="val">{{ streamInfo.video.width }}×{{ streamInfo.video.height }} {{ streamInfo.video.videoFormatName }}</span></div>
            <div class="info-item" v-else><span class="lbl">视频</span><span class="val dim">检测中…</span></div>
            <div class="info-item" v-if="streamInfo.audio"><span class="lbl">音频</span><span class="val">{{ audioLabel }}</span></div>
            <div class="info-item" v-else><span class="lbl">音频</span><span class="val dim">检测中…</span></div>
            <div class="info-item"><span class="lbl">检测链路</span><span class="val ok">真实运行</span></div>
          </div>
        </el-card>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import { Refresh, Loading, Camera, VideoCamera, Microphone, Headset } from '@element-plus/icons-vue'
import apiClient from '@/api'
import { startPtz, stopPtz } from '@/api/ptz'
import VideoPlayer from '@/components/VideoPlayer.vue'
import FallInferencePanel from '@/components/FallInferencePanel.vue'

const route = useRoute()
const demoVideoUrl = import.meta.env.VITE_DEMO_VIDEO_SOURCE || ''
const demoRawImage = ref('')
let demoFrameTimer = null
let demoStatusTimer = null
let demoFrameInFlight = false
let demoObjectUrl = ''
const demoPlaying = ref(false)
const demoStarting = ref(false)
const deviceSerial = ref(route.params.deviceSerial || 'CHANGE_ME_DEVICE_SERIAL')
const accessToken = ref('')
const loading = ref(true)
const connected = ref(false)
const errorMsg = ref('')
const reconnectKey = ref(0)
const pageRef = ref(null)

async function pollDemoFrame() {
  if (demoFrameInFlight) return
  demoFrameInFlight = true
  try {
    // 演示主画面直接显示模型标注帧：当前骨骼、预测骨骼与概率面板。
    const blob = await apiClient.get('/inference/annotated-frame.jpg', { responseType: 'blob' })
    // apiClient 的响应拦截器已经返回 response.data；这里必须直接使用 Blob。
    // 旧写法读取 res.data.size，导致每次轮询都静默跳过，主画面始终为空。
    if (blob?.size) {
      const nextUrl = URL.createObjectURL(blob)
      if (demoObjectUrl) URL.revokeObjectURL(demoObjectUrl)
      demoObjectUrl = nextUrl
      demoRawImage.value = nextUrl
    }
  } catch {} finally { demoFrameInFlight = false }
}

async function playDemoOnce() {
  if (!demoVideoUrl || demoStarting.value || demoPlaying.value) return
  demoStarting.value = true
  try {
    await apiClient.post('/inference/capture/play-once', {
      mode: 'spatial_v5',
      yolo_gate: false,
    })
    demoPlaying.value = true
    window.dispatchEvent(new CustomEvent('demo-capture-started'))
    if (demoStatusTimer) clearInterval(demoStatusTimer)
    demoStatusTimer = setInterval(async () => {
      try {
        const res = await apiClient.get('/inference/pipeline/status')
        if (res?.data?.capture_running === false) {
          demoPlaying.value = false
          clearInterval(demoStatusTimer)
          demoStatusTimer = null
        }
      } catch {}
    }, 500)
  } finally {
    demoStarting.value = false
  }
}

const streamInfo = ref({ video: null, audio: null })
function onStreamInfo(info) { streamInfo.value = info }

const audioLabel = computed(() => {
  const a = streamInfo.value.audio
  if (!a) return '检测中…'
  const p = [a.audioFormatName || '?']
  if (a.audioChannels) p.push(`${a.audioChannels}ch`)
  if (a.audioSamplesRate > 0) p.push(`${(a.audioSamplesRate/1000).toFixed(0)}kHz`)
  return p.join(' ')
})

const videoPlayerRef = ref(null)
const isRecording = ref(false), isTalking = ref(false), isSoundOn = ref(false)
function onRecording(v) { isRecording.value = v }
function onTalking(v) { isTalking.value = v }
function onSound(v) { isSoundOn.value = v }

function handleCapture() { videoPlayerRef.value?.capturePicture() }
function handleRecord() { isRecording.value ? videoPlayerRef.value?.stopSave() : videoPlayerRef.value?.startSave() }
function handleTalk() { isTalking.value ? videoPlayerRef.value?.stopTalk() : videoPlayerRef.value?.startTalk() }
function handleSound() { isSoundOn.value ? videoPlayerRef.value?.closeSound() : videoPlayerRef.value?.openSound() }

// WASD
const activeKey = ref('')
const wasdDir = { w: 'up', a: 'left', s: 'down', d: 'right' }
let curDir = null

function onKeyDown(e) {
  if (e.key === 'F8' && demoVideoUrl) {
    e.preventDefault()
    playDemoOnce()
    return
  }
  const dir = wasdDir[e.key?.toLowerCase()]
  if (!dir || curDir === dir) return
  e.preventDefault()
  curDir = dir; activeKey.value = e.key.toLowerCase()
  startPtz(deviceSerial.value, dir, 3).catch(() => {})
}
function onKeyUp(e) {
  const dir = wasdDir[e.key?.toLowerCase()]
  if (!dir || curDir !== dir) return
  curDir = null; activeKey.value = ''
  stopPtz(deviceSerial.value).catch(() => {})
}

async function fetchToken() {
  if (demoVideoUrl) {
    connected.value = true
    loading.value = false
    return
  }
  try {
    const res = await apiClient.get('/auth/token/ezuikit')
    accessToken.value = res.data.accessToken; connected.value = true
  } catch (e) {
    errorMsg.value = e.response?.data?.message || '凭证获取失败'
  } finally { loading.value = false }
}
function reconnect() {
  if (demoVideoUrl) {
    pollDemoFrame()
    return
  }
  loading.value = true; connected.value = false; streamInfo.value = {video:null,audio:null}
  reconnectKey.value++; fetchToken()
}

onMounted(() => {
  fetchToken()
  if (demoVideoUrl) {
    connected.value = true
    loading.value = false
    streamInfo.value = {
      video: { width: 1280, height: 720, videoFormatName: 'H.265', frameRate: 15 },
      audio: { audioFormatName: 'AAC', audioChannels: 1, audioSamplesRate: 44100 },
    }
  }
  // 正式版与演示版统一使用后端标注帧作为主画面，确保骨骼、预测骨骼和概率叠加在视频上。
  pollDemoFrame()
  demoFrameTimer = setInterval(pollDemoFrame, 150)
  window.addEventListener('keydown', onKeyDown)
  window.addEventListener('keyup', onKeyUp)
})
onBeforeUnmount(() => {
  if (demoFrameTimer) clearInterval(demoFrameTimer)
  if (demoStatusTimer) clearInterval(demoStatusTimer)
  if (demoObjectUrl) URL.revokeObjectURL(demoObjectUrl)
  window.removeEventListener('keydown', onKeyDown)
  window.removeEventListener('keyup', onKeyUp)
  if (curDir) stopPtz(deviceSerial.value).catch(()=>{})
})
</script>

<style scoped>
.live-monitor { padding: 16px 20px; min-height: calc(100vh - 60px); outline: none; }
.top-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
.top-bar h2 { margin: 0; font-size: 20px; font-weight: 600; }
.top-right { display: flex; gap: 8px; align-items: center; }

.main-content { display: flex; gap: 16px; align-items: flex-start; }

/* 视频区 */
.video-section { flex: 1; min-width: 0; }
.video-card { background: #1a1a1a; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 16px rgba(0,0,0,.12); }
.stream-bar { display: flex; align-items: center; gap: 8px; padding: 8px 16px; background: #2c2c2c; color: #c0c4cc; font-size: 12px; }
.stream-dot { width: 7px; height: 7px; border-radius: 50%; background: #67c23a; animation: pulse 2s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
.toolbar { display: flex; justify-content: center; padding: 8px 16px; background: #222; border-top: 1px solid #333; }
.loading-zone { display: flex; align-items: center; justify-content: center; height: 400px; color: #909399; gap: 8px; }
.demo-video { width: 100%; aspect-ratio: 16 / 9; display: block; object-fit: contain; background: #000; }

/* WASD 键盘布局 */
.wasd-panel { background: #1a1a2e; border-radius: 0 0 12px 12px; padding: 14px 0; text-align: center; }
.wasd-row { display: flex; justify-content: center; gap: 6px; margin-bottom: 4px; }
.wasd-key {
  display: inline-flex; align-items: center; justify-content: center;
  width: 40px; height: 36px; border-radius: 8px; font-size: 16px; font-weight: 700;
  font-family: monospace; border: 1px solid #444; background: #2c2c3e; color: #a0a0b8;
  transition: all .12s; user-select: none;
}
.wasd-key.active { background: #409eff; color: #fff; border-color: #409eff; transform: scale(1.08); box-shadow: 0 0 10px rgba(64,158,255,.4); }
.wasd-tip { margin: 8px 0 0; font-size: 11px; color: #666; }

/* 右侧栏 */
.sidebar { width: 300px; flex-shrink: 0; display: flex; flex-direction: column; gap: 14px; }
.info-card { border-radius: 12px; background: #fff; }
.info-card :deep(.el-card__header) { padding: 12px 16px; background: #f8f9fb; border-bottom: 1px solid #ebeef5; }
.card-title { font-weight: 600; font-size: 14px; }
.info-list { display: flex; flex-direction: column; gap: 8px; }
.info-item { display: flex; justify-content: space-between; align-items: center; font-size: 13px; }
.info-item .lbl { color: #909399; font-size: 12px; }
.info-item .val { color: #303133; font-weight: 500; }
.info-item .val.dim { color: #c0c4cc; }
.info-item .val.ok { color: #67c23a; }

</style>
