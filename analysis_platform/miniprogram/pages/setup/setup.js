// 接入向导页 — 3步: 连接摄像头 → 配置告警 → 接入完成+历史告警
const app = getApp()
const { resolveUrl, toLocalImageUrl } = require('../../utils/asset')

Page({
  data: {
    step: 1,
    deviceSerial: '',
    validateCode: '',
    wecomKey: '',
    connecting: false,
    testing: false,
    deviceOk: false,
    deviceErr: '',
    wecomOk: false,
    wecomErr: '',
    subscribed: false,
    events: [],
  },

  onLoad() {
    // 预填演示值 (真实值由后端从 .env 读取)
    wx.request({
      url: `${app.globalData.apiBase}/mini/setup/prefill`,
      success: (res) => {
        const d = (res.data && res.data.data) || {}
        this.setData({
          deviceSerial: d.device_serial || '',
          validateCode: d.validate_code || '',
          wecomKey: d.wecom_webhook_key || '',
        })
      }
    })
    this.setData({ subscribed: !!wx.getStorageSync('subscribed') })
  },

  onSerial(e) { this.setData({ deviceSerial: e.detail.value }) },
  onValidateCode(e) { this.setData({ validateCode: e.detail.value }) },
  onWecomKey(e) { this.setData({ wecomKey: e.detail.value }) },

  // 第1步: 连接摄像头 + 验证取流
  connectDevice() {
    const { deviceSerial, validateCode } = this.data
    if (!deviceSerial) { wx.showToast({ title: '请输入设备序列号', icon: 'none' }); return }
    if (!validateCode) { wx.showToast({ title: '请输入设备验证码', icon: 'none' }); return }
    this.setData({ connecting: true, deviceErr: '', deviceOk: false })
    wx.request({
      url: `${app.globalData.apiBase}/mini/device/add`,
      method: 'POST',
      data: { device_serial: deviceSerial.trim(), validate_code: validateCode.trim() },
      success: (res) => {
        if (res.data && res.data.success) {
          wx.setStorageSync('deviceSerial', deviceSerial.trim())
          this.setData({ deviceOk: true, connecting: false })
          setTimeout(() => this.enterStep(2), 1200)
        } else {
          this.setData({ deviceErr: (res.data && res.data.message) || '接入失败', connecting: false })
        }
      },
      fail: () => this.setData({ deviceErr: '网络错误，请检查后端服务', connecting: false })
    })
  },

  // 第2步: 发送测试消息到企业微信群
  sendTest() {
    const { wecomKey } = this.data
    if (!wecomKey) { wx.showToast({ title: '请先填写企业微信 webhook', icon: 'none' }); return }
    this.setData({ testing: true, wecomErr: '', wecomOk: false })
    wx.request({
      url: `${app.globalData.apiBase}/mini/wecom/test`,
      method: 'POST',
      data: { webhook_key: wecomKey.trim() },
      success: (res) => {
        if (res.data && res.data.success) {
          wx.setStorageSync('wecomKey', wecomKey.trim())
          this.setData({ wecomOk: true, testing: false })
          setTimeout(() => this.enterStep(3), 1500)
        } else {
          this.setData({ wecomErr: (res.data && res.data.message) || '发送失败', testing: false })
        }
      },
      fail: () => this.setData({ wecomErr: '网络错误，请检查后端服务', testing: false })
    })
  },

  // 第2步: 微信订阅通知授权 (长期订阅: 授权一次可持续接收告警)
  authorizeNotify() {
    const tmplId = app.globalData.templateId
    if (!tmplId) { wx.showToast({ title: '模板ID未配置', icon: 'none' }); return }
    wx.requestSubscribeMessage({
      tmplIds: [tmplId],
      success: (res) => {
        if (res[tmplId] === 'accept') {
          wx.setStorageSync('subscribed', true)
          this.setData({ subscribed: true })
          // 上报订阅关系前先等 login 完成拿 openid (修复 openid 竞态)
          app.login().then((openid) => {
            if (openid) {
              wx.request({
                url: `${app.globalData.apiBase}/mini/subscribe`,
                method: 'POST',
                data: {
                  openid,
                  device_serial: this.data.deviceSerial || wx.getStorageSync('deviceSerial') || '',
                  nickname: '家属',
                }
              })
            } else {
              // openid 拿不到: 本地已标已订阅, 但后端无记录 → 需下次重新授权才会上报
              wx.showToast({ title: '登录未完成，订阅未上报，请稍后重新授权', icon: 'none' })
            }
          })
          wx.showToast({ title: '已开启告警通知', icon: 'success' })
        } else {
          wx.showToast({ title: '未授权，可稍后再开', icon: 'none' })
        }
      }
    })
  },

  // 进入某一步 (第3步时加载历史告警)
  enterStep(n) {
    this.setData({ step: n })
    if (n === 3) this.loadEvents()
  },

  // 第3步: 加载历史告警
  loadEvents() {
    wx.request({
      url: `${app.globalData.apiBase}/fall-events?limit=8&offset=0`,
      success: (res) => {
        const list = (res.data && res.data.data && res.data.data.events) || []
        const events = list.map(e => ({
          event_id: e.event_id,
          risk_level: e.risk?.level,
          risk_level_name: e.risk?.level_name,
          // 本地持久化图片是相对路径需拼接 [V9.4]
          capture_pic_url: resolveUrl(e.capture_pic_url),
          description: (e.context && e.context.description) || e.description,
          location: (e.context && e.context.location) || e.location || '',
          time: this.formatTime(e.created_at),
        }))
        this.setData({ events })
        // 黑图修复: 逐项把抓拍图转本地临时路径, <image> 直接渲染本地文件 [2026-08-12]
        events.forEach((e, idx) => {
          toLocalImageUrl(e.capture_pic_url, (local) => {
            if (local && local !== e.capture_pic_url) {
              this.setData({ [`events[${idx}].capture_pic_url`]: local })
            }
          })
        })
      }
    })
  },

  formatTime(iso) {
    if (!iso) return ''
    const d = new Date(iso)
    const p = (n) => String(n).padStart(2, '0')
    return `${d.getMonth() + 1}/${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`
  },

  goDetail(e) {
    wx.navigateTo({ url: `/pages/events/detail?id=${e.currentTarget.dataset.id}` })
  },

  goHome() {
    wx.switchTab({ url: '/pages/index/index' })
  }
})
