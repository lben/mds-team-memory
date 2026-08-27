import { reactive } from 'vue'
import { api } from './api'

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

interface AdminState {
  logged_in: boolean
  username: string | null
}

export const store = reactive({
  profile: null as Profile | null,
  admin: { logged_in: false, username: null } as AdminState,
  toast: '' as string,
  toastTimer: 0 as number,
  unread: 0,

  notify(message: string) {
    this.toast = message
    window.clearTimeout(this.toastTimer)
    this.toastTimer = window.setTimeout(() => (this.toast = ''), 2600)
  },

  async loadProfile() {
    this.profile = await api.get<Profile>('/api/profile')
  },

  async loadAdmin() {
    try {
      this.admin = await api.get<AdminState>('/api/admin/state')
    } catch {
      this.admin = { logged_in: false, username: null }
    }
  },

  async refreshUnread() {
    try {
      const data = await api.get<{ unread: number }>('/api/notifications')
      this.unread = data.unread
    } catch {
      /* transient */
    }
  },
})
