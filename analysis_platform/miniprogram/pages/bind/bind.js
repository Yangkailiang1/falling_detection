// 设备绑定页 — 序列号绑定 + 自动发现
const app = getApp()

Page({
  data: {
    deviceSerial: '',
    discovered: [],
    submitting: false,
  },

  onLoad() { this.discover() },

  onSerial(e) { this.setData({ deviceSerial: e.detail.value }) },

  // 自动发现萤石账号下的设备
  discover() {
    wx.request({
      url: `${app.globalData.apiBase}/mini/devices`,
      success: (res) => {
        const devices = (res.data && res.data.data && res.data.data.devices) || []
        this.setData({ discovered: devices })
      }
    })
  },

  submit() {
    const { deviceSerial } = this.data
    if (!deviceSerial) {
      wx.showToast({ title: '请输入设备序列号', icon: 'none' })
      return
    }
    this.doBind(deviceSerial.trim())
  },

  bindFromList(e) {
    this.doBind(e.currentTarget.dataset.serial)
  },

  doBind(serial) {
    this.setData({ submitting: true })
    wx.request({
      url: `${app.globalData.apiBase}/mini/device/bind`,
      method: 'POST',
      data: { device_serial: serial, openid: app.getOpenid() },
      success: (res) => {
        this.setData({ submitting: false })
        if (res.data && res.data.success) {
          wx.setStorageSync('deviceSerial', serial)
          wx.showToast({ title: '绑定成功', icon: 'success' })
          setTimeout(() => wx.navigateBack(), 800)
        } else {
          wx.showToast({ title: res.data?.message || '绑定失败', icon: 'none' })
        }
      },
      fail: () => {
        this.setData({ submitting: false })
        wx.showToast({ title: '网络错误', icon: 'none' })
      }
    })
  },

  scanCode() {
    wx.scanCode({
      success: (res) => {
        // 二维码内容可能是序列号或含 SN= 参数
        const q = (res.result || '').trim()
        const sn = q.match(/SN=([A-Za-z0-9]+)/)
        const serial = sn ? sn[1] : q
        if (serial && serial.length >= 6) {
          this.doBind(serial)
        } else {
          wx.showToast({ title: '未识别到设备信息', icon: 'none' })
        }
      }
    })
  }
})
