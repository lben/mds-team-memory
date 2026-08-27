<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from './api'
import { identityNote, initialsFor } from './profile'
import { store } from './store'

const route = useRoute()
const router = useRouter()

const titles: Record<string, string> = {
  '/': 'Home',
  '/scratchpad': 'Scratchpad',
  '/documents': 'Documents',
  '/leaderboard': 'Leaderboard',
  '/admin/expertise': 'Expertise Routing',
}
const crumb = computed(() => {
  if (route.path === '/') return 'Home'
  const base = '/' + route.path.split('/')[1]
  return titles[route.path] || titles[base] || 'MDS'
})

const showProfile = ref(false)
const showNotifications = ref(false)
const nameDraft = ref('')
const notifications = ref<{ id: string; kind: string; message: string; item_id: string | null; read: boolean; created_at: string }[]>([])

const initials = computed(() => initialsFor(store.profile?.label))
const identity = computed(() => identityNote(store.profile?.label))
async function signOutAdmin() {
  await api.post('/api/admin/logout')
  await store.loadAdmin()
  store.notify('Signed out of admin')
  router.push('/')
}

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

async function openNotification(n: { item_id: string | null }) {
  showNotifications.value = false
  if (!n.item_id) return
  // A notification about a question or an answer belongs on the question page,
  // not in a generic item modal.
  try {
    const item = await api.get<{ kind: string; parent_id: string | null }>(`/api/items/${n.item_id}`)
    if (item.kind === 'question') return void router.push({ path: '/', query: { question: n.item_id } })
    if (item.kind === 'answer' && item.parent_id)
      return void router.push({ path: '/', query: { question: item.parent_id } })
  } catch {
    /* fall through to the generic view */
  }
  router.push({ path: '/', query: { item: n.item_id } })
}

function fmt(ts: string) {
  return new Date(ts).toLocaleString()
}

onMounted(() => {
  store.loadProfile()
  store.loadAdmin()
  store.refreshUnread()
  store.watchNotifications()
})
</script>

<template>
  <div class="app">
    <aside class="sidebar">
      <div class="brand"><strong>MDS</strong><span>Team Memory</span></div>
      <nav class="nav" aria-label="Main navigation">
        <router-link to="/" exact-active-class="active"><span class="nav-dot"></span>Home</router-link>
        <router-link to="/scratchpad" active-class="active"><span class="nav-dot"></span>Scratchpad</router-link>
        <router-link to="/documents" active-class="active"><span class="nav-dot"></span>Documents</router-link>
        <router-link to="/leaderboard" active-class="active"><span class="nav-dot"></span>Leaderboard</router-link>
      </nav>
      <div class="nav-label">Admin</div>
      <nav class="nav" data-testid="admin-nav">
        <router-link to="/admin/expertise" active-class="active"><span class="nav-dot"></span>Expertise Routing</router-link>
        <button
          v-if="store.admin.logged_in"
          class="nav-signout"
          data-testid="sign-out-admin"
          @click="signOutAdmin"
        >
          <span class="nav-dot"></span>Sign out<span class="who">{{ store.admin.username }}</span>
        </button>
      </nav>
      <button class="profile" @click="showProfile = !showProfile" data-testid="profile-button">
        <span class="avatar">{{ initials }}</span>
        <span>
          <strong>{{ store.profile?.label || '…' }}</strong>
          <span>{{ identity }}</span>
        </span>
      </button>
      <div v-if="showProfile" class="profile-pop">
        <label>Display name</label>
        <input v-model="nameDraft" type="text" maxlength="80" placeholder="e.g. Jane S." @keyup.enter="saveName" />
        <p class="pop-note">
          Contributors have no login yet, so a name you set here is self-declared and shown as unverified.
        </p>
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
