// [开发文档 二.V1.0 - Vue3前端入口]
// 功能: Vue应用入口，挂载根组件，注册插件
// 对应文档章节: 七.7.2 - 前端启动
import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

import App from './App.vue'
import router from './router'
import { pinia } from './stores'
import './assets/main.css'

const app = createApp(App)

// [开发文档 二.技术栈 - 插件注册]
app.use(ElementPlus, { locale: zhCn })
app.use(router)
app.use(pinia)

// 全局注册Element Plus图标
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

app.mount('#app')
