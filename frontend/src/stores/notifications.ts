import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useAuthStore } from '@/stores/auth'

export interface NotificationItem {
  id: string
  title: string
  content: string
  type: string
  status: 'read' | 'unread'
  created_at: string
  link?: string
  source?: string
}

export const useNotificationStore = defineStore('notifications', () => {
  const items = ref<NotificationItem[]>([])
  const unreadCount = ref(0)
  const loading = ref(false)
  const drawerVisible = ref(false)

  const connected = computed(() => false)
  const hasUnread = computed(() => unreadCount.value > 0)

  async function refreshUnreadCount() {}
  async function loadList(_status: 'unread' | 'all' = 'all') { loading.value = true; loading.value = false }

  async function markRead(id: string) {
    const idx = items.value.findIndex(x => x.id === id)
    if (idx !== -1) items.value[idx].status = 'read'
    if (unreadCount.value > 0) unreadCount.value -= 1
  }

  async function markAllRead() {
    items.value = items.value.map(x => ({ ...x, status: 'read' }))
    unreadCount.value = 0
  }

  function addNotification(n: Omit<NotificationItem, 'id' | 'status' | 'created_at'> & { id?: string; created_at?: string; status?: 'unread' | 'read' }) {
    const id = n.id || `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
    const item: NotificationItem = {
      id, title: n.title, content: n.content, type: n.type,
      status: n.status ?? 'unread', created_at: n.created_at || new Date().toISOString(),
      link: n.link, source: n.source,
    }
    items.value.unshift(item)
    if (item.status === 'unread') unreadCount.value += 1
  }

  function connectWebSocket() {}  // WebSocket 路由已移除
  function disconnectWebSocket() {}
  function connect() { connectWebSocket() }
  function disconnect() { disconnectWebSocket() }
  function setDrawerVisible(v: boolean) { drawerVisible.value = v }

  return {
    items, unreadCount, hasUnread, loading, drawerVisible, connected,
    refreshUnreadCount, loadList, markRead, markAllRead, addNotification,
    connect, disconnect, connectWebSocket, disconnectWebSocket, setDrawerVisible,
  }
})
