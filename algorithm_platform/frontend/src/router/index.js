// 算法迭代平台 - 路由配置（hash 模式）
import { createRouter, createWebHashHistory } from 'vue-router'

const AppLayout = () => import('@/components/AppLayout.vue')

const routes = [
  {
    path: '/',
    component: AppLayout,
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: '仪表盘', icon: 'Odometer' }
      },
      {
        path: 'events',
        name: 'EventList',
        component: () => import('@/views/EventList.vue'),
        meta: { title: '事件浏览', icon: 'List' }
      },
      {
        path: 'events/:eventId',
        name: 'EventDetail',
        component: () => import('@/views/EventDetail.vue'),
        meta: { title: '事件详情', hidden: true }
      },
      {
        path: 'ingest',
        name: 'Ingest',
        component: () => import('@/views/Ingest.vue'),
        meta: { title: '数据接入', icon: 'Upload' }
      }
    ]
  }
]

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

router.beforeEach((to, from, next) => {
  document.title = to.meta.title
    ? `${to.meta.title} - 算法迭代平台`
    : '算法迭代平台'
  next()
})

export default router
