// 首页 — 引导 + 设备状态 + 订阅授权
const app = getApp()

Page({
  data: {
    deviceBound: false,
    subscribed: false,
    wecomConfigured: false,
    setupDone: false,
    deviceSerial: '',
    deviceName: '',
    deviceOnline: false,
    authError: '',   // 授权错误信息 (调试用, 完整显示)
    authResult: '',  // 授权结果 (调试用)
  },

  onShow() {
    this.loadState()
  },

  loadState() {
    const serial = wx.getStorageSync('deviceSerial')
    const subscribed = wx.getStorageSync('subscribed')
    const wecomConfigured = !!wx.getStorageSync('wecomKey')
    this.setData({
      deviceBound: !!serial,
      subscribed: !!subscribed,
      wecomConfigured,
      setupDone: !!serial && (!!subscribed || wecomConfigured),
      deviceSerial: serial || '',
    })
    if (serial) this.loadDevice(serial)
  },

  loadDevice(serial) {
    wx.request({
      url: `${app.globalData.apiBase}/mini/devices`,
      success: (res) => {
        const devices = (res.data && res.data.data && res.data.data.devices) || []
        const dev = devices.find(d => d.device_serial === serial)
        if (dev) {
          this.setData({
            deviceName: dev.device_name || serial,
            deviceOnline: dev.status === 'online',
          })
        }
      }
    })
  },

  goSetup() {
    wx.navigateTo({ url: '/pages/setup/setup' })
  },

  goBind() {
    wx.navigateTo({ url: '/pages/bind/bind' })
  },

  // 订阅消息授权 (长期订阅: 授权一次可持续接收告警)
  authorizeNotify() {
    const tmplId = app.globalData.templateId
    this.setData({ authError: '', authResult: '' })
    if (!tmplId) {
      this.setData({ authError: '模板ID未配置' })
      return
    }
    wx.requestSubscribeMessage({
      tmplIds: [tmplId],
      success: (res) => {
        this.setData({ authResult: JSON.stringify(res) })
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
                  device_serial: wx.getStorageSync('deviceSerial') || '',
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
          this.setData({ authError: '用户未允许授权: ' + JSON.stringify(res) })
        }
      },
      fail: (err) => {
        // 完整错误显示在页面 (调试用)
        this.setData({ authError: '授权失败: ' + (err.errMsg || JSON.stringify(err)) })
      }
    })
  },

  goEvents() {
    wx.switchTab({ url: '/pages/events/events' })
  },

  // 查看实时画面 (萤石半屏播放器, 含对讲)
  goCamera() {
    const serial = wx.getStorageSync('deviceSerial') || ''
    if (!serial) {
      wx.showToast({ title: '未绑定设备', icon: 'none' })
      return
    }
    wx.navigateTo({ url: `/pages/camera/camera?serial=${serial}` })
  }
})
