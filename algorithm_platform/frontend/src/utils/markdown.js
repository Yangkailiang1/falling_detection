// 算法迭代平台 - 安全 Markdown 渲染工具
// 功能: 把 LLM 生成的医疗简报 markdown（**加粗** / 标题 / 斜体 / 行内代码 / 换行）渲染为 HTML
// 安全: 先转义 HTML 实体（防 XSS），再注入受控的标签
const escapeHtml = (s = '') =>
  String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')

/**
 * 渲染受支持的子集 markdown → 安全 HTML。
 * 支持: **加粗**、*斜体*、`行内代码`、#~###### 标题、换行 → <br>
 * 其他 markdown（链接/图片/表格等）保持纯文本显示，不做解析。
 */
export function renderMarkdown(text) {
  if (!text) return ''
  // 1) 先转义用户内容（防 XSS）
  let html = escapeHtml(text)
  // 2) 行内：加粗、斜体、行内代码（加粗优先，避免斜体误吃 **）
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  html = html.replace(/(^|[^*])\*([^*\r\n]+?)\*/g, '$1<em>$2</em>')
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>')
  // 3) 标题（块级，m 标志按行匹配）
  html = html.replace(/^######\s+(.+)$/gm, '<h6>$1</h6>')
  html = html.replace(/^#####\s+(.+)$/gm, '<h5>$1</h5>')
  html = html.replace(/^####\s+(.+)$/gm, '<h4>$1</h4>')
  html = html.replace(/^###\s+(.+)$/gm, '<h3>$1</h3>')
  html = html.replace(/^##\s+(.+)$/gm, '<h2>$1</h2>')
  html = html.replace(/^#\s+(.+)$/gm, '<h1>$1</h1>')
  // 4) 换行 → <br>
  html = html.replace(/\r?\n/g, '<br>')
  return html
}
