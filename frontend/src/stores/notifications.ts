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

  const ws = ref<WebSocket | null>(null)
  const wsConnected = ref(false)
  let wsReconnectTimer: any = null
  let wsReconnectAttempts = 0
  const maxReconnectAttempts = 10

  const connected = computed(() => wsConnected.value)
  const hasUnread = computed(() => unreadCount.value > 0)

  async function refreshUnreadCount() {
    // REST API removed — notifications are WebSocket-only
  }

  async function loadList(_status: 'unread' | 'all' = 'all') {
    loading.value = true
    // REST API removed — notifications are WebSocket-only
    loading.value = false
  }

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
    const created_at = n.created_at || new Date().toISOString()
    const item: NotificationItem = {
      id,
      title: n.title,
      content: n.content,
      type: n.type,
      status: n.status ?? 'unread',
      created_at,
      link: n.link,
      source: n.source
    }
    items.value.unshift(item)
    if (item.status === 'unread') unreadCount.value += 1
  }

  function connectWebSocket() {
    try {
      if (ws.value) {
        try { ws.value.close() } catch {}
        ws.value = null
      }
      if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null }

      const authStore = useAuthStore()
      const token = authStore.token || localStorage.getItem('auth-token') || ''
      if (!token) {
        console.warn('[WS] 未找到 token，无法连接 WebSocket')
        return
      }

      const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const host = window.location.host
      const wsUrl = `${wsProtocol}//${host}/api/ws/notifications?token=${encodeURIComponent(token)}`

      const socket = new WebSocket(wsUrl)
      ws.value = socket

      socket.onopen = () => {
        wsConnected.value = true
        wsReconnectAttempts = 0
      }

      socket.onclose = (event) => {
        console.log('[WS] 连接关闭:', event.code, event.reason)
        wsConnected.value = false
        ws.value = null
        if (wsReconnectAttempts < maxReconnectAttempts) {
          const delay = Math.min(1000 * Math.pow(2, wsReconnectAttempts), 30000)
          wsReconnectTimer = setTimeout(() => {
            wsReconnectAttempts++
            connectWebSocket()
          }, delay)
        }
      }

      socket.onerror = (_error) => {
        wsConnected.value = false
      }

      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data)
          handleWebSocketMessage(message)
        } catch {}
      }
    } catch {
      wsConnected.value = false
    }
  }

  function handleWebSocketMessage(message: any) {
    switch (message.type) {
      case 'notification':
        if (message.data?.title && message.data?.type) {
          addNotification({
            id: message.data.id,
            title: message.data.title,
            content: message.data.content,
            type: message.data.type,
            link: message.data.link,
            source: message.data.source,
            created_at: message.data.created_at,
            status: message.data.status || 'unread'
          })
        }
        break
      case 'heartbeat':
      case 'connected':
        break
    }
  }

  function disconnectWebSocket() {
    if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null }
    if (ws.value) { try { ws.value.close() } catch {}; ws.value = null }
    wsConnected.value = false
    wsReconnectAttempts = 0
  }

  function connect() { connectWebSocket() }
  function disconnect() { disconnectWebSocket() }
  function setDrawerVisible(v: boolean) { drawerVisible.value = v }

  return {
    items, unreadCount, hasUnread, loading, drawerVisible,
    connected, wsConnected,
    refreshUnreadCount, loadList, markRead, markAllRead,
    addNotification, connect, disconnect,
    connectWebSocket, disconnectWebSocket, setDrawerVisible
  }
})
