// 事件详情 — 展示嵌套结构 (risk/skeleton_analysis/context/medical_report)
const app = getApp()
const { resolveUrl, toLocalImageUrl, base64ToLocalImage } = require('../../utils/asset')

Page({
  data: { event: null, eventId: '', imgError: false },

  onLoad(options) {
    this.setData({ eventId: options.id || '' })
    this.load()
  },

  load() {
    const { eventId } = this.data
    if (!eventId) return
    wx.request({
      url: `${app.globalData.apiBase}/fall-events/${eventId}`,
      success: (res) => {
        const e = (res.data && res.data.data) || null
        if (e) {
          e.created_at_display = this.formatTime(e.created_at)
          // 图片字段在顶层 (V9.3 统一嵌套结构); 本地持久化图片是相对路径需拼接 [V9.4]
          const imgUrl = resolveUrl(e.capture_pic_url)
          const imgBase64 = e.capture_pic_base64 || ''
          delete e.capture_pic_base64
          e.capture_pic_url = imgUrl
          e.capture_img_src = ''
          e.touch_ground_label = this.partLabel(e.skeleton_analysis && e.skeleton_analysis.touch_ground_part)
          e.medical_text = (e.medical_report && e.medical_report.full_text) || ''
          e.medical_recommend = (e.medical_report && e.medical_report.recommendation) || ''
          // 医疗简报 Markdown 加粗 → rich-text 可渲染的 HTML [V9.5]
          e.medical_html = this.toRichText(e.medical_text)
          this.setData({ event: e, imgError: false })
          const useNetworkFallback = () => toLocalImageUrl(imgUrl, (local) => {
            if (local) this.setData({ 'event.capture_img_src': local, imgError: false })
            else this.setData({ imgError: true })
          })
          // 真机优先使用详情接口内嵌图片，避免局域网 HTTP 被 <image> 单独拦截。
          if (imgBase64) {
            base64ToLocalImage(imgBase64, e.event_id, (local) => {
              if (local) this.setData({ 'event.capture_img_src': local, imgError: false })
              else useNetworkFallback()
            })
          } else useNetworkFallback()
        }
      },
      fail: () => wx.showToast({ title: '加载失败', icon: 'none' })
    })
  },

  formatTime(iso) {
    if (!iso) return ''
    const d = new Date(iso)
    const p = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}/${p(d.getMonth()+1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
  },

  // 医疗简报 Markdown → rich-text HTML (转义 + **粗体** → <strong> + 换行 → <br>) [V9.5]
  toRichText(text) {
    if (!text) return ''
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\n/g, '<br>')
  },

  partLabel(p) {
    return ({ head: '头部', spine: '脊柱', hip: '髋部', shoulder: '肩部', hand: '手部', elbow: '肘部', knee: '膝部' }[p] || p || '未知')
  },

  // 预览现场图片
  previewImage() {
    const url = this.data.event && this.data.event.capture_pic_url
    if (!url) return
    wx.previewImage({ urls: [url], current: url })
  },

  // 抓拍图加载失败 → 显示兜底提示 (黑图修复的观测点) [2026-08-12]
  onCaptureError() {
    this.setData({ imgError: true })
  },

  goCamera() {
    const serial = wx.getStorageSync('deviceSerial') || (this.data.event && this.data.event.context.device_serial) || ''
    wx.navigateTo({ url: `/pages/camera/camera?serial=${serial}` })
  },

  // 现场画面 (抓拍图 + 视频回放)
  goMedia() {
    wx.navigateTo({ url: `/pages/events/media?id=${this.data.eventId}` })
  },

  confirmEvent() {
    this.submitFeedback('已由家属确认处理')
  },

  markFalseAlarm() {
    wx.showModal({
      title: '标记为误报',
      content: '确认这是一次误报吗？',
      success: (res) => {
        if (res.confirm) this.submitFeedback('家属标记为误报')
      }
    })
  },

  submitFeedback(comment) {
    wx.request({
      url: `${app.globalData.apiBase}/fall-events/${this.data.eventId}/feedback`,
      method: 'POST',
      data: { is_false_alarm: comment.includes('误报'), comment },
      success: (res) => {
        if (res.data && res.data.success) {
          wx.showToast({ title: '已提交', icon: 'success' })
          this.load()
        } else {
          wx.showToast({ title: '提交失败', icon: 'none' })
        }
      },
      fail: () => wx.showToast({ title: '网络错误', icon: 'none' })
    })
  }
})
