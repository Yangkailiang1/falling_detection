<template>
  <div class="fall-detail" v-loading="loading">
    <!-- 返回按钮 + 标题 -->
    <div class="detail-header">
      <el-button @click="$router.push('/fall-events')" text>
        <el-icon><ArrowLeft /></el-icon> 返回事件列表
      </el-button>
      <div class="header-right">
        <el-tag v-if="event.feedback?.is_false_alarm" type="danger" size="large">已标记误报</el-tag>
        <el-button v-else type="warning" plain @click="showFeedbackDialog = true" size="small">
          <el-icon><WarningFilled /></el-icon> 标记误报
        </el-button>
      </div>
    </div>

    <h2 v-if="event.event_id" class="event-title">
      跌倒事件 #{{ event.event_id }}
      <span class="event-time">{{ formatTime(event.created_at) }}</span>
    </h2>

    <!-- ===== 风险等级横幅 ===== -->
    <div v-if="event.risk" class="risk-banner" :class="`risk-${event.risk.level}`">
      <div class="banner-icon">
        <el-icon :size="48"><WarningFilled /></el-icon>
      </div>
      <div class="banner-content">
        <div class="banner-level">
          <el-tag :type="riskTagType(event.risk.level)" size="large" effect="dark">
            风险等级：{{ event.risk.level }}级（{{ event.risk.level_name }}）
          </el-tag>
        </div>
        <div class="banner-response" v-if="event.response">
          <el-icon><Bell /></el-icon>
          干预策略：{{ event.response.strategy }}
        </div>
      </div>
    </div>

    <!-- ===== 语音交互（摄像头对讲） ===== -->
    <VoiceInteraction
      v-if="event.risk?.level && event.event_id"
      :risk-level="event.risk.level"
      :countdown-seconds="event.response?.countdown_seconds || 30"
      :event-id="event.event_id"
      :auto-start="autoInquiry"
      :voice-status="event.response?.voice_confirm_status || 'pending'"
      @cancelled="onVoiceCancelled"
      @emergency="onVoiceEmergency"
      @timeout="onVoiceTimeout"
      class="section-card"
    />

    <!-- ===== 现场抓拍图片 (V9.3) ===== -->
    <el-card v-if="event.capture_pic_url" shadow="hover" class="section-card">
      <template #header>
        <div class="card-title">
          <el-icon><PictureFilled /></el-icon> 现场抓拍
          <span class="card-sub" v-if="validCaptureTime">· {{ formatTime(event.capture_time) }}</span>
          <el-button
            type="primary" plain size="small"
            :loading="captureRefreshing"
            @click="refreshCapture"
            style="margin-left:auto"
          >
            <el-icon><Refresh /></el-icon> 重新抓拍
          </el-button>
        </div>
      </template>
      <div class="capture-preview">
        <el-image
          :src="event.capture_pic_url"
          :preview-src-list="[event.capture_pic_url]"
          :initial-index="0"
          fit="cover"
          preview-teleported
          class="capture-image"
        >
          <template #error>
            <div class="capture-error">
              <el-icon><PictureFilled /></el-icon>
              <span>图片加载失败或已过期</span>
            </div>
          </template>
        </el-image>
        <div class="capture-caption">跌倒事件现场抓拍图（点击可放大查看）</div>
      </div>
    </el-card>

    <!-- ===== 现场录像 (V9.3) ===== -->
    <el-card v-if="event.video_url" shadow="hover" class="section-card">
      <template #header>
        <div class="card-title"><el-icon><VideoCamera /></el-icon> 跌倒现场录像 <span class="card-sub">· 问询时 RTSP 录制片段</span></div>
      </template>
      <video :src="event.video_url" controls class="clip-video"></video>
      <div class="capture-caption">跌倒事件录像回放（点击播放）</div>
    </el-card>

    <!-- ===== 核心信息卡片 ===== -->
    <el-row :gutter="16" class="info-row">
      <el-col :span="12">
        <el-card shadow="hover">
          <template #header><div class="card-title"><el-icon><DataAnalysis /></el-icon> 骨骼着地分析</div></template>
          <el-descriptions :column="1" border size="default">
            <el-descriptions-item label="触地部位">
              <el-tag :type="partTagType(event.skeleton_analysis?.touch_ground_part)" size="large">
                {{ partLabel(event.skeleton_analysis?.touch_ground_part) }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="跌倒方向">{{ event.skeleton_analysis?.fall_direction || '-' }}</el-descriptions-item>
            <el-descriptions-item label="可能伤害">
              <el-tag v-for="inj in event.risk?.likely_injury_types" :key="inj" type="danger" size="small" style="margin-right:4px">
                {{ inj }}
              </el-tag>
              <span v-if="!event.risk?.likely_injury_types?.length">-</span>
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>

      <el-col :span="12">
        <el-card shadow="hover">
          <template #header><div class="card-title"><el-icon><Cpu /></el-icon> 检测参数</div></template>
          <el-descriptions :column="1" border size="default">
            <el-descriptions-item label="检测置信度">
              <el-progress
                :percentage="Math.round((event.detection?.confidence || 0) * 100)"
                :color="event.detection?.confidence > 0.9 ? '#67c23a' : '#e6a23c'"
                :stroke-width="16"
              />
            </el-descriptions-item>
            <el-descriptions-item label="推理延迟">{{ event.detection?.latency_ms }} ms</el-descriptions-item>
            <el-descriptions-item label="视频窗口">{{ event.detection?.video_window_frames }}帧 / {{ event.detection?.video_window_duration_s }}s</el-descriptions-item>
            <el-descriptions-item label="发生地点">
              <el-icon><Location /></el-icon> {{ event.context?.location || '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="检测设备">{{ event.context?.device_serial || '-' }}</el-descriptions-item>
            <el-descriptions-item label="场景">{{ event.context?.scenario_name || '-' }}</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>

    <!-- ===== 医疗报告 ===== -->
    <el-card shadow="hover" class="section-card" v-if="event.medical_report?.full_text">
      <template #header><div class="card-title"><el-icon><Document /></el-icon> 萤石大模型医疗简报</div></template>
      <div class="report-content">
        <el-alert
          :title="`建议措施: ${event.medical_report?.recommendation || '-'}`"
          :type="event.risk?.level === 'I' ? 'error' : 'warning'"
          :closable="false"
          show-icon
          style="margin-bottom:16px"
        />
        <div class="report-text" v-html="formatReport(event.medical_report?.full_text)"></div>
      </div>
    </el-card>

    <el-card shadow="hover" class="section-card" v-else>
      <template #header><div class="card-title"><el-icon><Document /></el-icon> 医疗简报</div></template>
      <el-empty description="未生成医疗报告（快速模拟模式跳过）" :image-size="60" />
    </el-card>

    <el-row :gutter="16">
      <!-- ===== 通知状态 ===== -->
      <el-col :span="12">
        <el-card shadow="hover" class="section-card">
          <template #header><div class="card-title"><el-icon><ChatLineSquare /></el-icon> 通知状态</div></template>
          <div class="notify-grid" v-if="event.notification">
            <!-- 企业微信推送 (真实) -->
            <div class="notify-item" :class="{ active: notif.wechat_sent }">
              <el-icon :size="24"><ChatDotSquare /></el-icon>
              <span>企业微信群</span>
              <el-tag :type="notif.wechat_sent ? 'success' : 'info'" size="small">
                {{ notif.wechat_sent ? '已推送' : '未推送' }}
              </el-tag>
              <span class="notify-sub">文本 + 抓拍图 + 录像</span>
            </div>
            <!-- 小程序订阅消息 (真实) -->
            <div class="notify-item" :class="{ active: notif.mini_subscribed }">
              <el-icon :size="24"><ChatDotRound /></el-icon>
              <span>小程序订阅消息</span>
              <el-tag :type="notif.mini_subscribed ? 'success' : 'info'" size="small">
                {{ notif.mini_subscribed ? `已推 ${notif.mini_sent}/${notif.mini_total}` : '未推送' }}
              </el-tag>
              <span class="notify-sub">{{ notif.mini_message || '家属微信提醒' }}</span>
            </div>
            <!-- 管理端收件箱 (真实, 替代萤石APP推送) -->
            <div class="notify-item" :class="{ active: notif.inbox_written }">
              <el-icon :size="24"><Box /></el-icon>
              <span>管理端收件箱</span>
              <el-tag :type="notif.inbox_written ? 'success' : 'info'" size="small">
                {{ notif.inbox_written ? '已写入' : '未写入' }}
              </el-tag>
              <span class="notify-sub">替代萤石APP推送</span>
            </div>
            <!-- 预留通道 -->
            <div class="notify-item reserved">
              <el-icon :size="24"><Lock /></el-icon>
              <span>短信 / 电话 / 120</span>
              <el-tag type="info" size="small">未开通·预留</el-tag>
              <span class="notify-sub">需萤石短信/电话服务及120对接</span>
            </div>
          </div>
          <el-empty v-else description="暂无通知记录" :image-size="40" />
        </el-card>
      </el-col>

      <!-- ===== 响应策略 ===== -->
      <el-col :span="12">
        <el-card shadow="hover" class="section-card">
          <template #header><div class="card-title"><el-icon><AlarmClock /></el-icon> 响应策略</div></template>
          <div class="response-info" v-if="event.response">
            <div class="response-countdown" :class="`countdown-${event.risk?.level}`">
              <span class="countdown-number">{{ event.response.countdown_seconds }}</span>
              <span class="countdown-unit">秒</span>
              <span class="countdown-label">语音问询倒计时</span>
            </div>
            <div class="response-voice" v-if="event.response.countdown_seconds > 0">
              <el-steps :active="voiceStep(event)" finish-status="success" align-center>
                <el-step title="语音播报" description="摄像头播放问询" />
                <el-step title="等待回应" :description="`${event.response.countdown_seconds}秒倒计时`" />
                <el-step title="自动联络" description="无回应则紧急联络" />
              </el-steps>
            </div>
            <div class="response-voice" v-else>
              <el-alert title="高危事件 — 跳过语音问询" type="error" description="头部/脊柱着地，直接启动紧急联络流程" :closable="false" show-icon />
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- ===== 完整时间线 ===== -->
    <el-card shadow="hover" class="section-card">
      <template #header><div class="card-title"><el-icon><Timer /></el-icon> 处理时间线</div></template>
      <el-timeline>
        <el-timeline-item
          v-for="(entry, idx) in event.timeline"
          :key="idx"
          :timestamp="formatTime(entry.time)"
          :type="entry.status === 'error' ? 'danger' : entry.stage === 'archived' ? 'success' : 'primary'"
          :icon="timelineIcon(entry.stage)"
          placement="top"
        >
          <div class="tl-card">
            <div class="tl-header">
              <el-tag :type="timelineTagType(entry.stage)" size="small">{{ entry.label }}</el-tag>
              <span class="tl-duration" v-if="entry.duration_ms > 0">{{ entry.duration_ms }}ms</span>
            </div>
            <div class="tl-detail">{{ entry.detail }}</div>
          </div>
        </el-timeline-item>
      </el-timeline>
    </el-card>

    <!-- ===== 误报反馈对话框 ===== -->
    <el-dialog v-model="showFeedbackDialog" title="标记误报" width="450px">
      <el-form label-width="80px">
        <el-form-item label="标记">
          <el-radio-group v-model="feedbackForm.is_false_alarm">
            <el-radio :value="true">误报（老人未摔倒）</el-radio>
            <el-radio :value="false">确认跌倒</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="feedbackForm.comment" type="textarea" :rows="3" placeholder="请描述实际情况..." />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showFeedbackDialog = false">取消</el-button>
        <el-button type="warning" @click="submitFeedback" :loading="feedbackSubmitting">提交反馈</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ArrowLeft, WarningFilled, DataAnalysis, Cpu, Document, Bell,
  ChatLineSquare, AlarmClock, Timer, Location,
  Iphone, Monitor, Service, UserFilled,
  VideoCamera, PictureFilled, ChatDotSquare, ChatDotRound, Box, Lock, Connection, Edit, Refresh
} from '@element-plus/icons-vue'
import { fallEventApi } from '@/api/fallEvents'
import VoiceInteraction from '@/components/VoiceInteraction.vue'

const route = useRoute()
const router = useRouter()
const event = ref({
  event_id: '', status: '', created_at: '',
  detection: {}, skeleton_analysis: {}, risk: {},
  medical_report: {}, response: {}, notification: {},
  context: {}, timeline: [], feedback: null
})
const loading = ref(false)
const showFeedbackDialog = ref(false)
const feedbackSubmitting = ref(false)
const feedbackForm = reactive({ is_false_alarm: true, comment: '' })
// 重新抓拍现场图片 [V9.4]
const captureRefreshing = ref(false)

// 工具函数
const riskTagType = (l) => ({ I: 'danger', II: 'warning', III: 'success' }[l] || 'info')
const partTagType = (p) => ({ head: 'danger', spine: 'danger', hip: 'warning', shoulder: 'warning', hand: 'success', elbow: 'success', knee: 'success' }[p] || 'info')
const partLabel = (p) => ({ head: '头部', spine: '脊柱', hip: '髋部', shoulder: '肩部', hand: '手部', elbow: '肘部', knee: '膝部' }[p] || p)
const formatTime = (t) => t ? new Date(t).toLocaleString('zh-CN') : '-'
// 抓拍时间有效判断: 排除 0 / '0' / 空 / 1970 无效时间戳
const validCaptureTime = computed(() => {
  const t = event.value?.capture_time
  if (!t || t === '0' || t === 0) return false
  const d = new Date(t)
  return !isNaN(d.getTime()) && d.getFullYear() > 2000
})
// 通知状态映射 (真实通道: 企业微信/小程序订阅/收件箱; 兼容旧事件结构) [2026-08-12]
const notif = computed(() => {
  const n = event.value?.notification || {}
  const details = n.notification_details || {}
  const wechat = details.wechat || {}
  const inbox = details.app || {}
  return {
    wechat_sent: n.wechat_sent ?? (wechat.success ?? false),
    mini_subscribed: !!n.mini_subscribed,
    mini_sent: n.mini_sent ?? 0,
    mini_total: n.mini_total ?? 0,
    mini_message: n.mini_message || '',
    inbox_written: n.inbox_written ?? (inbox.success ?? false),
  }
})
const timelineTagType = (s) => ({ detected: 'info', analyzed: 'warning', reporting: 'info', notifying: 'primary', archived: 'success' }[s] || 'info')
const timelineIcon = (s) => ({ detected: VideoCamera, analyzed: DataAnalysis, reporting: ChatDotSquare, notifying: Connection, archived: Edit }[s] || Timer)
const voiceStep = (e) => e?.response?.voice_confirm_status === 'confirmed' ? 1 : 2

function formatReport(text) {
  if (!text) return ''
  return text
    .replace(/\n/g, '<br>')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
}

async function loadDetail(silent = false) {
  // silent=true (轮询/后台刷新): 不显示全页 loading 遮罩, 避免每 2s 闪烁
  if (!silent) loading.value = true
  try {
    const res = await fallEventApi.getDetail(route.params.eventId)
    event.value = res.data || event.value
    // 服务端已结束问询 → 停轮询
    const vc = event.value.response?.voice_confirm_status
    if (vc && vc !== 'pending') stopPolling()
  } catch {
    if (!silent) ElMessage.error('加载事件详情失败')
  }
  loading.value = false
}

// [V7.3] 事件处于问询中（inquiring + pending）→ 前端自动启动语音问询
const autoInquiry = computed(() =>
  event.value.status === 'inquiring' &&
  event.value.response?.voice_confirm_status === 'pending' &&
  ['I', 'II', 'III'].includes(event.value.risk?.level))

// [V7.3] 2s 轮询服务端状态（检测超时自动通知/取消等终态）
// silent=true: 轮询不显示 loading 遮罩, 修复倒计时期间页面闪烁
let pollTimer = null
function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(() => loadDetail(true), 2000)
}
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

async function submitFeedback() {
  feedbackSubmitting.value = true
  try {
    await fallEventApi.submitFeedback(route.params.eventId, feedbackForm)
    ElMessage.success('反馈已提交')
    showFeedbackDialog.value = false
    await loadDetail()
  } catch (e) {
    ElMessage.error('提交失败: ' + (e.response?.data?.message || e.message))
  }
  feedbackSubmitting.value = false
}

// [V9.4] 重新抓拍现场图片 (萤石签名 URL 过期后恢复: 抓拍→下载持久化→刷新详情)
async function refreshCapture() {
  captureRefreshing.value = true
  try {
    await fallEventApi.refreshCapture(route.params.eventId)
    ElMessage.success('重新抓拍成功')
    await loadDetail()
  } catch (e) {
    ElMessage.error('重新抓拍失败: ' + (e.response?.data?.message || e.message))
  }
  captureRefreshing.value = false
}

// 语音交互事件处理 [V7.3 接线 voice/confirm + notify]
async function onVoiceCancelled({ eventId, recognizedText }) {
  ElMessage.success('语音确认：老人安全，告警已取消')
  try {
    await fallEventApi.voiceConfirm(eventId, {
      action: 'cancel',
      recognized_text: recognizedText || '我没事'
    })
  } catch (e) {
    ElMessage.error('上报失败: ' + (e.response?.data?.message || e.message))
  }
  stopPolling()
  await loadDetail()
}

async function onVoiceEmergency({ eventId, skipped }) {
  if (skipped) {
    // 无倒计时兜底按钮：手动触发紧急联络（按风险等级默认通道）
    ElMessage.error('正在启动紧急联络...')
    try {
      await fallEventApi.triggerNotify(eventId, {})
    } catch (e) {
      ElMessage.error('触发失败: ' + (e.response?.data?.message || e.message))
    }
  } else {
    // II/III级"帮我呼叫"：调度器已触发通知，此处上报状态
    ElMessage.error('语音确认：老人求助，紧急联络已启动')
    try {
      await fallEventApi.voiceConfirm(eventId, { action: 'help' })
    } catch (e) {
      ElMessage.error('上报失败: ' + (e.response?.data?.message || e.message))
    }
  }
  stopPolling()
  await loadDetail()
}

async function onVoiceTimeout({ eventId }) {
  // timeout 由服务端调度器裁决并自动通知，前端仅刷新展示
  ElMessage.warning('语音问询超时，自动启动紧急联络')
  await loadDetail()
}

onMounted(() => {
  loadDetail()
  startPolling()
})
onBeforeUnmount(stopPolling)
</script>

<style scoped>
.fall-detail { padding: 0; max-width: 1200px; }

.detail-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.header-right { display: flex; align-items: center; gap: 8px; }

.event-title { font-size: 22px; color: #303133; margin: 0 0 20px 0; }
.event-time { font-size: 14px; color: #909399; font-weight: normal; margin-left: 12px; }

/* 风险横幅 */
.risk-banner { display: flex; align-items: center; padding: 20px 24px; border-radius: 10px; margin-bottom: 20px; }
.risk-banner.risk-I { background: linear-gradient(135deg, #fef0f0, #fde2e2); border: 2px solid #f56c6c; }
.risk-banner.risk-II { background: linear-gradient(135deg, #fdf6ec, #faecd8); border: 2px solid #e6a23c; }
.risk-banner.risk-III { background: linear-gradient(135deg, #f0f9eb, #e1f3d8); border: 2px solid #67c23a; }
.banner-icon { margin-right: 20px; color: var(--risk-color, #f56c6c); }
.risk-banner.risk-I .banner-icon { color: #f56c6c; }
.risk-banner.risk-II .banner-icon { color: #e6a23c; }
.risk-banner.risk-III .banner-icon { color: #67c23a; }
.banner-content { flex: 1; }
.banner-level { margin-bottom: 8px; }
.banner-response { font-size: 14px; color: #606266; display: flex; align-items: center; gap: 6px; }

/* 信息行 */
.info-row { margin-bottom: 16px; }
.highlight { font-weight: bold; color: #f56c6c; font-family: monospace; font-size: 15px; }

/* 通用卡片 */
.section-card { margin-bottom: 16px; }
.card-title { display: flex; align-items: center; gap: 6px; font-weight: 600; font-size: 15px; }
.card-sub { font-weight: 400; font-size: 13px; color: #909399; }

/* 现场抓拍图片 (V9.3) */
.capture-preview { text-align: center; }
.capture-image {
  width: 100%; max-height: 420px; border-radius: 8px;
  background: #111; display: block;
}
.capture-caption { font-size: 12px; color: #909399; margin-top: 8px; }

/* 现场录像 (V9.3) */
.clip-video { width: 100%; max-height: 420px; border-radius: 8px; background: #111; display: block; }
.capture-error {
  width: 100%; height: 220px; background: #f5f7fa;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  color: #909399; font-size: 13px; gap: 8px; border-radius: 8px;
}

/* 报告 */
.report-content { line-height: 1.8; }
.report-text { white-space: pre-wrap; color: #303133; font-size: 14px; }

/* 通知网格 */
.notify-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.notify-item {
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 12px; border-radius: 8px; background: #f5f7fa;
}
.notify-item.active { background: #f0f9ff; border: 1px solid #b3d8ff; }
.notify-item.reserved { background: #fafafa; opacity: 0.7; }
.notify-sub { font-size: 11px; color: #909399; line-height: 1.4; }

/* 响应策略 */
.response-countdown {
  display: flex; align-items: baseline; justify-content: center; gap: 4px;
  padding: 16px; border-radius: 8px; margin-bottom: 16px;
}
.response-countdown.countdown-I { background: #fef0f0; }
.response-countdown.countdown-II { background: #fdf6ec; }
.response-countdown.countdown-III { background: #f0f9eb; }
.countdown-number { font-size: 36px; font-weight: bold; }
.countdown-I .countdown-number { color: #f56c6c; }
.countdown-II .countdown-number { color: #e6a23c; }
.countdown-III .countdown-number { color: #67c23a; }
.countdown-unit { font-size: 16px; color: #909399; }
.countdown-label { font-size: 12px; color: #909399; }

/* 时间线 */
.tl-card { padding: 4px 0; }
.tl-header { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.tl-duration { font-size: 12px; color: #909399; font-family: monospace; }
.tl-detail { font-size: 13px; color: #606266; line-height: 1.5; }
</style>
