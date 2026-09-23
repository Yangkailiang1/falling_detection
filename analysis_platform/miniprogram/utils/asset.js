// 资源 URL 解析 — 把 standalone 返回的相对路径(/api/mini/xxx)拼接成小程序可加载的完整地址
// 背景: standalone 对本地持久化的图片/视频返回相对路径(如 /api/mini/capture/<id>),
//       小程序 <image>/<video> 组件需要完整 URL, 前缀 apiBase 根(去掉 /api 后缀)

function resolveUrl(path) {
  if (!path) return ''
  if (path.startsWith('http')) return path
  if (path.startsWith('/')) {
    const root = getApp().globalData.apiBase.replace(/\/api$/, '')
    return root + path
  }
  return path
}

// 把网络图片 URL 转成本地临时路径, 供 <image> 渲染 (修复"抓拍图显示黑图"问题) [2026-08-12]
// 背景: <image> 走小程序 downloadFile 域名白名单(HTTP 局域网地址真机/部分环境静默加载失败,
//       透出容器深色背景 → 黑图); 而 wx.previewImage 走系统原生加载器, 同一 URL 却能正常显示。
//       用 wx.getImageInfo(与 previewImage 同源加载) 拿到本地 wxfile:// 临时路径后,
//       <image> 直接渲染本地文件, 不再受网络通道差异影响。
function toLocalImageUrl(url, done) {
  if (!url) return done('')
  if (url.indexOf('wxfile://') === 0) return done(url)

  // 详情接口与图片接口使用同一 API 主机；wx.request 已能正常访问该主机。
  // 先取二进制并写成本地文件，避免 <image> 与 previewImage 网络通道不一致。
  const fallback = () => wx.getImageInfo({
    src: url,
    success: (res) => done(res.path || url),
    fail: () => done(url),
  })
  let hash = 0
  for (let i = 0; i < url.length; i++) hash = ((hash << 5) - hash + url.charCodeAt(i)) | 0
  const localPath = `${wx.env.USER_DATA_PATH}/capture_${Math.abs(hash)}.jpg`
  wx.request({
    url,
    method: 'GET',
    responseType: 'arraybuffer',
    success: (res) => {
      if (res.statusCode < 200 || res.statusCode >= 300 || !res.data) return fallback()
      wx.getFileSystemManager().writeFile({
        filePath: localPath,
        data: res.data,
        success: () => done(localPath),
        fail: fallback,
      })
    },
    fail: fallback,
  })
}

function base64ToLocalImage(data, key, done) {
  if (!data) return done('')
  const safeKey = String(key || 'event').replace(/[^a-zA-Z0-9_-]/g, '_')
  const localPath = `${wx.env.USER_DATA_PATH}/capture_${safeKey}.jpg`
  try {
    // 同步写入后再把路径交给视图，避免网络图失败事件与异步写入互相覆盖。
    const buffer = wx.base64ToArrayBuffer(data)
    wx.getFileSystemManager().writeFileSync(localPath, buffer)
    done(localPath)
  } catch (err) {
    console.error('[capture] base64 local write failed', err)
    // 极少数真机文件系统异常时，继续用 data URI，仍不回退到局域网图片组件。
    done(`data:image/jpeg;base64,${data}`)
  }
}

module.exports = { resolveUrl, toLocalImageUrl, base64ToLocalImage }
