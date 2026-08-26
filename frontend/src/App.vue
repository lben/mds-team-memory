<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from './api'
import { store } from './store'

const route = useRoute()
const router = useRouter()

const titles: Record<string, string> = {
  '/capture': 'Capture',
  '/search': 'Search',
  '/questions': 'Questions',
  '/scratchpad': 'Scratchpad',
  '/documents': 'Documents',
  '/context': 'Context Map',
  '/impact': 'Impact',
  '/admin/expertise': 'Expertise Routing',
}
const crumb = computed(() => {
  const base = '/' + route.path.split('/')[1]
  return titles[route.path] || titles[base] || 'MDS'
})

const showProfile = ref(false)
const showNotifications = ref(false)
const nameDraft = ref('')
const notifications = ref<{ id: string; kind: string; message: string; item_id: string | null; read: boolean; created_at: string }[]>([])

const initials = computed(() => {
  const label = store.profile?.label || ''
  const words = label.trim().split(/\s+/)
  const source = store.profile?.display_name || ''
  if (source) return words.map((w) => w[0]).slice(0, 2).join('').toUpperCase()
  return label.slice(-4, -2).toUpperCase() || 'BP'
})

async function saveName() {
  if (!nameDraft.value.trim()) return
  await api.put('/api/profile', { display_name: nameDraft.value.trim() })
  await store.loadProfile()
  showProfile.value = false
  store.notify('Display name saved (shown as unverified)')
}

async function openNotifications() {
  showNotifications.value = !showNotifications.value
  if (!showNotifications.value) return
  const data = await api.get<{ unread: number; notifications: typeof notifications.value }>('/api/notifications')
  notifications.value = data.notifications
  store.unread = data.unread
}

async function markAllRead() {
  await api.post('/api/notifications/read', {})
  notifications.value = notifications.value.map((n) => ({ ...n, read: true }))
  store.unread = 0
}

function openNotification(n: { item_id: string | null }) {
  showNotifications.value = false
  if (n.item_id) router.push({ path: '/search', query: { item: n.item_id } })
}

function fmt(ts: string) {
  return new Date(ts).toLocaleString()
}

onMounted(() => {
  store.loadProfile()
  store.refreshUnread()
  window.setInterval(() => store.refreshUnread(), 30000)
})
</script>

<template>
  <div class="app">
    <aside class="sidebar">
      <div class="brand"><strong>MDS</strong><span>Team Memory</span></div>
      <nav class="nav" aria-label="Main navigation">
        <router-link to="/capture" active-class="active"><span class="nav-dot"></span>Capture</router-link>
        <router-link to="/search" active-class="active"><span class="nav-dot"></span>Search</router-link>
        <router-link to="/questions" active-class="active"><span class="nav-dot"></span>Questions</router-link>
        <router-link to="/scratchpad" active-class="active"><span class="nav-dot"></span>Scratchpad</router-link>
        <router-link to="/documents" active-class="active"><span class="nav-dot"></span>Documents</router-link>
        <router-link to="/context" active-class="active"><span class="nav-dot"></span>Context Map</router-link>
        <router-link to="/impact" active-class="active"><span class="nav-dot"></span>Impact</router-link>
      </nav>
      <div class="nav-label">Admin</div>
      <nav class="nav" data-testid="admin-nav">
        <router-link to="/admin/expertise" active-class="active"><span class="nav-dot"></span>Expertise Routing</router-link>
      </nav>
      <button class="profile" @click="showProfile = !showProfile" data-testid="profile-button">
        <span class="avatar">{{ initials }}</span>
        <span>
          <strong>{{ store.profile?.label || '…' }}</strong>
          <span>Unverified · This browser</span>
        </span>
      </button>
      <div v-if="showProfile" class="profile-pop">
        <label>Optional display name (unverified)</label>
        <input v-model="nameDraft" type="text" maxlength="80" placeholder="e.g. Jane S." @keyup.enter="saveName" />
        <div class="modal-actions" style="margin-top: 12px">
          <button class="btn small" @click="showProfile = false">Close</button>
          <button class="btn small primary" @click="saveName">Save name</button>
        </div>
      </div>
    </aside>

    <main class="main">
      <header class="topbar">
        <div class="crumb">MDS Team Memory / <strong>{{ crumb }}</strong></div>
        <div class="top-actions">
          <router-link class="btn" to="/search">Search</router-link>
          <router-link class="btn" :to="{ path: '/questions', query: { ask: '1' } }">Ask question</router-link>
          <router-link class="btn primary" to="/capture">Quick Capture</router-link>
          <button class="btn bell" @click="openNotifications" aria-label="Notifications" data-testid="bell">
            🔔<span v-if="store.unread" class="badge">{{ store.unread }}</span>
          </button>
        </div>
      </header>

      <div v-if="showNotifications" class="notif-pop">
        <div class="head">
          <h3>Notifications</h3>
          <button class="btn small" @click="markAllRead">Mark all read</button>
        </div>
        <div v-if="!notifications.length" class="notif muted">Nothing yet. Recognition for your contributions appears here.</div>
        <div
          v-for="n in notifications"
          :key="n.id"
          class="notif"
          :class="{ unread: !n.read }"
          style="cursor: pointer"
          @click="openNotification(n)"
        >
          {{ n.message }}
          <time>{{ fmt(n.created_at) }}</time>
        </div>
      </div>

      <router-view />
    </main>
  </div>
  <div v-if="store.toast" class="toast">{{ store.toast }}</div>
</template>
