// 实时画面 — 萤石半屏小程序方案 (openEmbeddedMiniProgram, 含对讲)
const app = getApp()

const EZ_PLAYER_APPID = 'wxf2b3a0262975d8c2'  // 萤石官方小程序

Page({
  data: {
    deviceSerial: '',
    deviceOnline: false,
    loading: true,
    errorMsg: '',
    streamUrl: '',
    accessToken: '',
  },

  onLoad(options) {
    this.setData({ deviceSerial: options.serial || wx.getStorageSync('deviceSerial') || '' })
    this.initStream()
  },

  initStream() {
    const serial = this.data.deviceSerial
    if (!serial) {
      this.setData({ loading: false, errorMsg: '未绑定设备' })
      return
    }
    let urlDone = false
    let tokenDone = false
    const tryReady = () => {
      // URL 和 token 都就绪后, 按钮才可用
      if (urlDone && tokenDone) {
        this.setData({ loading: false })
      }
    }
    // 并行获取 URL + token
    wx.request({
      url: `${app.globalData.apiBase}/mini/stream-url?serial=${serial}&type=live`,
      success: (res) => {
        urlDone = true
        if (res.data && res.data.success) {
          this.setData({ streamUrl: res.data.data.url, deviceOnline: true })
        } else {
          this.setData({ errorMsg: '取流地址失败' })
        }
        tryReady()
      },
      fail: () => {
        urlDone = true
        this.setData({ errorMsg: '网络错误' })
        tryReady()
      },
    })
    wx.request({
      url: `${app.globalData.apiBase}/mini/stream-token`,
      success: (res) => {
        tokenDone = true
        if (res.data && res.data.success) {
          this.setData({ accessToken: res.data.data.accessToken })
        } else {
          this.setData({ errorMsg: 'token获取失败' })
        }
        tryReady()
      },
      fail: () => {
        tokenDone = true
        this.setData({ errorMsg: 'token网络错误' })
        tryReady()
      },
    })
  },

  // 打开萤石半屏播放器 (props 方式, 新版)
  openPlayer() {
    const { streamUrl, accessToken } = this.data
    if (!streamUrl || !accessToken) {
      this.setData({ errorMsg: '播放凭证未就绪，请稍候重试' })
      return
    }
    const props = {
      accessToken,
      url: streamUrl,
      plugins: 'talk,voice,ptz,mirror',   // 对讲+语音播报+云台+镜像
      theme: {
        showFullScreenBtn: true,
        showCapture: true,
        showBottomBar: true
      }
    }
    wx.openEmbeddedMiniProgram({
      appId: EZ_PLAYER_APPID,
      path: '/packageJ/pages/ezplayer/ezplayer?props=' + encodeURIComponent(JSON.stringify(props)),
      envVersion: 'release',
      success: () => {
        this.setData({ loading: false })
      },
      fail: (err) => {
        this.setData({ loading: false, errorMsg: '打开播放器失败: ' + (err.errMsg || '') })
      }
    })
  }
})
