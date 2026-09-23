#!/usr/bin/env node
/**
 * 无头语音客户端 (Puppeteer 方案)
 * ==============================
 * 无人值守地驱动摄像头语音问询：
 *   检测到跌倒事件(inquiring) → 摄像头扬声器播问询(edge-tts 注入对讲通道)
 *   → 摄像头麦克风 STT(讯飞) → 分类 cancel/help/未听清 → 调后端 voice/confirm
 * 复用 ezuikit-js 对讲通道（与浏览器 VoiceInteraction 同源）。
 *
 * 启动: node headless_voice.js
 * 依赖: npm i puppeteer（首次会下载 Chromium）
 */
const puppeteer = require('puppeteer')
const path = require('path')
const fs = require('fs')
const crypto = require('crypto')

const API_BASE = process.env.API_BASE || 'http://127.0.0.1:5001'
const EZUIKIT_JS = process.env.EZUIKIT_JS ||
  path.resolve(__dirname, '..', 'frontend', 'node_modules', 'ezuikit-js', 'ezuikit.js')
const TALK_HTML = path.join(__dirname, 'talk.html')
const POLL_MS = 100
const EXECUTOR_ID = `camera-webrtc-${process.pid}-${crypto.randomUUID()}`

const FIRST_PROMPT = '检测到您摔倒了，请问是否需要呼叫家人？需要帮助请说"帮我呼叫"，如果没事请说"我没事"。'
const REPEAT_PROMPT = '请再说一次，您可以试着说"我没事"或"帮我呼叫"。'
const CANCEL_VOICE = '好的，已为您取消告警，请放心休息。'
const HELP_VOICE = '已为您发送求助信息，请保持冷静，家人将尽快联系您。'

let browser, page
const handled = new Set()
const feedbackHandled = new Set()
let inquiryActive = false  // [2026-08-13] 问询进行中标志: keepalive 重建对讲通道时跳过, 避免打断播报

async function api(pathname, opts = {}) {
  const res = await fetch(`${API_BASE}${pathname}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  })
  return res.json().catch(() => ({}))
}

async function classify(transcript) {
  const t = (transcript || '').toLowerCase().replace(/\s+/g, '')
  if (t.includes('没事') || t.includes('取消') || t.includes('不用')) return 'cancel'
  if (t.includes('帮我') || t.includes('救命') || t.includes('疼') || t.includes('起不来') ||
      t.includes('帮助') || t.includes('呼叫')) return 'help'
  return 'unclear'
}

// 所有播报统一走持续的 WebRTC 对讲通道，等待真实 onended 后才允许
// 开启摄像头麦克风，确保摄像头扬声器和麦克风严格半双工。
async function playPrompt(text) {
  let last = { ok: false, error: '摄像头 WebRTC 播报失败' }
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      const result = await page.evaluate(async (value) => window.__voice.playTts(value), text)
      if (result?.ok && result?.audioSent) {
        console.log('[headless] 摄像头播报确认: attempt=' + attempt + ' duration=' + (result.duration || 0) + 's packets=' + (result.packetsSent || 0))
        await new Promise((resolve) => setTimeout(resolve, 200))
        return result
      }
      last = result || last
      console.warn('[headless] 摄像头 WebRTC 播报失败 attempt=' + attempt + ': ' + (last.error || '未确认音频发送'))
    } catch (err) {
      last = { ok: false, error: err.message }
      console.warn('[headless] 摄像头 WebRTC 播报异常 attempt=' + attempt + ': ' + err.message)
    }
    if (attempt < 2) await new Promise((resolve) => setTimeout(resolve, 800))
  }
  return { ok: false, error: last.error || '摄像头 WebRTC 播报失败' }
}

// 严格串行播放：无论消息来自问询、求助确认还是超时回调，都不能并发
// 操作同一个摄像头扬声器。
let playbackQueue = Promise.resolve()
function enqueuePrompt(text) {
  const job = playbackQueue.then(() => playPrompt(text))
  playbackQueue = job.catch(() => {})
  return job
}

function withTimeout(promise, ms, fallback = '') {
  let timer
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => resolve(fallback), ms)
  })
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer))
}

async function playFeedback(event) {
  const id = event.event_id
  if (!event.voice_feedback_text || event.voice_feedback_played || feedbackHandled.has(id)) return false
  const claim = await api(`/api/fall-events/${id}/voice/feedback/claim`, {
    method: 'POST', body: JSON.stringify({ executor_id: EXECUTOR_ID }),
  }).catch(() => ({}))
  const text = claim?.data?.text || claim?.text || ''
  if (!text || !(claim?.data?.claimed ?? claim?.claimed)) return false
  feedbackHandled.add(id)
  await enqueuePrompt(text)
  await api(`/api/fall-events/${id}/voice/feedback/ack`, {
    method: 'POST', body: JSON.stringify({ executor_id: EXECUTOR_ID }),
  }).catch(() => {})
  return true
}

async function driveInquiry(event) {
  const id = event.event_id
  console.log(`[headless] 开始问询 ${id} risk=${event.risk_level || '?'} (${new Date().toISOString()})`)
  const claim = await api(`/api/fall-events/${id}/voice/claim`, {
    method: 'POST', body: JSON.stringify({ executor_id: EXECUTOR_ID }),
  }).catch(() => ({}))
  if (!(claim?.data?.claimed ?? claim?.claimed)) {
    console.log(`[headless] ${id} 已被其他语音执行器认领`)
    // 认领失败不应永久写入 handled：另一个执行器可能已崩溃，
    // 下一轮轮询应在认领租约过期后重试，避免事件一直等到超时。
    handled.delete(id)
    return
  }
  // 1. 首条问询播报（真实 onended 后 resume 启动服务端完整倒计时）
  const p1 = await enqueuePrompt(FIRST_PROMPT)
  if (!p1?.ok) {
    console.warn('[headless] 首条播报失败，结束问询并触发安全通知: ' + (p1?.error || ''))
    await api('/api/fall-events/' + id + '/voice/failure', {
      method: 'POST',
      body: JSON.stringify({ detail: p1?.error || '摄像头首句问询未确认发送' }),
    }).catch(() => {})
    return
  }
  await api('/api/fall-events/' + id + '/voice/resume', { method: 'POST' }).catch(() => {})

  // 2. 监听回应，最多 3 轮（未听清→暂停倒计时→再问→恢复）
  for (let attempt = 0; attempt <= 2; attempt++) {
    if (attempt > 0) {
      await api(`/api/fall-events/${id}/voice/pause`, { method: 'POST' }).catch(() => {})
      await enqueuePrompt(REPEAT_PROMPT)
      await api(`/api/fall-events/${id}/voice/resume`, { method: 'POST' }).catch(() => {})
    }
    const transcript = await withTimeout(
      page.evaluate(async () => window.__voice.transcribe(3)),
      7500,
      '',
    ).catch(() => '')
    console.log(`[headless] 识别[${attempt}]: "${transcript}"`)
    const action = await classify(transcript)
    if (action === 'cancel') {
      const confirmed = await api(`/api/fall-events/${id}/voice/confirm`, { method: 'POST', body: JSON.stringify({ action: 'cancel', recognized_text: transcript }) })
      if (confirmed?.data?.voice_confirm_status === 'cancelled') await playFeedback({ event_id: id, voice_feedback_text: CANCEL_VOICE })
      console.log(`[headless] ${id} → 取消/误报`)
      return
    }
    if (action === 'help') {
      const confirmed = await api(`/api/fall-events/${id}/voice/confirm`, { method: 'POST', body: JSON.stringify({ action: 'help', recognized_text: transcript }) })
      if (confirmed?.data?.voice_confirm_status === 'help_requested') await playFeedback({ event_id: id, voice_feedback_text: HELP_VOICE })
      console.log(`[headless] ${id} → 求助`)
      return
    }
  }
  // 3. 多次未听清 → 当前倒计时已经在最后一轮监听前恢复，交给超时派发
  console.log(`[headless] ${id} 多次未听清, 交由服务端超时`)
}

async function main() {
  console.log('[headless] 启动无头语音客户端...')
  // 开发机可能只安装系统 Chrome，没有 Puppeteer 自带的 Chromium。
  // 显式使用系统浏览器仍保持 headless WebRTC 对讲链路，不改变摄像头音频路径。
  const chromePath = process.env.CHROME_PATH ||
    (process.platform === 'win32' ? 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' : '')
  browser = await puppeteer.launch({
    headless: 'new',
    ...(chromePath ? { executablePath: chromePath } : {}),
    args: [
      '--no-sandbox', '--disable-setuid-sandbox',
      '--autoplay-policy=no-user-gesture-required',
      '--use-fake-ui-for-media-stream',  // 自动允许麦克风（本地 mic 不用, 只用于 startTalk 建立通道）
      '--disable-gpu',
    ],
  })
  page = await browser.newPage()
  try { await page.grantPermissions(['microphone'], { origin: new URL(API_BASE).origin }) } catch {}

  // 从后端伺服的同源页面加载（页面源=后端 → 无 CORS）
  await page.goto(`${API_BASE}/headless-voice`, { waitUntil: 'domcontentloaded' })
  if (!fs.existsSync(EZUIKIT_JS)) {
    throw new Error(`找不到 ezuikit.js: ${EZUIKIT_JS}。请先在 analysis_platform/frontend 执行 npm ci`)
  }
  const ezuikit = fs.readFileSync(EZUIKIT_JS, 'utf8')
  await page.addScriptTag({ content: ezuikit })

  // apiBase='' → 同源 fetch（页面由后端伺服）
  const init = await page.evaluate(async (b) => window.__voice.init({ apiBase: b || '' }), API_BASE === 'http://127.0.0.1:5001' ? '' : API_BASE)
  console.log('[headless] 对讲通道:', JSON.stringify(init))
  if (!init.ok) {
    console.error('[headless] 对讲通道建立失败, 退出')
    await browser.close()
    process.exit(1)
  }

  // 在已经建立的浏览器页面内预热 TTS，避免 Node 24 的 undici 直接读取
  // Flask 音频响应时触发 HTTP 流解析异常；实际播报仍由 page.playTts
  // 经 WebRTC 对讲音轨发送到摄像头扬声器。
  await page.evaluate(async (texts) => {
    for (const text of texts) {
      try {
        const response = await fetch('/api/voice/tts.mp3?text=' + encodeURIComponent(text))
        if (response.ok) await response.arrayBuffer()
      } catch (error) {
        console.warn('[voice] TTS 预热失败，将在播报时重试:', error?.message || error)
      }
    }
  }, [FIRST_PROMPT, REPEAT_PROMPT, CANCEL_VOICE, HELP_VOICE])

  console.log('[headless] 就绪, 轮询跌倒事件...')

  // 启动时把历史事件加入基线，避免服务重启后重新问询旧事件。
  // 此后只处理本次演示中新产生且仍处于 pending 的问询事件。
  try {
    const initial = await api('/api/fall-events')
    for (const e of initial?.data?.items || []) {
      // 服务重启时恢复唯一活动问询；已结束事件才进入历史基线。
      const pendingInquiry = e.status === 'inquiring' &&
        e.response?.voice_confirm_status === 'pending'
      if (!pendingInquiry) handled.add(e.event_id)
      if (!e.voice_feedback_text || e.voice_feedback_played) {
        feedbackHandled.add(e.event_id)
      }
    }
    console.log(`[headless] 历史事件基线: ${handled.size} 条`)
  } catch (err) {
    console.warn('[headless] 历史事件基线读取失败:', err.message)
  }

  // [2026-08-13] 对讲通道保活: 每 2min ensureTalk 一次(8min 过期强制重建),
  // 防止 EZVIZ 对讲通道 20min 闲置超时导致播报/STT 失效。问询进行中跳过。
  setInterval(async () => {
    if (inquiryActive || !page) return
    try {
      const r = await page.evaluate(async () => window.__voice.ensureTalk())
      if (r && !r.ok) console.warn(`[headless] keepalive ensureTalk: ${r.error || 'failed'}`)
    } catch (err) {
      console.warn('[headless] keepalive err:', err.message)
    }
  }, 2 * 60 * 1000)

  while (true) {
    try {
      const res = await api('/api/fall-events')
      const items = res?.data?.items || []
      for (const e of items) {
        if (inquiryActive) break
        if (e.status === 'inquiring' && e.response?.voice_confirm_status === 'pending' && !handled.has(e.event_id)) {
          handled.add(e.event_id)
          inquiryActive = true
          driveInquiry(e).finally(() => { inquiryActive = false }).catch(err => console.error(`[headless] 问询异常 ${e.event_id}:`, err.message))
        } else if (!inquiryActive && e.voice_feedback_text && !e.voice_feedback_played && !feedbackHandled.has(e.event_id)) {
          // 超时可能由服务端定时器触发；在当前问询完全结束后，再由同一
          // 播放队列播报终句，避免旧线程抢占首句。
          playFeedback(e).catch(err => console.error(`[headless] 反馈播报异常 ${e.event_id}:`, err.message))
        }
      }
    } catch (err) {
      console.error('[headless] 轮询错误:', err.message)
    }
    await new Promise((r) => setTimeout(r, POLL_MS))
  }
}

main().catch((e) => { console.error('[headless] fatal:', e.message); process.exit(1) })

