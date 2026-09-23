<template>
  <div class="voice-interaction" :class="`risk-${riskLevel}`">
    <!-- 未激活状态 -->
    <div v-if="phase === 'idle'" class="vi-idle">
      <el-alert
        v-if="countdownSeconds === 0"
        title="未配置倒计时 — 可直接启动紧急联络"
        type="error"
        description="当前事件未配置语音问询倒计时（异常/旧数据），可手动启动紧急联络流程"
        :closable="false"
        show-icon
      >
        <template #default>
          <el-button type="danger" @click="triggerEmergency" :loading="emergencyTriggering" style="margin-top:12px">
            <el-icon><Phone /></el-icon> 确认启动紧急联络
          </el-button>
        </template>
      </el-alert>
      <div v-else class="vi-ready">
        <el-icon :size="48" color="#409eff"><Microphone /></el-icon>
        <h3>语音问询待启动</h3>
        <p>{{ countdownSeconds }}秒倒计时，通过摄像头扬声器播放语音提示</p>
        <el-button type="primary" size="large" @click="startInteraction" :loading="starting">
          <el-icon><VideoPlay /></el-icon> 开始语音问询
        </el-button>
      </div>
    </div>

    <!-- 正在进行语音问询 -->
    <div v-else-if="phase === 'active'" class="vi-active">
      <!-- 倒计时环 -->
      <div class="countdown-ring">
        <el-progress
          type="circle"
          :percentage="countdownPercent"
          :color="countdownColor"
          :width="140"
        >
          <template #default>
            <span class="cd-number">{{ remainingSeconds }}</span>
            <span class="cd-unit">秒</span>
          </template>
        </el-progress>
      </div>

      <!-- 当前播放的语音文本 -->
      <div class="speech-bubble" v-if="currentPrompt">
        <el-icon :size="20"><ChatLineSquare /></el-icon>
        <span>{{ currentPrompt }}</span>
      </div>

      <!-- 语音识别状态 -->
      <div class="listen-status">
        <div class="listen-wave" v-if="listening">
          <span v-for="i in 5" :key="i" class="wave-bar" :style="{ animationDelay: `${i * 0.1}s` }"></span>
        </div>
        <el-tag v-if="listening" type="warning" size="large">
          <el-icon class="is-loading"><Loading /></el-icon> 正在听取回应...
        </el-tag>
        <el-tag v-else-if="interimText" type="info" size="large">
          听到: {{ interimText }}
        </el-tag>
      </div>

      <!-- 识别到的结果 -->
      <div v-if="recognizedText" class="recognized-result">
        <el-alert title="识别结果" :type="recognizedAction === 'cancel' ? 'success' : 'error'" :closable="false">
          <template #default>
            <strong>{{ recognizedText }}</strong>
            <span v-if="recognizedAction === 'cancel'"> — 已取消告警</span>
            <span v-else-if="recognizedAction === 'help'"> — 立即启动紧急联络</span>
          </template>
        </el-alert>
      </div>

      <!-- 手动确认按钮 -->
      <div class="manual-buttons" v-if="!resolved">
        <el-button type="success" size="large" @click="confirmCancel" :loading="confirming">
          <el-icon><Select /></el-icon> 我没事（取消告警）
        </el-button>
        <el-button type="danger" size="large" @click="confirmHelp" :loading="confirming">
          <el-icon><Phone /></el-icon> 帮我呼叫（紧急联络）
        </el-button>
      </div>
      <p class="manual-hint" v-if="!resolved">无需麦克风，直接点击按钮也可完成确认</p>
    </div>

    <!-- 已确认状态 -->
    <div v-else-if="phase === 'resolved'" class="vi-resolved">
      <el-result
        :icon="resolvedAction === 'cancel' ? 'success' : 'error'"
        :title="resolvedAction === 'cancel' ? '告警已取消' : '紧急联络已启动'"
        :sub-title="resolvedAction === 'cancel'
          ? '老人确认安全，事件已标记为误报'
          : '已通知紧急联络人，医疗报告已推送'"
      >
        <template #extra>
          <el-button v-if="resolvedAction === 'cancel'" type="primary" @click="resetInteraction">
            重新问询
          </el-button>
        </template>
      </el-result>
    </div>

    <!-- 超时状态 -->
    <div v-else-if="phase === 'timeout'" class="vi-timeout">
      <el-result
        icon="warning"
        title="语音问询超时"
        sub-title="老人未在倒计时内回应，系统已自动启动紧急联络流程"
      >
        <template #extra>
          <el-tag type="danger" size="large">已通知紧急联络人</el-tag>
        </template>
      </el-result>
    </div>

    <!-- 隐藏的 EZUIKit 播放器，仅用于音频通道（startTalk → 摄像头扬声器） -->
    <div :id="ezuikitContainerId" style="width:0;height:0;overflow:hidden;opacity:0"></div>

    <!-- 摄像头音频通道状态 -->
    <div class="camera-status" v-if="phase === 'active' || phase === 'idle'">
      <div class="status-row">
        <span :class="['dot', cameraReady ? 'green' : 'yellow']"></span>
        <span v-if="!cameraReady && ezInitAttempted">摄像头连接中...</span>
        <span v-else-if="cameraReady">摄像头音频通道已就绪</span>
        <span v-else>点击开始后自动连接摄像头</span>
      </div>
      <div class="status-tips" v-if="cameraReady">
        <el-icon><Headset /></el-icon> 你说的话会通过麦克风传到摄像头扬声器
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onBeforeUnmount, onMounted, watch, nextTick } from 'vue'
import { ElMessage, ElNotification } from 'element-plus'
import apiClient from '@/api'
import {
  Microphone, VideoPlay, Loading, ChatLineSquare, Select, Phone, Headset
} from '@element-plus/icons-vue'
import { EZUIKitPlayer } from 'ezuikit-js'

// === WebRTC Audio Injection ===
// Monkey-patch RTCPeerConnection.addTrack / addTransceiver to capture the talk peer connection.
// ezuikit 内部可能走 addTrack 或 addTransceiver("audio") 两种路径，都要拦截。
// This allows us to directly inject TTS audio into the WebRTC track sent to the camera,
// bypassing the unreliable "speaker → microphone → camera" physical audio loop.
let _talkPC = null
// EZUIKit 将真实对讲 PeerConnection 暴露在全局 tts 句柄中；优先使用它，
// 避免预览链路的 PeerConnection 被 addTrack 监控误捕获。
function _getTalkPC() {
  return window.tts?.webrtcStuff?.pc || _talkPC
}
const _capturePC = (pc, kind) => {
  if (!_talkPC) {
    _talkPC = pc
    console.log(`[Voice] peer connection captured via ${kind}`)
    // Expose for console diagnostics
    window.__voicePC = pc
  }
}
const _origAddTrack = RTCPeerConnection.prototype.addTrack
RTCPeerConnection.prototype.addTrack = function (track, ...streams) {
  _capturePC(this, `addTrack(${track?.kind})`)
  return _origAddTrack.call(this, track, ...streams)
}
const _origAddTransceiver = RTCPeerConnection.prototype.addTransceiver
RTCPeerConnection.prototype.addTransceiver = function (...args) {
  _capturePC(this, `addTransceiver(${args[0]})`)
  return _origAddTransceiver.apply(this, args)
}

let _uid = 0

const props = defineProps({
  riskLevel: { type: String, default: 'II' },      // I/II/III
  countdownSeconds: { type: Number, default: 30 },   // 倒计时秒数: I=10 / II=30 / III=60
  eventId: { type: String, default: '' },
  enableVoiceRecognition: { type: Boolean, default: true },
  autoStart: { type: Boolean, default: false },   // [V7.3] 事件处于问询中时自动启动
  voiceStatus: { type: String, default: 'pending' }, // [V7.3] 服务端权威状态: pending/cancelled/help_requested/timeout
})

const emit = defineEmits(['cancelled', 'emergency', 'timeout'])

// === EZUIKit 音频通道 ===
const ezuikitContainerId = ref(`voice-ezuikit-${++_uid}`)
let ezPlayer = null
let accessToken = ''
const cameraDeviceSerial = ref('')
const cameraReady = ref(false)
const ezInitAttempted = ref(false)

async function fetchAccessToken() {
  try {
    const res = await apiClient.get('/auth/token/ezuikit')
    accessToken = res.data?.accessToken || ''
    cameraDeviceSerial.value = res.data?.deviceSerial || cameraDeviceSerial.value
    return accessToken
  } catch (e) {
    console.warn('[Voice] Failed to get accessToken:', e.message)
    return ''
  }
}

async function initEzPlayer() {
  if (ezPlayer) return
  ezInitAttempted.value = true
  const token = await fetchAccessToken()
  if (!token) {
    console.warn('[Voice] No access token for EZUIKit')
    ElMessage.warning('获取摄像头凭证失败，对讲功能不可用')
    return
  }
  if (!cameraDeviceSerial.value) {
    console.warn('[Voice] No device serial returned by backend')
    ElMessage.warning('未获取到摄像头设备标识，对讲功能不可用')
    return
  }

  await nextTick()
  const el = document.getElementById(ezuikitContainerId.value)
  if (!el) return
  el.innerHTML = ''

  try {
    ezPlayer = new EZUIKitPlayer({
      id: ezuikitContainerId.value,
      accessToken: token,
      url: `ezopen://open.ys7.com/${cameraDeviceSerial.value}/1.hd.live`,
      template: 'pcLive',
      width: 1,
      height: 1,
      audio: 1,
      decoderType: 'v3',
      plugin: ['talk'],
      handleError: (err) => {
        console.warn('[Voice] EZUIKit error:', err)
      }
    })

    ezPlayer.on(EZUIKitPlayer.EVENTS.firstFrameDisplay, () => {
      cameraReady.value = true
      console.log('[Voice] EZUIKit 音频通道就绪')
      ElMessage.success('摄像头已连接，音频通道就绪')
    })

    ezPlayer.on(EZUIKitPlayer.EVENTS.startTalk, (info) => {
      console.log('[Voice] 对讲请求已发起:', info || '')
    })
    ezPlayer.on(EZUIKitPlayer.EVENTS.talkSuccess, () => {
      console.log('[Voice] 对讲服务端已确认 — 摄像头扬声器通道可用')
    })
    ezPlayer.on(EZUIKitPlayer.EVENTS.talkError, (err) => {
      console.error('[Voice] 萤石对讲失败:', err || '')
    })

    ezPlayer.on(EZUIKitPlayer.EVENTS.stopTalk, () => {
      console.log('[Voice] 对讲结束')
    })

  } catch (e) {
    console.error('[Voice] EZUIKit init failed:', e)
    ElMessage.error('摄像头连接失败: ' + e.message)
  }
}

function startEzTalk() {
  if (ezPlayer && cameraReady.value) {
    // Reset captured PC ref — the talk stream will create a new one
    _talkPC = null
    try {
      ezPlayer.startTalk()
      console.log('[Voice] startTalk() called — WebRTC talk channel opening...')
      // Expose diagnostic reference
      window.__voiceEzPlayer = ezPlayer
    } catch (e) {
      console.error('[Voice] startTalk() threw:', e)
    }
  } else {
    console.warn('[Voice] Cannot startTalk: ezPlayer=', !!ezPlayer, 'cameraReady=', cameraReady.value)
  }
}

function stopEzTalk() {
  if (ezPlayer) {
    try { ezPlayer.stopTalk() } catch {}
  }
}

// [2026-08-03] 预连接: 挂载时提前建立摄像头音频通道（ezopen + WebRTC 对讲），
// 避免用户点击/自动触发后才开始初始化（原来首次播报需 10s+）
// 注意: 挂载时（无用户手势）getUserMedia 麦克风权限可能被拒，startTalk 会失败；
//       点击时 ensureTalkChannel() 会重试，因此这里只是"预热"，失败不影响后续。
// [V-fix] 预取 TTS 缓存按文本精确匹配（text→{audioBuffer, ctx}），
// 首条 + "请再说一次"预取；用后即弃（一次性），避免缓存被其他文案误用。
const REPEAT_PROMPT = '请再说一次，您可以试着说"我没事"或"帮我呼叫"。'
const _ttsPrefetch = new Map()

let _audioSenderRef = null  // 首次对讲建立后缓存的音频发送端；replaceTrack(null) 后 track/kind 均变 null，仍可复用

// 查找音频发送端 sender
// [V-fix] 用持久引用代替轨道检测：TTS 播完后 onended 会 sender.replaceTrack(null) 静音，
// 此时 sender.track 与 sender.kind 均为 null，仅靠 getSenders() 检测不到。
// 缓存首次找到的音频发送端，后续播报（请再说一次/确认语音）直接 replaceTrack(TTS) 复用，无需重建对讲通道（getUserMedia）。
function _findAudioSender() {
  const pc = _getTalkPC()
  if (pc && pc !== _talkPC) _talkPC = pc
  if (_audioSenderRef && pc && pc.getSenders().includes(_audioSenderRef)) {
    return _audioSenderRef
  }
  if (!pc) return null
  const s = pc.getSenders().find(s => s.track?.kind === 'audio') || null
  if (s) _audioSenderRef = s
  return s
}

// 持久静音轨道：TTS 播完后用静音轨道替换发送端，保持对讲会话存活。
// 若 replaceTrack(null)，ezuikit 会把"发送轨道为 null"判定为对讲失败(7003)并销毁整个会话，
// 导致后续确认/重播语音无法播放。
let _silentTrack = null
let _silentCtx = null
function _getSilentAudioTrack() {
  if (_silentTrack) return _silentTrack
  try {
    _silentCtx = new AudioContext()
    const dest = _silentCtx.createMediaStreamDestination()
    _silentTrack = dest.stream.getAudioTracks()[0]
    if (_silentCtx.state === 'suspended') _silentCtx.resume().catch(() => {})
  } catch {
    _silentTrack = null
  }
  return _silentTrack
}

async function ensureTalkChannel() {
  // 幂等但可重试: init → cameraReady → startTalk → 捕获 PC + 等待音频轨道绑定
  // 必须在用户手势上下文调用（startTalk 内部 getUserMedia 需要权限）
  // 判断条件必须是"存在已绑定的音频轨道"，而不是"存在 PC"——
  // 挂载时 startTalk 被权限拒绝会留下只有 PC、没有轨道的半开状态
  try {
    if (!ezPlayer) {
      console.log('[Voice] ensureTalkChannel: 初始化播放器...')
      await initEzPlayer()
    }
    let readyAttempts = 0
    while (!cameraReady.value && readyAttempts < 40) {
      await new Promise(r => setTimeout(r, 500))
      readyAttempts++
      if (readyAttempts % 4 === 0) console.log(`[Voice] 等待摄像头就绪... ${readyAttempts * 0.5}s`)
    }
    if (cameraReady.value && !_findAudioSender()) {
      console.log('[Voice] ensureTalkChannel: 建立对讲通道（无已绑定音频轨道）...')
      // 清理可能残留的 talk 状态后重建（挂载时权限被拒留下的半开 PC 必须重建）
      try { ezPlayer.stopTalk() } catch {}
      startEzTalk()
      let attempts = 0
      while (attempts < 40) {
        if (_findAudioSender()) break
        await new Promise(r => setTimeout(r, 200))
        attempts++
      }
      const sender = _findAudioSender()
      console.log(`[Voice] ensureTalkChannel: 音频轨道 ${sender ? '已绑定' : '未绑定'} (${attempts * 0.2}s)`)
    }
    return !!_findAudioSender()
  } catch (e) {
    console.warn('[Voice] ensureTalkChannel failed:', e.message)
    return false
  }
}

async function preconnect() {
  // 预热: 只初始化播放器 + 等摄像头就绪 + 预取 TTS（不需要对讲/麦克风权限）
  // startTalk 必须留在用户手势里做（getUserMedia 权限要求），否则挂载时自动 startTalk 会被浏览器拒绝
  console.log('[Voice] preconnect: 预热摄像头连接（不启动对讲）...')
  try {
    if (!ezPlayer) await initEzPlayer()
    let readyAttempts = 0
    while (!cameraReady.value && readyAttempts < 40) {
      await new Promise(r => setTimeout(r, 500))
      readyAttempts++
      if (readyAttempts % 4 === 0) console.log(`[Voice] 等待摄像头就绪... ${readyAttempts * 0.5}s`)
    }
    if (cameraReady.value) {
      console.log('[Voice] preconnect: 摄像头就绪，预取 TTS...')
      prefetchPrompts()
    }
  } catch (e) {
    console.warn('[Voice] preconnect failed:', e.message)
  }
}

// [V-fix] 预取"首条问询"+"请再说一次"的 TTS（解码缓存，播报时零等待，消除确认/重播的拉取延迟）
async function prefetchPrompts() {
  for (const text of [firstPrompt, REPEAT_PROMPT]) {
    if (_ttsPrefetch.has(text)) continue
    try {
      const resp = await fetch(`/api/voice/tts.mp3?text=${encodeURIComponent(text)}`)
      if (!resp.ok) continue
      const buf = await resp.arrayBuffer()
      const ctx = new AudioContext()
      const audioBuffer = await ctx.decodeAudioData(buf)
      _ttsPrefetch.set(text, { audioBuffer, ctx })
      console.log(`[Voice] 预取 TTS: ${text.substring(0, 18)}… ${audioBuffer.duration.toFixed(2)}s`)
    } catch (e) {
      console.warn('[Voice] 预取 TTS 失败:', e.message)
    }
  }
}

function destroyEzPlayer() {
  stopEzTalk()
  if (ezPlayer) {
    try { ezPlayer.stop() } catch {}
    try { ezPlayer.destroy() } catch {}
    ezPlayer = null
  }
  cameraReady.value = false
  _talkPC = null          // 复位，避免跨页面复用已关闭的 PC（_capturePC 只在 null 时捕获新 PC）
  _audioSenderRef = null  // 同步清空缓存的音频发送端
}

// === 状态 ===
const phase = ref('idle')          // idle/active/resolved/timeout
const resolvedAction = ref('')     // cancel/help
const starting = ref(false)
const confirming = ref(false)
const emergencyTriggering = ref(false)
const resolved = ref(false)

// [V-fix] 重试与确认语音状态
let repeatCount = 0                  // "请再说一次"重试次数（上限 2 次）
let _resolveVoicePending = ''        // 本地已确认意图 'cancel'|'help'（等服务端确认后播确认语音）
let _confirmVoicePlayed = false      // 确认语音是否已播（防重播）

// === 倒计时 ===
const totalSeconds = ref(props.countdownSeconds)
const remainingSeconds = ref(props.countdownSeconds)
let countdownTimer = null

const countdownPercent = computed(() => {
  if (totalSeconds.value === 0) return 0
  return Math.round((1 - remainingSeconds.value / totalSeconds.value) * 100)
})

const countdownColor = computed(() => {
  if (remainingSeconds.value > totalSeconds.value * 0.5) return '#67c23a'
  if (remainingSeconds.value > totalSeconds.value * 0.2) return '#e6a23c'
  return '#f56c6c'
})

// === 语音播报 (Web Speech API - TTS) ===
const currentPrompt = ref('')
// [V-fix] 只保留首条问询；不再有 50%/80% 固定重播（没听清走"请再说一次"）
const firstPrompt = '检测到您可能摔倒了，请说"我没事"取消。如需帮助，请说"帮我呼叫"。'

async function speakPrompt(text) {
  currentPrompt.value = text
  console.log('[Voice] === speakPrompt START ===')
  console.log('[Voice] text:', text.substring(0, 40))

  // Step A: 确保对讲通道就绪且音频轨道已绑定（点击手势上下文内可重试）
  if (!_findAudioSender()) {
    console.log('[Voice] 音频轨道未就绪，现场建立（点击手势上下文可获取麦克风权限）...')
    await ensureTalkChannel()
  }
  console.log('[Voice] cameraReady:', cameraReady.value, 'audioSender:', !!_findAudioSender())

  // 等待音频轨道绑定（最多 5s，startTalk 的 getUserMedia 异步完成）
  let sender = _findAudioSender()
  let senderWaits = 0
  while (!sender && senderWaits < 25) {
    await new Promise(r => setTimeout(r, 200))
    sender = _findAudioSender()
    senderWaits++
  }
  console.log('[Voice] Audio sender:', !!sender, 'track state:', sender?.track?.readyState)

  if (!sender) {
    console.warn('[Voice] No audio sender found in peer connection')
    return fallbackBrowserTTS(text)
  }

  try {
    // Step B: 防回音（摄像头扬声器播 TTS 时避免麦克风回采形成回音环）
    // 注: ezuikit 的 closeSound 在对讲模式下是 no-op、无 setVolume 公共 API，
    //     浏览器端无法直接静音接收方向；摄像头端 AEC（回声消除）兜底。
    //     若仍有回音，后续可评估降级为浏览器 TTS 播报方案。

    // Step C: 获取 TTS 音频（优先用预取缓存，否则现场拉取）
    let audioBuffer
    let audioCtx
    const cached = _ttsPrefetch.get(text)
    if (cached) {
      // [V-fix] 预取缓存按文本精确匹配（首条/请再说一次），用后即弃——
      // 避免预取迟到/被其他文案误用而播错内容
      audioBuffer = cached.audioBuffer
      audioCtx = cached.ctx
      _ttsPrefetch.delete(text)
      console.log('[Voice] 使用预取 TTS 缓存:', text.substring(0, 20))
    } else {
      const ttsUrl = `/api/voice/tts.mp3?text=${encodeURIComponent(text)}`
      console.log('[Voice] Fetching TTS:', ttsUrl.substring(0, 80))
      const response = await fetch(ttsUrl)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const arrayBuffer = await response.arrayBuffer()
      audioCtx = new AudioContext()
      audioBuffer = await audioCtx.decodeAudioData(arrayBuffer)
      console.log('[Voice] TTS fetched+decoded — duration:', audioBuffer.duration.toFixed(2), 's')
    }

    // 浏览器自动播放策略: 挂载时创建的 AudioContext 可能是 suspended，点击手势下必须 resume
    if (audioCtx.state === 'suspended') {
      try { await audioCtx.resume() } catch {}
      console.log('[Voice] AudioContext state after resume:', audioCtx.state)
    }
    if (audioCtx.state !== 'running') {
      // resume 失败（如预取 ctx 与手势无关）→ 现场重建 ctx 解码
      console.warn('[Voice] AudioContext 无法启动，现场重建解码')
      const ttsUrl = `/api/voice/tts.mp3?text=${encodeURIComponent(text)}`
      const resp = await fetch(ttsUrl)
      const buf = await resp.arrayBuffer()
      const freshCtx = new AudioContext()
      audioBuffer = await freshCtx.decodeAudioData(buf)
      audioCtx = freshCtx
      await audioCtx.resume()
    }
    if (audioCtx.state !== 'running') {
      // 重建后仍无法启动（浏览器自动播放策略，非手势异步上下文常见）→
      // 抛错走浏览器 TTS 降级，避免"静音播放"（source.start 在 suspended ctx 上无声且不报错）
      throw new Error('AudioContext 无法启动（自动播放策略），降级浏览器 TTS')
    }

    // 用 AudioBuffer 创建 MediaStream，获得可替换的音频轨道
    const source = audioCtx.createBufferSource()
    source.buffer = audioBuffer
    const dest = audioCtx.createMediaStreamDestination()
    source.connect(dest)

    const ttsTrack = dest.stream.getAudioTracks()[0]
    const originalMicTrack = sender.track

    // 替换 → 摄像头扬声器直接播放 TTS
    console.log('[Voice] Calling sender.replaceTrack with TTS track...')
    await sender.replaceTrack(ttsTrack)
    console.log('[Voice] replaceTrack SUCCESS — TTS should now be playing through camera speaker')
    source.start(0)
    console.log('[Voice] TTS source started, camera should play:', text.substring(0, 30))

    // 记录当前播放源（识别到回应时 stopCurrentPrompt 可立即打断）
    _activePromptSource = source

    // 返回"播放完成"Promise（供 startInteraction 在播完后启动倒计时）
    return new Promise((resolve) => {
      // TTS 播完 → 发送方向置静音（不恢复浏览器麦克风轨道）
      // 用户回应由摄像头麦克风（讯飞 STT）/本地 Web Speech 识别，无需传回摄像头扬声器——
      // 若恢复麦克风轨道，用户声音会被摄像头扬声器回放再被摄像头麦克风采到，形成杂音环
      source.onended = async () => {
        _activePromptSource = null
        source.disconnect()
        try { audioCtx.close() } catch {}   // 预取/现场 ctx 均用后即弃（静音轨道独立于本 ctx）
        try {
          // [V-fix] 用静音轨道替换发送端（而非 replaceTrack(null)）：
          // null 轨道会让 ezuikit 判定"对讲失败(7003)"并销毁会话，确认/重播语音将无法播放。
          await sender.replaceTrack(_getSilentAudioTrack())
          console.log('[Voice] TTS finished, 发送方向替换为静音轨道（对讲会话保持存活）')
        } catch (e) {
          console.warn('[Voice] 发送轨道置空失败:', e.message)
        }
        resolve()
      }
      // 兜底: onended 可能因异常不触发，按音频时长+2s 强制结束
      setTimeout(resolve, (audioBuffer.duration + 2) * 1000)
    })
  } catch (e) {
    console.warn('[Voice] TTS injection failed, fallback to browser TTS:', e.message)
    return fallbackBrowserTTS(text)
  }
}

// [V-fix] 统一播报辅助: 所有经摄像头扬声器的播报走这里。
// 播报期间 → 关闭摄像头麦克风（扬声器发声时防回音）+ 冻结前端倒计时 + 暂停服务端倒计时；
// 播报完成 → 立即重开麦克风接收用户回应 + 恢复/启动服务端倒计时。
// first=true（首条）: 播完才启动服务端完整倒计时（修复 Ⅰ级提前超时——倒计时从"播完首条"开始）。
async function speakPromptWithPause(text, { first = false } = {}) {
  const wasPaused = countdownPaused
  countdownPaused = true                    // 前端倒计时冻结（本 tick 跳过）
  stopCameraListening()                     // 摄像头麦克风关闭（防回音）
  if (!first) {
    try { await apiClient.post(`/fall-events/${props.eventId}/voice/pause`) } catch (e) { console.warn('[Voice] 服务端倒计时暂停失败:', e.message) }
  }
  try {
    await speakPrompt(text)
  } finally {
    if (!wasPaused) countdownPaused = false
  }
  // 播完立刻重开麦克风接收用户回应（仍在问询中时）
  if (phase.value === 'active' && !resolved.value) resumeListening()
  // 服务端倒计时: 首条播完=开始完整窗口; 其余=恢复暂停前剩余
  try { await apiClient.post(`/fall-events/${props.eventId}/voice/resume`) } catch (e) { console.warn('[Voice] 服务端倒计时恢复失败:', e.message) }
}

// 重开麦克风: 摄像头麦克风（讯飞 STT）优先，不可用则降级浏览器 Web Speech
function resumeListening() {
  const started = startCameraListening()
  if (!started) {
    console.log('[Voice] 摄像头监听不可用，使用浏览器麦克风识别')
    startRecognition()
  }
}

// [V-fix] 确认语音: 问询已结束（cancel/help/timeout），不 pause/resume 服务端倒计时，仅关麦后播报
async function playConfirmVoice(text) {
  stopCameraListening()                     // 扬声器发声期间关闭摄像头麦克风（防回音）
  try {
    await speakPrompt(text)
  } catch (e) {
    console.warn('[Voice] 确认语音播放失败:', e.message)
  }
}

// [V-fix] 播放确认语音（防重播）：直接在确认动作里触发，不依赖服务端轮询回传的 voiceStatus prop。
// _confirmVoicePlayed 置位后，watch 里的确认语音逻辑会被跳过，避免双播。
function _playConfirmOnce(text) {
  if (_confirmVoicePlayed) return
  _confirmVoicePlayed = true
  playConfirmVoice(text)
}

// 当前播放中的 TTS（用于识别到回应后立即中断后续播报）
let _activePromptSource = null   // AudioBufferSourceNode
let _activePromptAudio = null    // fallback 的 Audio 元素

// 中断当前播报（识别到回应时调用，source.stop() 会触发 onended → resolve）
function stopCurrentPrompt() {
  if (_activePromptSource) {
    try { _activePromptSource.stop() } catch {}
    _activePromptSource = null
  }
  if (_activePromptAudio) {
    try { _activePromptAudio.pause() } catch {}
    _activePromptAudio = null
  }
  if (window.speechSynthesis) {
    try { window.speechSynthesis.cancel() } catch {}
  }
}

// 降级方案：浏览器 TTS 播放（扬声器输出，非摄像头发声）
// 返回"播放完成"Promise（与 speakPrompt 一致，供倒计时顺序控制）
async function fallbackBrowserTTS(text) {
  // 尝试 gTTS mp3 通过浏览器 Audio 标签播放
  const ttsUrl = `/api/voice/tts.mp3?text=${encodeURIComponent(text)}`
  try {
    const audio = new Audio(ttsUrl)
    audio.volume = 1.0
    await audio.play()
    console.log('[Voice] Fallback: TTS played through browser speakers')
    return new Promise((resolve) => {
      _activePromptAudio = audio
      audio.onended = () => { _activePromptAudio = null; resolve() }
      setTimeout(() => { if (_activePromptAudio === audio) { _activePromptAudio = null; resolve() } }, (audio.duration || 5) * 1000 + 2000)  // 兜底
    })
  } catch {
    // 最终降级：浏览器内置 SpeechSynthesis
    if (window.speechSynthesis) {
      return new Promise((resolve) => {
        const u = new SpeechSynthesisUtterance(text)
        u.lang = 'zh-CN'
        u.rate = 0.9
        u.volume = 1.0
        u.onend = resolve
        window.speechSynthesis.speak(u)
        setTimeout(resolve, (text.length / 3) * 1000 + 2000)  // 兜底: 按字数估时
      })
    }
  }
  return Promise.resolve()
}

// === 语音识别 [V7.3 摄像头麦克风优先 + Web Speech 降级] ===
const listening = ref(false)
const interimText = ref('')
const recognizedText = ref('')
const recognizedAction = ref('')
let recognition = null

// --- 摄像头麦克风识别（讯飞 STT） ---
// 录音源: WebRTC 对讲通道的接收轨道（remote audio = 摄像头麦克风）
// 流程: MediaRecorder 录 webm → AudioContext 转 16k PCM → POST /api/voice/stt → 意图分析
let cameraRecorder = null
let cameraChunks = []
let cameraListenTimer = null
let cameraListening = false

function _getCameraMicStream() {
  if (!_talkPC) return null
  const receiver = _talkPC.getReceivers().find(r => r.track?.kind === 'audio')
  if (!receiver?.track) return null
  return new MediaStream([receiver.track])
}

// webm/opus → 16kHz Int16 PCM (ArrayBuffer)
async function webmToPcm16k(blob) {
  const arrayBuffer = await blob.arrayBuffer()
  const ctx = new AudioContext({ sampleRate: 16000 })
  const audioBuffer = await ctx.decodeAudioData(arrayBuffer)
  const data = audioBuffer.getChannelData(0)
  const int16 = new Int16Array(data.length)
  for (let i = 0; i < data.length; i++) {
    const s = Math.max(-1, Math.min(1, data[i]))
    int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF
  }
  ctx.close()
  return int16.buffer
}

// 上传 PCM → 讯飞识别 → 返回文本
async function uploadPcmToStt(pcmBuffer) {
  try {
    const resp = await fetch('/api/voice/stt', {
      method: 'POST',
      headers: { 'Content-Type': 'audio/pcm' },
      body: pcmBuffer,
    })
    const data = await resp.json()
    return data?.data?.transcript || ''
  } catch (e) {
    console.warn('[Voice] STT 上传失败:', e.message)
    return ''
  }
}

// 识别到文本后的统一处理（走 LLM 意图分析 → cancel/help）
async function handleTranscript(transcript) {
  if (!transcript) return
  interimText.value = transcript
  // 已识别到回应 → 立即停止后续播报（第二/三条不再播，正在播的立即打断）
  stopCurrentPrompt()
  // 短文本关键词快判
  const t = transcript.toLowerCase().replace(/\s/g, '')
  if (t.includes('没事') || t.includes('取消') || t.includes('不用')) { stopCameraListening(); confirmCancel(transcript); return }
  if (t.includes('帮我') || t.includes('救命') || t.includes('疼') || t.includes('起不来')) { stopCameraListening(); confirmHelp(transcript); return }
  // 走 LLM 意图分析
  stopCameraListening()
  analyzeWithLLM(transcript)
}

// 启动摄像头麦克风循环监听（每 4s 录一段上传识别，直到有结果或停止）
function startCameraListening() {
  if (cameraListening) return
  const stream = _getCameraMicStream()
  if (!stream) {
    console.warn('[Voice] 摄像头麦克风轨道不可用，降级 Web Speech')
    return false
  }
  cameraListening = true
  console.log('[Voice] 摄像头麦克风监听启动（讯飞 STT）')
  const captureOnce = () => {
    if (!cameraListening) return
    cameraChunks = []
    try {
      cameraRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' })
    } catch {
      cameraRecorder = new MediaRecorder(stream)
    }
    cameraRecorder.ondataavailable = (e) => { if (e.data.size > 0) cameraChunks.push(e.data) }
    cameraRecorder.onstop = async () => {
      if (!cameraListening) return
      try {
        const blob = new Blob(cameraChunks, { type: 'audio/webm' })
        const pcm = await webmToPcm16k(blob)
        const text = await uploadPcmToStt(pcm)
        if (text) {
          console.log('[Voice] 摄像头STT识别:', text)
          handleTranscript(text)
        }
      } catch (e) {
        console.warn('[Voice] 摄像头录音处理失败:', e.message)
      }
      // 循环录制下一段（仍在监听且未结束）
      if (cameraListening) {
        cameraListenTimer = setTimeout(captureOnce, 300)
      }
    }
    cameraRecorder.start()
    cameraListenTimer = setTimeout(() => {
      if (cameraRecorder && cameraRecorder.state === 'recording') cameraRecorder.stop()
    }, 4000)  // 每段 4s
  }
  captureOnce()
  return true
}

function stopCameraListening() {
  cameraListening = false
  if (cameraListenTimer) { clearTimeout(cameraListenTimer); cameraListenTimer = null }
  if (cameraRecorder && cameraRecorder.state === 'recording') {
    try { cameraRecorder.stop() } catch {}
  }
  cameraRecorder = null
  cameraChunks = []
}

function initRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
  if (!SpeechRecognition) {
    console.warn('[Voice] SpeechRecognition not supported')
    return null
  }

  const rec = new SpeechRecognition()
  rec.lang = 'zh-CN'
  rec.interimResults = true
  rec.continuous = true
  rec.maxAlternatives = 1

  rec.onresult = (event) => {
    let transcript = ''
    for (let i = event.resultIndex; i < event.results.length; i++) {
      transcript += event.results[i][0].transcript
    }
    interimText.value = transcript

    // 当语音识别有结果或阶段性结果足够长时，调用 DeepSeek LLM 分析意图
    if (event.results[event.resultIndex].isFinal && transcript.length > 2) {
      interimText.value = transcript
      stopRecognition()
      analyzeWithLLM(transcript)
    }

    // 同时用关键词做快速匹配（等于或超过 3 个字就走 LLM，否则关键词兜底）
    const text = transcript.toLowerCase().replace(/\s/g, '')
    if (text.length < 3) {
      if (text.includes('没事') || text === '取消') { stopRecognition(); confirmCancel() }
      else if (text === '救命' || text === '帮') { stopRecognition(); confirmHelp() }
    }
  }

  rec.onerror = (event) => {
    console.warn('[Voice] Recognition error:', event.error)
    if (event.error === 'not-allowed') {
      ElMessage.info('语音识别需麦克风权限，已降级为手动确认模式。下方按钮可直接操作。')
    }
    // aborted/network/no-speech: 若仍在问询中则自动重启识别（恢复被其他实例打断的情况）
    if (['aborted', 'network', 'no-speech', 'audio-capture'].includes(event.error)) {
      listening.value = false
      if (phase.value === 'active' && !resolved.value) {
        setTimeout(() => {
          if (phase.value === 'active' && !resolved.value && !listening.value) {
            try { rec.start(); listening.value = true } catch {}
          }
        }, 400)
      }
    }
  }

  rec.onend = () => {
    listening.value = false
    // 如果没有识别到结果且还在active阶段，重新开始
    if (phase.value === 'active' && !resolved.value && recognizedAction.value === '') {
      setTimeout(() => {
        if (phase.value === 'active' && !resolved.value && !listening.value) {
          try { rec.start(); listening.value = true } catch {}
        }
      }, 500)
    }
  }

  return rec
}

// === LLM 意图分析 ===

// [V-fix] 没听清 → 摄像头扬声器播放"请再说一次"（期间前后端倒计时暂停），播完恢复监听。
// 重试上限 2 次，超过后提示手动确认。
async function handleUnclearResponse(reason) {
  repeatCount++
  if (repeatCount <= 2) {
    await speakPromptWithPause(REPEAT_PROMPT)
  } else {
    ElMessage.warning('多次未听清 (' + (reason || '') + ')，请手动点击按钮确认')
    resumeListening()
  }
}

async function analyzeWithLLM(transcript) {
  console.log('[Voice] LLM analyzing (非关键词回应):', transcript)
  recognizedText.value = transcript
  recognizedAction.value = ''

  // [V-fix] 非关键词 → 立即播"请再说一次"（不等 LLM，消除 ~5s 延迟）。
  // LLM 改为后台并行：只用于捕捉关键词漏掉的 cancel/help 意图，绝不阻塞播报。
  handleUnclearResponse('非关键词回应')

  try {
    const res = await apiClient.post('/voice/analyze-response', {
      transcript,
      risk_level: props.riskLevel,
      context: `${props.riskLevel}级风险，跌倒事件`
    })
    const analysis = res.data || {}
    console.log('[Voice] LLM result (后台):', analysis)

    // [V-fix] 仅高置信度的 cancel/help 才覆盖关键词漏判（后台 LLM 是"二次确认"，避免误触发紧急联络）
    if (analysis.action === 'cancel' && (analysis.confidence ?? 0) >= 0.7) {
      recognizedAction.value = 'cancel'
      ElNotification({ title: 'LLM 分析: 老人安全', message: analysis.reason, type: 'success', duration: 5000 })
      confirmCancel()
    } else if (analysis.action === 'help' && (analysis.confidence ?? 0) >= 0.7) {
      recognizedAction.value = 'help'
      ElNotification({ title: 'LLM 分析: 需要帮助', message: analysis.reason, type: 'error', duration: 5000 })
      confirmHelp()
    }
    // unclear/no_response 或低置信度 → 已立即播过"请再说一次"，这里不再重复
  } catch (e) {
    console.warn('[Voice] LLM analysis failed, fallback to keyword:', e.message)
    // 降级到关键词匹配
    const text = transcript.toLowerCase()
    if (text.includes('没事') || text.includes('取消')) confirmCancel()
    else if (text.includes('帮') || text.includes('疼') || text.includes('动不了')) confirmHelp()
    // 否则保持"请再说一次"已播状态，不重复
  }
}

function startRecognition() {
  if (!props.enableVoiceRecognition) return
  if (listening.value) return  // 防重入: 识别已在运行时不重复启动
  if (!recognition) {
    recognition = initRecognition()
  }
  if (!recognition) return

  try {
    recognition.start()
    listening.value = true
    console.log('[Voice] Recognition started')
  } catch (e) {
    console.warn('[Voice] Recognition start failed:', e.message)
  }
}

function stopRecognition() {
  stopCameraListening()  // [V7.3] 摄像头麦克风监听（讯飞 STT）优先停止
  stopCurrentPrompt()    // [V7.3] 识别到回应/结束时立即中断播报
  if (recognition) {
    try { recognition.stop() } catch {}
    listening.value = false
  }
}

// === 交互流程 ===

async function startInteraction() {
  starting.value = true
  phase.value = 'active'
  totalSeconds.value = props.countdownSeconds
  remainingSeconds.value = props.countdownSeconds
  resolved.value = false
  recognizedText.value = ''
  recognizedAction.value = ''
  repeatCount = 0
  _resolveVoicePending = ''
  _confirmVoicePlayed = false

  // 1. 播报第一条语音提示（播报期间关摄像头麦克风防回音），播完 → 立即重开麦克风 +
  //    服务端 resume 启动完整倒计时（倒计时在播报完成后开始）
  await speakPromptWithPause(firstPrompt, { first: true })
  console.log('[Voice] 首条提示播报完成，开始聆听与倒计时')

  // 2. 启动倒计时
  startCountdown()

  starting.value = false
}

let countdownPaused = false  // 播报期间暂停倒计时（播报时间不计入回应窗口）

async function startCountdown() {
  clearInterval(countdownTimer)
  countdownPaused = false
  countdownTimer = setInterval(() => {
    if (countdownPaused) return  // "请再说一次"播报中，本 tick 跳过（不减少剩余时间）
    remainingSeconds.value--
    if (remainingSeconds.value <= 0) {
      handleTimeout()
      return
    }
    // [V-fix] 移除 50%/80% 固定重播问询——会在老人回应时抢播/掩盖确认语音。
    // 只播首条；没听清由 handleUnclearResponse 播"请再说一次"，
    // 回应清晰则直接取消/呼叫并播对应确认语音。
  }, 1000)
}

function stopCountdown() {
  clearInterval(countdownTimer)
  countdownTimer = null
  countdownPaused = false
}

async function handleTimeout() {
  stopCountdown()
  stopRecognition()
  window.speechSynthesis.cancel()
  _resolveVoicePending = 'help'   // 超时=自动求助，服务端确认后播"已联系家人"确认语音
  phase.value = 'timeout'
  resolved.value = true
  emit('timeout', { eventId: props.eventId, riskLevel: props.riskLevel })
  // [V-fix] 超时即自动求助，直接播确认语音（不等待兜底 POST）
  _playConfirmOnce('已为您发送求助信息，请保持冷静，家人将尽快联系您。')
  // [V-fix] 兜底同步服务端（服务端定时器是权威源，正常会先到期；此处保证紧急联络不遗漏）
  try { await apiClient.post(`/fall-events/${props.eventId}/voice/timeout`) } catch (e) { console.warn('[Voice] 兜底超时上报失败:', e.message) }
}

async function confirmCancel(transcript = '') {
  if (resolved.value) return
  confirming.value = true
  stopCountdown()
  stopRecognition()
  window.speechSynthesis.cancel()
  if (transcript) recognizedText.value = transcript  // 摄像头 STT 传入识别文本
  _resolveVoicePending = 'cancel'   // 服务端确认 cancelled 后播"已取消"确认语音
  phase.value = 'resolved'
  resolvedAction.value = 'cancel'
  resolved.value = true
  emit('cancelled', { eventId: props.eventId, recognizedText: recognizedText.value, action: 'cancel' })
  // [V-fix] 立即播确认语音（不等待服务端轮询回传）
  _playConfirmOnce('好的，已为您取消告警，请放心休息。')
  confirming.value = false
}

async function confirmHelp(transcript = '') {
  if (resolved.value) return
  confirming.value = true
  stopCountdown()
  stopRecognition()
  window.speechSynthesis.cancel()
  if (transcript) recognizedText.value = transcript  // 摄像头 STT 传入识别文本
  _resolveVoicePending = 'help'    // 服务端确认 help_requested 后播"已联系家人"确认语音
  phase.value = 'resolved'
  resolvedAction.value = 'help'
  resolved.value = true
  emit('emergency', { eventId: props.eventId, recognizedText: recognizedText.value, action: 'help' })
  // [V-fix] 立即播确认语音（不等待服务端轮询回传）
  _playConfirmOnce('已为您发送求助信息，请保持冷静，家人将尽快联系您。')
  confirming.value = false
}

async function triggerEmergency() {
  emergencyTriggering.value = true
  emit('emergency', { eventId: props.eventId, skipped: true })
  emergencyTriggering.value = false
}

function resetInteraction() {
  stopCountdown()
  stopRecognition()
  window.speechSynthesis.cancel()
  phase.value = 'idle'
  resolvedAction.value = ''
  resolved.value = false
  recognizedText.value = ''
  interimText.value = ''
  remainingSeconds.value = props.countdownSeconds
  repeatCount = 0
  _resolveVoicePending = ''
  _confirmVoicePlayed = false
}

// 每次countdownSeconds变化时重置
watch(() => props.countdownSeconds, (val) => {
  totalSeconds.value = val
  remainingSeconds.value = val
})

// [V7.3] 自动启动: 事件处于问询中（autoStart && 服务端 pending）时自动开始
watch(
  () => props.autoStart && props.voiceStatus === 'pending',
  (v) => {
    if (v && phase.value === 'idle' && props.countdownSeconds > 0) {
      startInteraction()
    }
  },
  { immediate: true },
)

// [V7.3] 服务端状态同步: 页面晚开/状态已结束时停止本地流程并显示终态
watch(() => props.voiceStatus, (vc) => {
  if (!vc || vc === 'pending') return
  const wasActive = phase.value === 'active'
  stopCountdown()
  stopRecognition()
  window.speechSynthesis.cancel()
  if (vc === 'timeout') {
    phase.value = 'timeout'
    resolved.value = true
  } else if (vc === 'cancelled' || vc === 'confirmed_cancel') {
    phase.value = 'resolved'
    resolvedAction.value = 'cancel'
    resolved.value = true
  } else if (vc === 'help_requested' || vc === 'confirmed_help') {
    phase.value = 'resolved'
    resolvedAction.value = 'help'
    resolved.value = true
  }
  // [V-fix] 确认语音: 本地刚确认 / 问询中被服务端结束（超时/求助）→ 摄像头扬声器播报确认。
  // wasActive 守卫: 页面加载一个"已结束"事件时 phase='idle'，不重播。
  if (!_confirmVoicePlayed && (wasActive || _resolveVoicePending)) {
    _confirmVoicePlayed = true
    const isCancel = vc === 'cancelled' || vc === 'confirmed_cancel'
    playConfirmVoice(isCancel ? '好的，已为您取消告警，请放心休息。'
                              : '已为您发送求助信息，请保持冷静，家人将尽快联系您。')
  }
})

// [2026-08-03] 挂载即预连接摄像头音频通道（大幅缩短首播延迟）
onMounted(preconnect)

onBeforeUnmount(() => {
  stopCountdown()
  stopRecognition()
  window.speechSynthesis.cancel()
  destroyEzPlayer()
})
</script>

<style scoped>
.voice-interaction {
  border-radius: 12px;
  padding: 24px;
  min-height: 200px;
}
.voice-interaction.risk-I { background: linear-gradient(135deg, #fef0f0, #fde2e2); border: 2px solid #f56c6c; }
.voice-interaction.risk-II { background: linear-gradient(135deg, #fdf6ec, #faecd8); border: 2px solid #e6a23c; }
.voice-interaction.risk-III { background: linear-gradient(135deg, #f0f9eb, #e1f3d8); border: 2px solid #67c23a; }

/* idle */
.vi-idle { text-align: center; }
.vi-ready { display: flex; flex-direction: column; align-items: center; gap: 12px; }
.vi-ready h3 { margin: 0; font-size: 18px; color: #303133; }
.vi-ready p { margin: 0; color: #909399; font-size: 14px; }

/* active */
.vi-active { display: flex; flex-direction: column; align-items: center; gap: 16px; }
.countdown-ring { margin: 8px 0; }
.cd-number { font-size: 36px; font-weight: bold; color: #303133; }
.cd-unit { font-size: 14px; color: #909399; }

.speech-bubble {
  display: flex; align-items: flex-start; gap: 8px;
  padding: 12px 16px; background: #fff; border-radius: 10px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.08); max-width: 500px; font-size: 14px;
}

.listen-status {
  text-align: center;
  min-height: 56px;               /* 固定高度，防止标签/波形切换导致窗口上下跳动 */
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
}
.listen-wave { display: flex; gap: 3px; justify-content: center; margin-bottom: 8px; }
.wave-bar {
  width: 4px; height: 14px; background: #409eff; border-radius: 2px;
  /* 静态波形，不做动画（用户反馈动画导致界面抖动感） */
}

.manual-buttons { display: flex; gap: 16px; margin-top: 8px; }
.manual-hint { text-align: center; color: #909399; font-size: 12px; margin: 8px 0 0; }

/* camera status */
.camera-status { margin-top: 12px; padding: 8px 12px; background: rgba(255,255,255,0.6); border-radius: 8px; }
.status-row { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #606266; }
.dot { width: 8px; height: 8px; border-radius: 50%; }
.dot.green { background: #67c23a; animation: pulse-dot 2s infinite; }
.dot.yellow { background: #e6a23c; animation: pulse-dot 1.5s infinite; }
@keyframes pulse-dot { 0%,100%{opacity:1} 50%{opacity:0.3} }
.status-tips { font-size: 12px; color: #909399; margin-top: 4px; display: flex; align-items: center; gap: 4px; }

/* resolved */
.vi-resolved, .vi-timeout { text-align: center; }
</style>
