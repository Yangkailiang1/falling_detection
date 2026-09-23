// 告警记录列表
const app = getApp()
const { resolveUrl, toLocalImageUrl } = require('../../utils/asset')

Page({
  data: {
    events: [],
    loading: false,
  },

  onShow() { this.load() },
  onPullDownRefresh() { this.load(() => wx.stopPullDownRefresh()) },

  load(done) {
    this.setData({ loading: true })
    wx.request({
      url: `${app.globalData.apiBase}/fall-events?limit=20&offset=0&days=30`,
      success: (res) => {
        const list = (res.data && res.data.data && res.data.data.events) || []
        const events = list.map(e => ({
          ...e,
          created_at_display: this.formatTime(e.created_at),
          // 本地持久化图片是相对路径, 需拼接 apiBase 根 [V9.4]
          capture_pic_url: resolveUrl(e.capture_pic_url),
        }))
        this.setData({ events, loading: false })
        // 黑图修复: 逐项把抓拍图转本地临时路径, <image> 直接渲染本地文件 [2026-08-12]
        events.forEach((e, idx) => {
          toLocalImageUrl(e.capture_pic_url, (local) => {
            if (local && local !== e.capture_pic_url) {
              this.setData({ [`events[${idx}].capture_pic_url`]: local })
            }
          })
        })
      },
      fail: () => {
        this.setData({ loading: false })
        wx.showToast({ title: '加载失败', icon: 'none' })
      },
      complete: () => done && done(),
    })
  },

  formatTime(iso) {
    if (!iso) return ''
    const d = new Date(iso)
    const p = (n) => String(n).padStart(2, '0')
    return `${d.getMonth() + 1}/${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`
  },

  statusText(status) {
    const map = {
      archived: '已归档', notified: '已通知', inquiring: '问询中',
      voice_cancelled: '已取消', detected: '已检测',
    }
    return map[status] || status || ''
  },

  goDetail(e) {
    wx.navigateTo({ url: `/pages/events/detail?id=${e.currentTarget.dataset.id}` })
  }
})
