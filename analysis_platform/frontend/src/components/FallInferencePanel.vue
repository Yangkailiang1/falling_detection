<template>
  <div class="fip">
    <div class="fip-header">
      <span class="fip-title">🎥 AI 检测</span>
      <div style="margin-left:auto">
        <el-button size="small" :type="capturing?'danger':'primary'" round
          @click="toggleRTSP" :loading="toggling">
          {{ capturing ? '■ 停止检测' : '▶ 开始检测' }}
        </el-button>
      </div>
    </div>

    <!-- 配置区: 模型选择 + 检测方式 + 云台状态（独立一行，不挤右侧） -->
    <div class="fip-config">
      <span class="cfg-item">
        <span class="cfg-label">模型</span>
        <el-select v-model="modelMode" size="small" style="width:140px"
          :disabled="capturing" @change="onConfigChange">
          <el-option label="Spatial v5 轻量模型" value="spatial_v5" />
          <el-option label="KD 学生模型" value="kd" />
          <el-option label="WorldAV 世界模型" value="worldav" />
          <el-option label="WorldPose v2.2 (AV)" value="worldpose" />
        </el-select>
      </span>
      <span class="cfg-item">
        <span class="cfg-label">检测方式</span>
        <el-select v-model="yoloGate" size="small" style="width:120px"
          :disabled="capturing" @change="onConfigChange">
          <el-option label="YOLO 预检" :value="true" />
          <el-option label="直接检测" :value="false" />
        </el-select>
      </span>
      <span class="cfg-item" style="margin-left:auto">
        <el-tag v-if="capturing && trackingState" :type="trackingTagType" size="small" effect="dark">
          {{ trackingLabel }}
        </el-tag>
      </span>
    </div>

    <!-- 标注帧 -->
    <div class="anno-frame">
      <!-- 演示模式与主窗口复用同一标注帧 URL；不重复请求，也不再显示空占位。 -->
      <img
        v-if="(sharedFrame && sharedFrameUrl) || (!sharedFrame && annotatedImage)"
        :src="sharedFrame ? sharedFrameUrl : annotatedImage"
        style="width:100%;display:block"
      />
      <div v-else class="anno-placeholder">⏳ 等待检测画面…</div>
    </div>

    <!-- 人数 -->
    <div class="count-bar">
      <span>检测到 <strong>{{ persons.length }}</strong> 人</span>
      <span style="color:#888;font-size:11px">{{ yoloTime }}ms YOLO</span>
    </div>

    <!-- 概率指标面板（按模型区分: KD 纯视频 / WorldAV 多模态） -->
    <div class="metrics-panel" v-if="showMetrics">
      <!-- 公共: P(fall) -->
      <div class="metric-row">
        <span class="metric-label">P(fall)</span>
        <span class="metric-value" :style="{color:probColor(pFall)}">{{ (pFall*100).toFixed(2) }}%</span>
        <el-tag v-if="alertActive" type="danger" size="small">⚠ 告警</el-tag>
      </div>
      <!-- Spatial v5 / WorldAV: 展示多模态拆分结果 -->
      <template v-if="modelMode === 'spatial_v5' || modelMode === 'worldav' || modelMode === 'worldpose'">
        <div class="metric-row sub">
          <span>视觉</span><span :style="{color:probColor(pVisual)}">{{ (pVisual*100).toFixed(1) }}%</span>
        </div>
        <div class="metric-row sub" v-if="modelMode === 'spatial_v5'">
          <span>世界</span><span :style="{color:probColor(pWorld)}">{{ (pWorld*100).toFixed(1) }}%</span>
        </div>
        <div class="metric-row sub" v-if="modelMode === 'spatial_v5'">
          <span>姿态</span><span :style="{color:probColor(pPose)}">{{ (pPose*100).toFixed(1) }}%</span>
        </div>
        <div class="metric-row sub" v-if="modelMode === 'spatial_v5'">
          <span>未来风险</span><span :style="{color:probColor(pFuture)}">{{ (pFuture*100).toFixed(1) }}%</span>
        </div>
        <div class="metric-row sub">
          <span>AV 融合</span><span :style="{color:probColor(pAV)}">{{ (pAV*100).toFixed(1) }}%</span>
        </div>
        <div class="metric-row">
          <span class="metric-label">Gate</span>
          <span class="metric-value">{{ gate.toFixed(3) }}</span>
          <span class="metric-label">音频</span>
          <span :class="audioAvail ? 'status-on' : 'status-off'">{{ audioAvail ? 'ON' : 'OFF' }}</span>
        </div>
      </template>
    </div>

    <!-- Pose 医疗结果 -->
    <div class="pose-panel" v-if="poseResult && poseResult.active">
      <div :class="['risk-banner', 'risk-'+poseResult.risk_level]">
        {{ riskLabel(poseResult.risk_level) }}
      </div>
      <div class="pose-details">
        <span>着地: {{ poseResult.first_contact_part || '?' }} / {{ poseResult.first_side || '?' }}</span>
      </div>
      <div class="pose-msg">{{ poseResult.message }}</div>
    </div>

    <!-- 降级提示 -->
    <div class="fallback-banner" v-if="fallbackMode">
      ⚠ WorldAV 不可用，已降级到 KD-Stride-2
    </div>

    <!-- 每人卡片 -->
    <div class="person-grid" v-if="persons.length">
      <div v-for="p in persons" :key="p.idx" class="person-card"
        :style="{borderColor: probColor(p.fall_prob)}">
        <img v-if="p.crop_b64" :src="p.crop_b64" class="crop-img" />
        <div class="crop-placeholder" v-else>加载中…</div>
        <div class="card-info">
          <span class="ci-label">#{{ p.idx+1 }}</span>
          <span class="ci-conf">YOLO {{ (p.conf*100).toFixed(0) }}%</span>
          <span v-if="p.fall_prob !== undefined" class="ci-fall" :style="{color:probColor(p.fall_prob)}">
            {{ (p.fall_prob*100).toFixed(1) }}%
          </span>
          <span v-else class="ci-wait">…</span>
        </div>
        <!-- 实时跌倒概率迷你曲线 -->
        <svg class="prob-spark" :viewBox="`0 0 ${SPARK_W} ${SPARK_H}`" preserveAspectRatio="none">
          <line :x1="0" :y1="thresholdY" :x2="SPARK_W" :y2="thresholdY"
                stroke="#e6a23c" stroke-width="0.6" stroke-dasharray="2 2" opacity="0.55"/>
          <polyline
            :points="sparkPoints"
            fill="none"
            :stroke="probColor(p.fall_prob)"
            stroke-width="1.4"
            stroke-linejoin="round"
            stroke-linecap="round"
          />
          <text v-if="probHistory.length" :x="SPARK_W-2" :y="Math.min(sparkLastY+8, SPARK_H-2)"
                text-anchor="end" font-size="6" :fill="probColor(p.fall_prob)">
            {{ ((p.fall_prob ?? 0)*100).toFixed(0) }}%
          </text>
        </svg>
      </div>
    </div>
    <div class="empty-state" v-else-if="capturing">
      <p>未检测到人 — 仅运行 YOLO</p>
    </div>

    <!-- 底部统计 -->
    <div class="footer-stats">
      <div><span>{{ totalInferences }}</span><small>推理</small></div>
      <div><span>{{ inferenceTime }}ms</span><small>{{ fallbackMode ? 'KD' : (modelMode === 'spatial_v5' ? 'Spatial v5' : 'AV') }}</small></div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import apiClient from '@/api'

const props = defineProps({
  deviceSerial: { type: String, default: '' },
  // LiveMonitor 在演示模式下提供主画面的唯一标注帧源，侧栏不再重复拉取 JPEG。
  sharedFrame: { type: Boolean, default: false },
  sharedFrameUrl: { type: String, default: '' },
})

const capturing = ref(false); const toggling = ref(false)
const annotatedImage = ref(''); const persons = ref([])
const totalInferences = ref(0); const inferenceTime = ref(0)
const yoloTime = ref(0); let pollTimer = null
let frameRequestInFlight = false

// 云台状态（只读展示）
const trackingState = ref('')    // idle / tracking / scanning
const trackingDir = ref('')

// 模型选择与检测方式（capture 未运行时才能改）
const modelMode = ref('spatial_v5')      // spatial_v5 / kd / worldav
const yoloGate = ref(true)       // true=YOLO预检, false=直接检测
function onConfigChange() {
  // 配置修改后下次启动检测时生效（capture 运行时禁用）
}

function onDemoCaptureStarted() {
  modelMode.value = 'spatial_v5'
  yoloGate.value = false
  capturing.value = true
  probHistory.value = []
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(poll, 150)
  poll()
}

// WorldAV metrics
const pFall = ref(0); const pVisual = ref(0); const pAV = ref(0)
const pWorld = ref(0); const pPose = ref(0); const pFuture = ref(0)
const gate = ref(0); const audioAvail = ref(false)
const alertActive = ref(false); const fallbackMode = ref(false)
const poseResult = ref(null)

// 实时概率曲线 (最近 ~60s, 每 300ms 一个点)
const MAX_HISTORY = 200
const probHistory = ref([])
const SPARK_W = 120
const SPARK_H = 34

// 概率 → 曲线 y 坐标 (0=顶部)
function probToY(v) {
  return SPARK_H - 3 - Math.min(Math.max(v, 0), 1) * (SPARK_H - 8)
}
const detectorThreshold = ref(0.669911)
const thresholdY = computed(() => probToY(detectorThreshold.value))  // 阈值虚线位置

// 折线点串 (缩放填充整个宽度)
const sparkPoints = computed(() => {
  const n = probHistory.value.length
  if (n < 2) return ''
  const step = SPARK_W / (MAX_HISTORY - 1)
  return probHistory.value.map((v, i) => {
    const x = i * step
    const y = probToY(v)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
})
const sparkLastY = computed(() => {
  if (!probHistory.value.length) return probToY(0)
  return probToY(probHistory.value[probHistory.value.length - 1])
})

function pushHistory(v) {
  const arr = probHistory.value
  arr.push(v)
  if (arr.length > MAX_HISTORY) arr.shift()
}

const showMetrics = computed(() => {
  return capturing.value && pFall.value !== undefined
})

// 云台状态徽章（只读）
const trackingLabel = computed(() => {
  const dirMap = {
    idle: '云台待机', left: '跟随←', right: '跟随→', up: '跟随↑', down: '跟随↓',
    'scan-left': '寻找中←', 'scan-right': '寻找中→', 'scan-up': '寻找中↑', 'scan-down': '寻找中↓',
  }
  const d = dirMap[trackingDir.value]
  if (d) return d
  if (trackingState.value === 'scanning') return '云台寻找中'
  if (trackingState.value === 'tracking') return '云台跟随中'
  return '云台待机'
})
const trackingTagType = computed(() => {
  if (trackingState.value === 'scanning') return 'warning'
  if (trackingState.value === 'tracking') return 'danger'
  return 'info'
})

function probColor(p) {
  if (p === undefined || p === null) return '#555'
  if (p >= 0.7) return '#f56c6c'
  if (p >= 0.4) return '#e6a23c'
  return '#67c23a'
}

function riskLabel(level) {
  const map = { critical: '🔴 Ⅰ级高危', high: '🟠 Ⅱ级中危', medium: '🟡 Ⅲ级低危' }
  return map[level] || level || '未知'
}

async function poll() {
  try {
    // 演示模式由 LiveMonitor 提供唯一标注帧轮询；普通设备页仍保留兼容请求。
    if (!props.sharedFrame && !frameRequestInFlight) {
      frameRequestInFlight = true
      apiClient.get('/inference/annotated-frame', { validateStatus: s => s < 500 })
        .then(imgRes => { if (imgRes?.data?.image) annotatedImage.value = imgRes.data.image })
        .catch(() => {})
        .finally(() => { frameRequestInFlight = false })
    }

    const [probRes, pipeRes] = await Promise.all([
      apiClient.get('/inference/live-prob'),
      apiClient.get('/inference/pipeline/status'),
    ])
    const prob = probRes?.data || {}; const pipe = pipeRes?.data || {}
    totalInferences.value = prob.total_inferences || 0
    inferenceTime.value = prob.last_inference_time_ms || 0
    yoloTime.value = pipe.yolo_time_ms || prob.yolo_time_ms || 0
    persons.value = pipe.person_details || []

    // WorldAV fields (from live-prob or pipeline/status)
    pFall.value = prob.p_fall ?? pipe.fall_prob ?? 0
    detectorThreshold.value = Number(prob.threshold ?? pipe.detector_threshold ?? 0.669911)
    pVisual.value = prob.p_visual ?? pipe.p_visual ?? 0
    pAV.value = prob.p_av ?? pipe.p_av ?? 0
    pWorld.value = prob.p_world ?? pipe.p_world ?? 0
    pPose.value = prob.p_pose ?? pipe.p_pose ?? 0
    pFuture.value = prob.p_future_warning ?? pipe.p_future_warning ?? 0
    gate.value = prob.gate ?? pipe.gate ?? 0
    audioAvail.value = prob.audio_available ?? pipe.audio_available ?? false
    alertActive.value = prob.alert_active ?? pipe.alert_active ?? false
    fallbackMode.value = prob.fallback_mode ?? pipe.fallback_mode ?? false
    // [2026-08-13] 告警片段结束后清除残留 pose_result:
    // 跌倒事件归档/处理完(alert_active 回落)后面板回到待检状态, 不再一直显示上一次检测的横幅
    if (alertActive.value) {
      poseResult.value = prob.pose_result ?? pipe.pose_result ?? null
    } else {
      poseResult.value = null
    }
    // 记录概率历史 (供迷你曲线)
    if (pipe.fall_prob !== undefined || prob.p_fall !== undefined) {
      pushHistory(pFall.value)
    }
    // 云台状态（只读展示）
    const t = pipe.tracking || null
    if (t) {
      trackingState.value = t.state || ''
      trackingDir.value = t.last_decision || ''
    }
    // 回读当前模型模式（与后端一致）
    if (pipe.model_mode) modelMode.value = pipe.model_mode
    if (pipe.yolo_gate !== undefined) yoloGate.value = pipe.yolo_gate
    if (capturing.value && pipe.one_shot && pipe.capture_running === false) {
      capturing.value = false
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
    }
  } catch {}
}

async function toggleRTSP() {
  toggling.value = true
  if (capturing.value) {
    // 停止检测 → 同时停止云台追踪
    apiClient.post('/ptz-tracking/stop').catch(()=>{})
    apiClient.post('/inference/capture/stop').catch(()=>{})
    apiClient.post('/inference/reset').catch(()=>{})
    capturing.value = false; persons.value = []; annotatedImage.value = ''
    pFall.value = 0; poseResult.value = null; alertActive.value = false
    probHistory.value = []
    trackingState.value = ''; trackingDir.value = ''
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  } else {
    // 一键启动: 检测 + 云台追踪（一体）
    const res = await apiClient.get('/inference/pipeline/status').catch(() => null)
    const alreadyRunning = res?.data?.capture_running
    if (!alreadyRunning) {
      await apiClient.post('/inference/reset')
      await apiClient.post('/inference/capture/start', {
        mode: modelMode.value,
        yolo_gate: yoloGate.value,
      })
    }
    // 云台自动追踪已禁用 [2026-08-13]: 摄像头扫描会移出跌倒检测视野
    // await apiClient.post('/ptz-tracking/start', {}).catch(()=>{})
    capturing.value = true; pollTimer = setInterval(poll, 300)
  }
  toggling.value = false
}

// 刷新页面后自动恢复 LIVE + 云台状态（后端线程不受前端刷新影响）
async function syncWithBackend() {
  try {
    const res = await apiClient.get('/inference/pipeline/status')
    const running = res?.data?.capture_running
    if (running && !capturing.value) {
      capturing.value = true
      pollTimer = setInterval(poll, 150)
      poll()  // 立即拉一次数据
      // 云台自动追踪已禁用 [2026-08-13]: 摄像头扫描会移出跌倒检测视野, 实测干扰检测
      // await apiClient.post('/ptz-tracking/start', {}).catch(()=>{})
    }
  } catch {}
}
onMounted(() => {
  window.addEventListener('demo-capture-started', onDemoCaptureStarted)
  syncWithBackend()
})

// 页面卸载只清前端轮询定时器，不停止后端检测（刷新/切页不应中断跌倒监测）[2026-08-13]
// 采集由后端管理：启动时 AUTO_START_CAPTURE 自动起；手动"停止检测"按钮可显式停。
onBeforeUnmount(() => {
  window.removeEventListener('demo-capture-started', onDemoCaptureStarted)
  if(pollTimer)clearInterval(pollTimer)
})
</script>

<style scoped>
.fip {
  background: linear-gradient(160deg, #1a1a3a 0%, #16213e 60%, #1a1a2e 100%);
  border-radius: 14px;
  padding: 14px;
  color: #e8e8f0;
  font-size: 12px;
  border: 1px solid rgba(100, 120, 255, 0.15);
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
}
.fip-header { display:flex; align-items:center; gap:10px; margin-bottom:10px; }
.fip-title { font-weight:700; font-size:15px; letter-spacing:0.5px; }
.fip-sub { color:#8890b0; font-weight:400; font-size:11px; }
/* 配置区独立一行 */
.fip-config {
  display:flex; align-items:center; gap:12px;
  padding:8px 10px; margin-bottom:10px;
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(100,120,255,0.1);
  border-radius:10px;
  flex-wrap: wrap;
}
.cfg-item { display:flex; align-items:center; gap:6px; }
.cfg-label { color:#8890b0; font-size:11px; white-space:nowrap; }
.anno-frame {
  margin-bottom:8px; border-radius:10px; overflow:hidden; background:#0d0d20;
  border: 1px solid rgba(100, 120, 255, 0.1);
}
.count-bar { display:flex; justify-content:space-between; padding:4px 0; margin-bottom:8px; color:#a0a8c8; }
.count-bar strong { color:#5b8cff; font-size:13px; }
.metrics-panel {
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(100, 120, 255, 0.12);
  border-radius:10px; padding:8px 10px; margin-bottom:8px;
}
.metric-row { display:flex; align-items:center; gap:8px; padding:2px 0; }
.metric-row.sub { margin-left:12px; font-size:11px; color:#9aa0c0; }
.metric-label { color:#8890b0; font-size:11px; min-width:38px; }
.metric-value { font-weight:700; font-size:14px; color:#5b8cff; font-variant-numeric: tabular-nums; }
.status-on { color:#67c23a; font-weight:600; font-size:11px; }
.status-off { color:#f56c6c; font-weight:600; font-size:11px; }
.pose-panel {
  background: linear-gradient(160deg, rgba(245,108,108,0.12), rgba(245,108,108,0.04));
  border: 1px solid rgba(245,108,108,0.3);
  border-radius:10px; padding:8px 10px; margin-bottom:8px;
}
.risk-banner { font-weight:700; font-size:14px; padding:2px 0; margin-bottom:4px; }
.risk-critical { color:#f56c6c; }
.risk-high { color:#e6a23c; }
.risk-medium { color:#e6a23c; }
.pose-details { display:flex; gap:12px; font-size:11px; color:#ccc; margin-bottom:2px; }
.pose-msg { font-size:10px; color:#999; margin-top:2px; }
.fallback-banner { background:#3a2a1a; color:#e6a23c; padding:4px 8px; border-radius:6px; margin-bottom:8px; font-size:11px; }
.person-grid { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:8px; }
.person-card {
  display:flex; align-items:center; gap:8px;
  background: rgba(255,255,255,0.05);
  border-radius:10px; padding:6px;
  border: 2px solid #555;
  flex:1; min-width:150px; max-width:calc(50% - 4px);
  transition: border-color 0.2s;
}
.crop-img { width:52px; height:52px; border-radius:8px; object-fit:cover; }
.anno-placeholder { height:150px; display:flex; align-items:center; justify-content:center; color:#8890b0; font-size:13px; background:#0d0d20; border-radius:6px; }
.crop-placeholder { width:52px; height:52px; border-radius:8px; background:#2a2a4a; display:flex; align-items:center; justify-content:center; color:#888; font-size:10px; }
.card-info { display:flex; flex-direction:column; gap:2px; min-width:0; }
.prob-spark { width:100%; height:36px; margin-top:4px; background:rgba(13,13,32,0.7); border-radius:6px; display:block; }
.ci-label { font-weight:700; font-size:11px; color:#fff; }
.ci-conf { font-size:10px; color:#8890b0; }
.ci-fall { font-size:15px; font-weight:700; font-variant-numeric: tabular-nums; }
.ci-wait { color:#666; font-size:10px; }
.empty-state { text-align:center; padding:18px 0; color:#666; }
.footer-stats { display:flex; gap:24px; justify-content:center; padding-top:8px; border-top:1px solid rgba(100,120,255,0.1); }
.footer-stats div { text-align:center; }
.footer-stats span { display:block; font-weight:700; font-size:14px; color:#5b8cff; font-variant-numeric: tabular-nums; }
.footer-stats small { font-size:10px; color:#8890b0; }
</style>
