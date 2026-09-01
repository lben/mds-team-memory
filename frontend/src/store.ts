import { reactive } from 'vue'
import { ApiError, api, type Item } from './api'

interface Profile {
  id: string
  label: string
  display_name: string | null
  verified: boolean
  totals: {
    shared: number
    helped: number
    accepted: number
    corrections: number
    endorsements: number
    score: number
  }
}

interface AuthState {
  signed_in: boolean
  username: string | null
  is_admin: boolean
}

export const store = reactive({
  profile: null as Profile | null,
  auth: { signed_in: false, username: null, is_admin: false } as AuthState,
  toast: '' as string,
  toastTimer: 0 as number,
  unread: 0,

  /** The one implementation of "Helped me".
   *
   * Three copies disagreed on the aftermath: two incremented the counter
   * locally whatever the server said, so marking something already marked
   * showed a number the server never had. The server's `created` flag is the
   * only thing that decides whether the count moved.
   */
  async markHelped(item: Item): Promise<void> {
    try {
      const r = await api.post<{ created: boolean }>(`/api/items/${item.id}/helped`)
      item.marked_helped = true
      if (r.created) {
        item.helped += 1
        this.notify('Contributor impact increased')
      }
    } catch (e) {
      this.notify(e instanceof ApiError ? e.message : 'Could not mark as helpful')
    }
  },

  /** Report a failed request. Loaders used to have no catch at all, so a failed
   * read left the screen showing state the server no longer agreed with. */
  fail(e: unknown, fallback: string) {
    this.notify(e instanceof ApiError ? e.message : fallback)
  },

  notify(message: string) {
    this.toast = message
    window.clearTimeout(this.toastTimer)
    this.toastTimer = window.setTimeout(() => (this.toast = ''), 2600)
  },

  async loadProfile() {
    try {
      this.profile = await api.get<Profile>('/api/profile')
    } catch (e) {
      this.fail(e, 'Could not load your profile')
    }
  },

  async loadAuth() {
    try {
      this.auth = await api.get<AuthState>('/api/auth/state')
    } catch {
      this.auth = { signed_in: false, username: null, is_admin: false }
    }
  },

  /** Who you are and what you may do move together: signing in changes both. */
  async refreshIdentity() {
    await Promise.all([this.loadProfile(), this.loadAuth()])
  },

  async refreshUnread() {
    try {
      const data = await api.get<{ unread: number }>('/api/notifications')
      this.unread = data.unread
    } catch {
      /* transient */
    }
  },

  /** Listen for pushed notifications, falling back to polling if a websocket
   * cannot be established — corporate proxies sometimes block them. */
  watchNotifications() {
    const startPolling = () => {
      if (pollTimer) return
      pollTimer = window.setInterval(() => store.refreshUnread(), 30000)
    }
    const stopPolling = () => {
      if (!pollTimer) return
      window.clearInterval(pollTimer)
      pollTimer = 0
    }

    const connect = () => {
      let socket: WebSocket
      try {
        const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
        socket = new WebSocket(`${scheme}://${location.host}/ws/notifications`)
      } catch {
        startPolling()
        return
      }
      socket.onopen = () => {
        stopPolling()
        retryDelay = 2000
        store.refreshUnread() // catch anything missed while disconnected
      }
      socket.onmessage = () => store.refreshUnread()
      socket.onerror = () => socket.close()
      socket.onclose = () => {
        startPolling()
        window.setTimeout(connect, retryDelay)
        retryDelay = Math.min(retryDelay * 2, 60000)
      }
    }

    connect()
  },
})

let pollTimer = 0
let retryDelay = 2000
