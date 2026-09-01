<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError, api } from './api'
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
const username = ref('')
const password = ref('')
const authBusy = ref(false)
const authError = ref('')
const notifications = ref<{ id: string; kind: string; message: string; item_id: string | null; read: boolean; created_at: string }[]>([])

const initials = computed(() => initialsFor(store.profile?.label))
const identity = computed(() => identityNote(store.profile?.label, store.profile?.verified))

async function submitAuth(path: 'login' | 'signup') {
  if (authBusy.value) return
  authError.value = ''
  if (!username.value.trim() || !password.value) {
    authError.value = 'Enter a username and a password'
    return
  }
  authBusy.value = true
  try {
    await api.post(`/api/auth/${path}`, { username: username.value.trim(), password: password.value })
    username.value = ''
    password.value = ''
    showProfile.value = false
    await store.refreshIdentity()
    store.notify(path === 'signup' ? 'Account created - your work is now yours' : `Signed in as ${store.auth.username}`)
    router.go(0) // who you are shows on every screen; reload rather than patch each one
  } catch (e) {
    authError.value = e instanceof ApiError ? e.message : 'Could not sign in'
  } finally {
    authBusy.value = false
  }
}

async function signOut() {
  await api.post('/api/auth/logout')
  showProfile.value = false
  await store.refreshIdentity()
  store.notify('Signed out')
  router.go(0)
}

async function saveName() {
  if (!nameDraft.value.trim()) return void store.notify('Enter a display name first')
  await api.put('/api/profile', { display_name: nameDraft.value.trim() })
  await store.loadProfile()
  showProfile.value = false
  store.notify('Display name saved')
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
  store.refreshIdentity()
  store.refreshUnread()
  store.watchNotifications()
})
</script>

<template>
  <div class="app">
    <aside class="sidebar">
      <div class="brand"><strong>MDS</strong><span>Team Knowledge</span></div>
      <nav class="nav" aria-label="Main navigation">
        <router-link to="/" exact-active-class="active"><span class="nav-dot"></span>Home</router-link>
        <router-link to="/leaderboard" active-class="active"><span class="nav-dot"></span>Leaderboard</router-link>
        <router-link to="/documents" active-class="active"><span class="nav-dot"></span>Documents</router-link>
        <router-link to="/scratchpad" active-class="active"><span class="nav-dot"></span>Scratchpad</router-link>
      </nav>
      <div class="nav-label">Admin</div>
      <nav class="nav" data-testid="admin-nav">
        <router-link to="/admin/expertise" active-class="active"><span class="nav-dot"></span>Expertise Routing</router-link>
      </nav>
      <button class="profile" @click="showProfile = !showProfile" data-testid="profile-button">
        <span class="avatar">{{ initials }}</span>
        <span>
          <strong>{{ store.profile?.label || '…' }}</strong>
          <span>{{ identity }}</span>
        </span>
      </button>
      <div v-if="showProfile" class="profile-pop" data-testid="profile-pop">
        <template v-if="store.profile?.verified">
          <p class="pop-note">
            Signed in as <strong>{{ store.auth.username }}</strong
            ><template v-if="store.auth.is_admin"> · administrator</template>. Your contributions
            and your scratchpad belong to this account and survive clearing your cookies.
          </p>
          <div class="modal-actions" style="margin-top: 12px">
            <button class="btn small" @click="showProfile = false">Close</button>
            <button class="btn small" data-testid="sign-out" @click="signOut">Sign out</button>
          </div>
        </template>

        <template v-else>
          <p class="pop-warn" data-testid="no-account-warning">
            You have no account. Everything you write lives in this browser only — clear your
            cookies and your contributions and your private scratchpad are gone for good, with no
            way to get them back. Create an account and they stay yours.
          </p>
          <label>Username</label>
          <input v-model="username" type="text" maxlength="80" autocomplete="username" data-testid="auth-username" />
          <label style="margin-top: 8px">Password</label>
          <input
            v-model="password"
            type="password"
            maxlength="200"
            autocomplete="current-password"
            data-testid="auth-password"
            @keyup.enter="submitAuth('signup')"
          />
          <p v-if="authError" class="form-error" data-testid="auth-error">{{ authError }}</p>
          <div class="modal-actions" style="margin-top: 12px">
            <button class="btn small" :disabled="authBusy" data-testid="do-sign-in" @click="submitAuth('login')">
              Sign in
            </button>
            <button class="btn small primary" :disabled="authBusy" data-testid="do-sign-up" @click="submitAuth('signup')">
              Create account
            </button>
          </div>

          <div class="pop-divider"></div>
          <label>Or just a display name, on this machine</label>
          <input v-model="nameDraft" type="text" maxlength="80" placeholder="e.g. Jane S." data-testid="display-name" @keyup.enter="saveName" />
          <p class="pop-note">A name set here is not an account and does not survive a cookie clear.</p>
          <div class="modal-actions" style="margin-top: 10px">
            <button class="btn small" @click="showProfile = false">Close</button>
            <button class="btn small" @click="saveName">Save name</button>
          </div>
        </template>
      </div>
    </aside>

    <main class="main">
      <header class="topbar">
        <div class="crumb">MDS Team Knowledge / <strong>{{ crumb }}</strong></div>
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
