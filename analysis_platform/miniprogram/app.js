// 长辈守护小程序 — 全局入口
App({
  globalData: {
    openid: '',
    deviceSerial: '',
    apiBase: 'https://YOUR_API_DOMAIN/api',  // Set this to your own API host.
    // 订阅消息模板 ID (与后端 .env WX_TEMPLATE_ID 一致)
    templateId: 'YOUR_WECHAT_SUBSCRIPTION_TEMPLATE_ID',
  },

  onLaunch() {
    // 静默登录: wx.login → 后端换取 openid
    this._loginPromise = null
    this.login()
  },

  login() {
    // 复用同一个登录 Promise: 并发调用共享结果, 避免重复 wx.login
    // (解决授权点击早于 login 返回时 openid 为空、订阅未上报的竞态)
    if (this._loginPromise) return this._loginPromise
    this._loginPromise = new Promise((resolve) => {
      const that = this
      if (that.globalData.openid) { resolve(that.globalData.openid); return }
      wx.login({
        success(res) {
          wx.request({
            url: `${that.globalData.apiBase}/mini/login`,
            method: 'POST',
            data: { code: res.code },
            success(r) {
              if (r.data && r.data.success) {
                that.globalData.openid = r.data.data.openid
                wx.setStorageSync('openid', r.data.data.openid)
              }
              resolve(that.globalData.openid)
            },
            fail() { resolve('') }
          })
        },
        fail() { resolve('') }
      })
    })
    return this._loginPromise
  },

  getOpenid() {
    return this.globalData.openid || wx.getStorageSync('openid') || ''
  }
})
