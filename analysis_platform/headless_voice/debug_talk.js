// 调试：无头客户端对讲通道建立
const path = require('path')
const ROOT = path.resolve(__dirname, '..')
const puppeteer = require('puppeteer')
const fs = require('fs')
const API_BASE = process.env.API_BASE || 'http://127.0.0.1:5001'
const EZUIKIT = process.env.EZUIKIT_JS || path.join(ROOT, 'frontend', 'node_modules', 'ezuikit-js', 'ezuikit.js')

;(async () => {
  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required', '--use-fake-ui-for-media-stream', '--disable-gpu'],
  })
  const page = await browser.newPage()
  page.on('console', m => console.log('[page]', m.type(), m.text().substring(0, 150)))
  page.on('pageerror', e => console.log('[pageerror]', e.message))
  await page.goto(`${API_BASE}/headless-voice`, { waitUntil: 'domcontentloaded' })
  const ezuikit = fs.readFileSync(EZUIKIT, 'utf8')
  await page.addScriptTag({ content: ezuikit })
  // 先测 token fetch
  const tokenTest = await page.evaluate(async () => {
    try {
      const r = await fetch('/api/auth/token/ezuikit')
      const j = await r.json()
      return { status: r.status, hasToken: !!(j?.data?.accessToken || j?.accessToken), keys: Object.keys(j) }
    } catch (e) { return { error: e.message } }
  })
  console.log('[token test]', JSON.stringify(tokenTest))
  const init = await page.evaluate(async () => window.__voice.init({ apiBase: '' }))
  console.log('[init]', JSON.stringify(init))
  console.log('[state]', JSON.stringify(await page.evaluate(() => window.__voice.getState())))
  await browser.close()
})().catch(e => { console.error('[fatal]', e.message); process.exit(1) })
