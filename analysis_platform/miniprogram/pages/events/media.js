// 现场画面 — 事件抓拍图 + 视频回放 (与企业微信推送内容一致)
const app = getApp()
const { resolveUrl, toLocalImageUrl } = require('../../utils/asset')

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
          // 图片/视频相对路径统一拼接 apiBase 根 [V9.4]
          const imgUrl = resolveUrl(e.capture_pic_url)
          e.capture_pic_url = imgUrl
          e.video_url = resolveUrl(e.video_url)
          this.setData({ event: e })
          // 黑图修复: 用 wx.getImageInfo 取本地临时路径, <image> 直接渲染本地文件 [2026-08-12]
          toLocalImageUrl(imgUrl, (local) => {
            if (local && local !== imgUrl) this.setData({ 'event.capture_pic_url': local })
          })
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

  previewImage() {
    const url = this.data.event && this.data.event.capture_pic_url
    if (!url) return
    wx.previewImage({ urls: [url], current: url })
  },

  // 抓拍图加载失败 → 显示兜底提示 (黑图修复的观测点) [2026-08-12]
  onCaptureError() {
    this.setData({ imgError: true })
  },

  // 查看实时画面 (萤石半屏播放器, 含对讲)
  goLive() {
    const serial = wx.getStorageSync('deviceSerial') || (this.data.event && this.data.event.context.device_serial) || ''
    wx.navigateTo({ url: `/pages/camera/camera?serial=${serial}` })
  },

  goBack() {
    wx.navigateBack()
  }
})
