import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { nextTick } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useAppStore } from '@/stores/app'
import { ElMessage } from 'element-plus'
import NProgress from 'nprogress'
import 'nprogress/nprogress.css'

NProgress.configure({
  showSpinner: false,
  minimum: 0.2,
  easing: 'ease',
  speed: 500
})

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    redirect: '/dashboard'
  },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('@/layouts/BasicLayout.vue'),
    meta: {
      title: '仪表板',
      icon: 'Dashboard',
      requiresAuth: true,
      transition: 'fade'
    },
    children: [
      {
        path: '',
        name: 'DashboardHome',
        component: () => import('@/views/Dashboard/index.vue'),
        meta: { title: '仪表板', requiresAuth: true }
      }
    ]
  },
  {
    path: '/pattern-screening',
    name: 'PatternScreening',
    component: () => import('@/layouts/BasicLayout.vue'),
    meta: {
      title: '技术形态选股',
      icon: 'Search',
      requiresAuth: true,
      transition: 'slide-up'
    },
    children: [
      {
        path: '',
        name: 'PatternScreeningHome',
        component: () => import('@/views/PatternScreening/index.vue'),
        meta: { title: '技术形态选股', requiresAuth: true }
      },
      {
        path: 'tasks/:taskId',
        name: 'PatternScreeningTaskDetail',
        component: () => import('@/views/PatternScreening/TaskDetail.vue'),
        meta: { title: '选股任务详情', requiresAuth: true, hideInMenu: true }
      }
    ]
  },
  {
    path: '/strategies',
    name: 'Strategies',
    component: () => import('@/layouts/BasicLayout.vue'),
    meta: {
      title: '策略工具',
      icon: 'DataAnalysis',
      requiresAuth: true,
      transition: 'slide-up'
    },
    children: [
      {
        path: '',
        name: 'StrategiesHome',
        component: () => import('@/views/Strategies/index.vue'),
        meta: { title: '策略工具', requiresAuth: true }
      }
    ]
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('@/layouts/BasicLayout.vue'),
    meta: {
      title: '设置',
      icon: 'Setting',
      requiresAuth: true,
      transition: 'slide-left'
    },
    children: [
      {
        path: '',
        name: 'SettingsHome',
        component: () => import('@/views/Settings/index.vue'),
        meta: { title: '设置', requiresAuth: true }
      },
      {
        path: 'config',
        name: 'ConfigManagement',
        component: () => import('@/views/Settings/ConfigManagement.vue'),
        meta: { title: '配置管理', requiresAuth: true }
      },
      {
        path: 'database',
        name: 'DatabaseManagement',
        component: () => import('@/views/System/DatabaseManagement.vue'),
        meta: { title: '数据库管理', requiresAuth: true }
      },
      {
        path: 'logs',
        name: 'OperationLogs',
        component: () => import('@/views/System/OperationLogs.vue'),
        meta: { title: '操作日志', requiresAuth: true }
      },
      {
        path: 'system-logs',
        name: 'LogManagement',
        component: () => import('@/views/System/LogManagement.vue'),
        meta: { title: '系统日志', requiresAuth: true }
      },
      {
        path: 'sync',
        name: 'MultiSourceSync',
        component: () => import('@/views/System/MultiSourceSync.vue'),
        meta: { title: '多数据源同步', requiresAuth: true }
      },
      {
        path: 'cache',
        name: 'CacheManagement',
        component: () => import('@/views/Settings/CacheManagement.vue'),
        meta: { title: '缓存管理', requiresAuth: true }
      },
      {
        path: 'usage',
        name: 'UsageStatistics',
        component: () => import('@/views/Settings/UsageStatistics.vue'),
        meta: { title: '使用统计', requiresAuth: true }
      },
      {
        path: 'scheduler',
        name: 'SchedulerManagement',
        component: () => import('@/views/System/SchedulerManagement.vue'),
        meta: { title: '定时任务', requiresAuth: true }
      }
    ]
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Auth/Login.vue'),
    meta: { title: '登录', hideInMenu: true, transition: 'fade' }
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/views/Error/404.vue'),
    meta: { title: '页面不存在', hideInMenu: true, requiresAuth: true }
  }
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior(_to, _from, savedPosition) {
    if (savedPosition) {
      return savedPosition
    } else {
      return { top: 0 }
    }
  }
})

router.beforeEach(async (to, _from, next) => {
  NProgress.start()

  const authStore = useAuthStore()
  const appStore = useAppStore()

  const title = to.meta.title as string
  if (title) {
    document.title = `${title} - tradeToolkit`
  }

  if (to.meta.requiresAuth && !authStore.isAuthenticated) {
    authStore.setRedirectPath(to.fullPath)
    next('/login')
    return
  }

  if (authStore.isAuthenticated && to.name === 'Login') {
    next('/dashboard')
    return
  }

  appStore.setCurrentRoute(to)
  next()
})

router.afterEach((_to, _from) => {
  NProgress.done()
  nextTick(() => {})
})

router.onError((error) => {
  console.error('路由错误:', error)
  NProgress.done()
  ElMessage.error('页面加载失败，请重试')
})

export default router
export { routes }
