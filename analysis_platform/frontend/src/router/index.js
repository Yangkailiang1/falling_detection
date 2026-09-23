// [开发文档 三.3.2 - 前端路由设计]
// 功能: Vue Router路由配置，定义页面导航结构
// 对应文档章节: 三.3.2 - 前端路由设计
import { createRouter, createWebHashHistory } from 'vue-router'

// 布局组件 - 包含侧边栏的通用布局
const AppLayout = () => import('@/components/AppLayout.vue')

const routes = [
  {
    path: '/',
    component: AppLayout,
    redirect: '/dashboard',
    children: [
      // [开发文档 四.V4.0 - 仪表盘]
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: '仪表盘', icon: 'Odometer' }
      },
      // [开发文档 四.V3.0 - 设备管理]
      {
        path: 'devices',
        name: 'DeviceList',
        component: () => import('@/views/DeviceList.vue'),
        meta: { title: '设备管理', icon: 'VideoCamera' }
      },
      // [开发文档 四.V3.0 - 设备详情]
      {
        path: 'devices/:deviceSerial',
        name: 'DeviceDetail',
        component: () => import('@/views/DeviceDetail.vue'),
        meta: { title: '设备详情', hidden: true }
      },
      // [开发文档 四.V3.0 - 实时监控]
      {
        path: 'live/:deviceSerial',
        name: 'LiveMonitor',
        component: () => import('@/views/LiveMonitor.vue'),
        meta: { title: '实时监控', icon: 'VideoPlay' }
      },
      // [开发文档 四.V4.0 - 多路监控]
      {
        path: 'monitor',
        name: 'MultiMonitor',
        component: () => import('@/views/MultiMonitor.vue'),
        meta: { title: '多路监控', icon: 'Grid' }
      },
      // [开发文档 四.V3.0 - 告警中心]
      {
        path: 'alarms',
        name: 'AlarmCenter',
        component: () => import('@/views/AlarmCenter.vue'),
        meta: { title: '告警中心', icon: 'Bell' }
      },
      // [开发文档 四.V3.0 - 抓拍管理]
      {
        path: 'captures',
        name: 'CaptureManage',
        component: () => import('@/views/CaptureManage.vue'),
        meta: { title: '抓拍管理', icon: 'Picture' }
      },
      // [开发文档 四.V3.0 - AI对话]
      {
        path: 'chat',
        name: 'AiChat',
        component: () => import('@/views/AiChat.vue'),
        meta: { title: 'AI对话', icon: 'ChatDotRound' }
      },
      // [方案书 §3.1, §8 - 跌倒事件中心]
      {
        path: 'fall-events',
        name: 'FallEventList',
        component: () => import('@/views/FallEventList.vue'),
        meta: { title: '跌倒事件', icon: 'WarningFilled' }
      },
      // [方案书 §8.2 - 跌倒事件详情]
      {
        path: 'fall-events/:eventId',
        name: 'FallEventDetail',
        component: () => import('@/views/FallEventDetail.vue'),
        meta: { title: '事件详情', hidden: true }
      },
      // [开发文档 四.V4.0 - 系统设置]
      {
        path: 'settings',
        name: 'Settings',
        component: () => import('@/views/Settings.vue'),
        meta: { title: '系统设置', icon: 'Setting' }
      }
    ]
  }
]

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

// 路由守卫：设置页面标题
router.beforeEach((to, from, next) => {
  document.title = to.meta.title
    ? `${to.meta.title} - 萤石分析平台`
    : '萤石平台接入分析平台'
  next()
})

export default router
