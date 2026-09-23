<!--
  EZUIKit 视频播放器组件 — 使用萤石官方 ezuikit-js SDK
-->
<template>
  <div class="video-player-wrapper">
    <div v-if="errorMsg" class="video-error">
      <el-icon :size="32"><WarningFilled /></el-icon>
      <p>{{ errorMsg }}</p>
    </div>
    <div v-if="loading && !errorMsg" class="video-loading">
      <el-icon class="is-loading" :size="32"><Loading /></el-icon>
      <p>正在连接摄像头...</p>
    </div>
    <div :id="containerId" class="ezuikit-container"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { Loading, WarningFilled } from '@element-plus/icons-vue'
import { EZUIKitPlayer } from 'ezuikit-js'

let uid = 0
const containerId = ref(`ezuikit-${Date.now()}-${++uid}`)

const props = defineProps({
  deviceSerial: { type: String, required: true },
  accessToken: { type: String, required: true },
  width: { type: Number, default: 800 },
  height: { type: Number, default: 450 },
})

const emit = defineEmits(['streamInfo', 'recordingState', 'talkingState', 'soundState'])

const loading = ref(true)
const errorMsg = ref('')

// SDK API 状态
const isRecording = ref(false)
const isTalking = ref(false)
const isSoundOn = ref(false)

let player = null

function playerGuard() {
  if (!player) {
    console.warn('[EZUIKit] 播放器未初始化，忽略操作')
    return false
  }
  return true
}

function destroy() {
  if (player) {
    try { player.stop() } catch {}
    try { player.destroy() } catch {}
    player = null
  }
}

function initPlayer() {
  if (!props.accessToken || !props.deviceSerial) return
  destroy()

  const el = document.getElementById(containerId.value)
  if (el) el.innerHTML = ''

  loading.value = true
  errorMsg.value = ''

  try {
    player = new EZUIKitPlayer({
      id: containerId.value,
      accessToken: props.accessToken,
      url: `ezopen://open.ys7.com/${props.deviceSerial}/1.hd.live`,
      template: 'pcLive',
      width: props.width,
      height: props.height,
      scaleMode: 0,  // 铺满无黑边
      audio: 1,
      decoderType: 'v3',
      plugin: ['talk'],
      handleError: (err) => {
        console.error('[EZUIKit]', err)
        const msg = err?.data?.msg || err?.message || '播放失败'
        errorMsg.value = msg
        loading.value = false
      }
    })

    player.on(EZUIKitPlayer.EVENTS.firstFrameDisplay, () => {
      loading.value = false
      // audio: 1 已配置，记录声音状态
      isSoundOn.value = true
      emit('soundState', true)
    })

    // 监听并向上 emit 实际流参数 — 每次变化都通知父组件
    let _video = null, _audio = null

    const emitStreamInfo = () => {
      emit('streamInfo', { video: _video, audio: _audio })
    }

    player.on(EZUIKitPlayer.EVENTS.videoInfo, (info) => {
      _video = info
      console.log('[EZUIKit] 视频信息:', info)
      emitStreamInfo()
    })
    player.on(EZUIKitPlayer.EVENTS.audioInfo, (info) => {
      _audio = info
      console.log('[EZUIKit] 音频信息:', info)
      if (_video) emitStreamInfo()
    })
    // 清晰度切换时重置，等待新流参数
    player.on(EZUIKitPlayer.EVENTS.changeVideoLevel, (info) => {
      console.log('[EZUIKit] 清晰度切换:', info?.data || info)
      _video = null
      _audio = null
      emit('streamInfo', { video: null, audio: null })
    })

    // SDK API 事件：录制状态
    player.on(EZUIKitPlayer.EVENTS.startSave, () => {
      isRecording.value = true
      emit('recordingState', true)
    })
    player.on(EZUIKitPlayer.EVENTS.stopSave, () => {
      isRecording.value = false
      emit('recordingState', false)
    })
    // 对讲状态
    player.on(EZUIKitPlayer.EVENTS.startTalk, () => {
      isTalking.value = true
      emit('talkingState', true)
    })
    player.on(EZUIKitPlayer.EVENTS.stopTalk, () => {
      isTalking.value = false
      emit('talkingState', false)
    })
    // 声音状态
    player.on(EZUIKitPlayer.EVENTS.openSound, () => {
      isSoundOn.value = true
      emit('soundState', true)
    })
    player.on(EZUIKitPlayer.EVENTS.closeSound, () => {
      isSoundOn.value = false
      emit('soundState', false)
    })
  } catch (e) {
    errorMsg.value = '播放器初始化失败: ' + e.message
    loading.value = false
  }
}

watch(() => [props.deviceSerial, props.accessToken], () => {
  nextTick(() => initPlayer())
})

onMounted(() => {
  nextTick(() => {
    if (props.deviceSerial && props.accessToken) initPlayer()
  })
})

onBeforeUnmount(() => {
  destroy()
})

// ===== 对外暴露的 SDK API (父组件通过 ref 调用) =====

function capturePicture(name) {
  if (!playerGuard()) return
  const filename = name || `capture-${Date.now()}`
  player.capturePicture(filename, (data) => {
    console.log('[EZUIKit] 截图完成:', filename)
    // 可以触发下载或通过 emit 传 base64 给父组件
  })
}

function startSave(name) {
  if (!playerGuard()) return
  const filename = name || `record-${Date.now()}`
  player.startSave(filename)
  console.log('[EZUIKit] 开始录制:', filename)
}

function stopSave() {
  if (!playerGuard()) return
  player.stopSave()
  console.log('[EZUIKit] 停止录制')
}

function startTalk() {
  if (!playerGuard()) return
  player.startTalk()
  console.log('[EZUIKit] 开始对讲')
}

function stopTalk() {
  if (!playerGuard()) return
  player.stopTalk()
  console.log('[EZUIKit] 停止对讲')
}

function openSound() {
  if (!playerGuard()) return
  player.openSound()
  console.log('[EZUIKit] 开启声音')
}

function closeSound() {
  if (!playerGuard()) return
  player.closeSound()
  console.log('[EZUIKit] 关闭声音')
}

function getMicrophonePermission() {
  if (!playerGuard()) return Promise.reject('player not ready')
  return player.getMicrophonePermission()
}

defineExpose({
  capturePicture,
  startSave,
  stopSave,
  startTalk,
  stopTalk,
  openSound,
  closeSound,
  getMicrophonePermission,
  isRecording,
  isTalking,
  isSoundOn,
})
</script>

<style scoped>
.video-player-wrapper {
  position: relative;
  background: #000;
  display: flex;
  justify-content: center;
}
.ezuikit-container {
  /* SDK 自行管理尺寸，父 flex 居中 */
}
.ezuikit-container :deep(canvas),
.ezuikit-container :deep(video) {
  display: block;
}
.video-loading, .video-error {
  position: absolute;
  top: 0; left: 0; right: 0; bottom: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #909399;
  gap: 12px;
  z-index: 3;
  background: #000;
}
.video-error { color: #f56c6c; }
</style>
