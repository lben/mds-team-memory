<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ApiError, api } from '../api'
import EvidenceModal from '../components/EvidenceModal.vue'
import MapAdminPanel from '../components/MapAdminPanel.vue'
import { initialsFor } from '../profile'
import { store } from '../store'

const evidenceLinkId = ref<string | null>(null)
const route = useRoute()
// Arriving from the graph: select the record that was clicked.
const selectedLink = computed(() => (typeof route.query.link === 'string' ? route.query.link : null))
const selectedConcept = computed(() => (typeof route.query.concept === 'string' ? route.query.concept : null))


interface ConceptRow {
  id: string
  name: string
  aliases: string[]
}
interface MappingRow {
  profile_id: string
  label: string
  areas: { mapping_id: string; concept_id: string; name: string }[]
}
interface ProfileRow {
  id: string
  label: string
}

const loaded = ref(false)
const username = ref('')
const password = ref('')
const authError = ref('')

const concepts = ref<ConceptRow[]>([])
const mappings = ref<MappingRow[]>([])
const profiles = ref<ProfileRow[]>([])
const mapProfile = ref('')
const mapConcept = ref('')
const previewQuery = ref('')
const preview = ref<{ detected: string[]; experts: string[] } | null>(null)
const newAdminUser = ref('')
const newAdminPass = ref('')
const admins = ref<{ id: string; username: string }[]>([])
// Readable by everyone: knowing who to ask is the point of routing, and it used
// to be locked behind the admin sign-in with nowhere else to look.
const publicMap = ref<{ label: string; areas: string[] }[]>([])

async function loadPublicMap() {
  try {
    publicMap.value = await api.get<{ label: string; areas: string[] }[]>('/api/expertise')
  } catch (e) {
    store.fail(e, 'Could not load who is mapped to what')
  }
}

async function loadState() {
  await store.loadAuth()
  loaded.value = true
  if (store.auth.is_admin) await loadData()
  else await loadPublicMap()
}

async function loadData() {
  try {
    ;[concepts.value, mappings.value, profiles.value, admins.value] = await Promise.all([
      api.get<ConceptRow[]>('/api/admin/concepts'),
      api.get<MappingRow[]>('/api/admin/expertise'),
      api.get<ProfileRow[]>('/api/admin/profiles'),
      api.get<{ id: string; username: string }[]>('/api/admin/admins'),
    ])
  } catch (e) {
    store.fail(e, 'Could not load the admin data')
  }
}

async function submitAuth() {
  authError.value = ''
  try {
    await api.post('/api/auth/login', { username: username.value.trim(), password: password.value })
    password.value = ''
    // Signing in changes who you are, not only what you may do.
    await store.refreshIdentity()
    await loadState()
    store.notify(`Signed in as ${store.auth.username}`)
  } catch (e) {
    authError.value = e instanceof ApiError ? e.message : 'Authentication failed'
  }
}

async function addMapping() {
  if (!mapProfile.value || !mapConcept.value) {
    store.notify('Pick a person and an expertise area')
    return
  }
  try {
    await api.post('/api/admin/expertise', { profile_id: mapProfile.value, concept_id: mapConcept.value })
    await loadData()
    store.notify('Expertise mapping added')
  } catch (e) {
    store.fail(e, 'Could not add the mapping')
  }
}

async function removeMapping(mappingId: string) {
  try {
    await api.delete(`/api/admin/expertise/${mappingId}`)
  } catch (e) {
    store.fail(e, 'Could not remove that expertise area')
  }
  await loadData()
}

async function runPreview() {
  if (!previewQuery.value.trim()) return void store.notify('Paste a question to preview first')
  try {
    preview.value = await api.get(`/api/admin/routing-preview?q=${encodeURIComponent(previewQuery.value.trim())}`)
  } catch (e) {
    preview.value = null
    store.fail(e, 'Could not preview the routing')
  }
}

async function addAdmin() {
  if (!newAdminUser.value.trim() || !newAdminPass.value) {
    return void store.notify('Give the new admin a username and a password')
  }
  try {
    await api.post('/api/admin/admins', { username: newAdminUser.value.trim(), password: newAdminPass.value })
    newAdminUser.value = ''
    newAdminPass.value = ''
    await loadData()
    store.notify('Additional admin created')
  } catch (e) {
    store.fail(e, 'Could not create the admin')
  }
}

onMounted(loadState)
</script>

<template>
  <section class="page">
    <div class="page-head">
      <div>
        <div class="eyebrow">Admin</div>
        <h1>Expertise routing</h1>
        <p class="lead">Map teammates to expertise areas so matching questions appear in the right queue.</p>
      </div>
      <div v-if="store.auth.is_admin" class="row gap8">
        <span class="chip good">ADMIN · {{ store.auth.username }}</span>
      </div>
    </div>

    <!-- First-run onboarding / login -->
    <div v-if="loaded && !store.auth.is_admin" class="card auth-card" data-testid="admin-auth">
      <h2>Admin sign in</h2>
      <p v-if="store.auth.signed_in">
        You are signed in as <strong>{{ store.auth.username }}</strong>, which is not an administrator
        account. Administrator accounts are created on the server with
        <code>manage.py create-admin</code>.
      </p>
      <p v-else>Admin access is required to manage concepts and expertise mappings.</p>
      <label>Username</label>
      <input v-model="username" type="text" autocomplete="username" data-testid="admin-username"
            aria-label="Admin username" />
      <label>Password</label>
      <input v-model="password" type="password" autocomplete="current-password" data-testid="admin-password"
            aria-label="Admin password" @keyup.enter="submitAuth" />
      <p v-if="authError" class="form-error">{{ authError }}</p>
      <div class="modal-actions">
        <button class="btn primary" data-testid="admin-submit" @click="submitAuth">Sign in</button>
      </div>
    </div>

    <!-- Everyone can see who is mapped to what; only admins can change it. -->
    <div v-if="loaded && !store.auth.is_admin" class="card admin-table" style="margin-top: 16px" data-testid="public-expertise">
      <div class="admin-head"><div>Teammate</div><div>Expertise areas</div><div></div></div>
      <p class="admin-note">
        Who the team has been mapped to, so you know who to ask. Changing this is an admin action.
      </p>
      <p v-if="!publicMap.length" class="muted" style="padding: 14px 16px; font-size: 12px">
        Nobody has been mapped to a topic yet.
      </p>
      <div v-for="row in publicMap" :key="row.label" class="admin-row">
        <div class="person">
          <span class="avatar">{{ initialsFor(row.label) }}</span>
          <span><strong>{{ row.label }}</strong><span>Has an account</span></span>
        </div>
        <div class="area-chips">
          <span v-for="a in row.areas" :key="a" class="chip">{{ a }}</span>
        </div>
        <div></div>
      </div>
    </div>

    <template v-if="store.auth.is_admin">
      <div class="grid-2">
        <div class="card card-pad">
          <h3>How tagging works</h3>
          <p class="muted" style="font-size: 12px; margin-top: 6px; line-height: 1.5">
            Questions and contributions are tagged when they mention a concept or one of its aliases — deterministic
            word matching, nothing else. Concepts, links, and relationship types are managed in the curation table
            below; the knowledge graph itself lives on the Home page.
          </p>
        </div>

        <div class="card card-pad">
          <h3>Admin accounts</h3>
          <p class="muted" style="font-size: 11px; margin-top: 4px">Multiple admins are supported.</p>
          <div class="area-chips" style="margin-top: 10px">
            <span v-for="a in admins" :key="a.id" class="chip">{{ a.username }}</span>
          </div>
          <div class="row gap8" style="margin-top: 12px; flex-wrap: wrap">
            <input v-model="newAdminUser" type="text" placeholder="Username" style="flex: 1; min-width: 120px" />
            <input v-model="newAdminPass" type="password" placeholder="Password (8+ chars)" style="flex: 1; min-width: 140px" />
            <button class="btn" @click="addAdmin">Add admin</button>
          </div>
        </div>
      </div>

      <div class="card admin-table" style="margin-top: 16px" data-testid="mapping-table">
        <div class="admin-head">
          <div>Teammate</div>
          <div>Expertise areas</div>
          <div>Add area</div>
        </div>
        <p class="admin-note" data-testid="account-required-note">
          <em>
            Your expert is not showing up? Please check with him if he/she has created a full
            account first — expertise can only be routed to someone with an account, because a
            name that lives only in a browser disappears when its cookies are cleared.
          </em>
        </p>
        <div class="admin-row">
          <select v-model="mapProfile" data-testid="map-profile">
            <option value="" disabled>Select profile…</option>
            <option v-for="p in profiles" :key="p.id" :value="p.id">{{ p.label }}</option>
          </select>
          <select v-model="mapConcept" data-testid="map-concept">
            <option value="" disabled>Select concept…</option>
            <option v-for="c in concepts" :key="c.id" :value="c.id">{{ c.name }}</option>
          </select>
          <button class="btn small primary" data-testid="add-mapping" @click="addMapping">Add mapping</button>
        </div>
        <div v-for="m in mappings" :key="m.profile_id" class="admin-row">
          <div class="person">
            <span class="avatar">{{ initialsFor(m.label) }}</span>
            <span><strong>{{ m.label }}</strong><span>Has an account</span></span>
          </div>
          <div class="area-chips">
            <span v-for="a in m.areas" :key="a.mapping_id" class="chip">
              {{ a.name }}<button title="Remove" @click="removeMapping(a.mapping_id)">×</button>
            </span>
          </div>
          <div></div>
        </div>
      </div>

      <MapAdminPanel
        style="margin-top: 16px"
        center-concept-id=""
        :selected-link-id="selectedLink"
        :selected-concept-id="selectedConcept"
        @changed="loadData"
        @evidence="evidenceLinkId = $event"
      />

      <div class="card route-preview">
        <h3>Routing preview</h3>
        <div class="row gap8" style="margin-top: 10px">
          <input v-model="previewQuery" type="text" placeholder="Paste a question to preview its routing" style="flex: 1" @keyup.enter="runPreview" />
          <button class="btn" @click="runPreview">Preview</button>
        </div>
        <div v-if="preview" class="route-flow">
          <div class="flow-node"><strong>Question</strong><br /><span class="muted">“{{ previewQuery.length > 40 ? previewQuery.slice(0, 40) + '…' : previewQuery }}”</span></div>
          <span class="arrow">→</span>
          <div class="flow-node">
            <strong>Detected terms</strong><br />
            <span class="muted">{{ preview.detected.join(' · ') || 'No known concepts' }}</span>
          </div>
          <span class="arrow">→</span>
          <div class="flow-node">
            <strong>Needs Your Expertise</strong><br />
            <span class="muted">{{ preview.experts.join(', ') || 'No mapped experts' }}</span>
          </div>
        </div>
      </div>
    </template>
    <EvidenceModal v-if="evidenceLinkId" :link-id="evidenceLinkId" @close="evidenceLinkId = null" />
  </section>
</template>
