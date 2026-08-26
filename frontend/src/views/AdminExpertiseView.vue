<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ApiError, api } from '../api'
import { store } from '../store'

interface AdminState {
  logged_in: boolean
  username: string | null
}
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

const state = ref<AdminState | null>(null)
const username = ref('')
const password = ref('')
const authError = ref('')

const concepts = ref<ConceptRow[]>([])
const mappings = ref<MappingRow[]>([])
const profiles = ref<ProfileRow[]>([])
const conceptName = ref('')
const conceptAliases = ref('')
const mapProfile = ref('')
const mapConcept = ref('')
const previewQuery = ref('')
const preview = ref<{ detected: string[]; experts: string[] } | null>(null)
const newAdminUser = ref('')
const newAdminPass = ref('')
const admins = ref<{ id: string; username: string }[]>([])

async function loadState() {
  state.value = await api.get<AdminState>('/api/admin/state')
  if (state.value.logged_in) await loadData()
}

async function loadData() {
  ;[concepts.value, mappings.value, profiles.value, admins.value] = await Promise.all([
    api.get<ConceptRow[]>('/api/admin/concepts'),
    api.get<MappingRow[]>('/api/admin/expertise'),
    api.get<ProfileRow[]>('/api/admin/profiles'),
    api.get<{ id: string; username: string }[]>('/api/admin/admins'),
  ])
}

async function submitAuth() {
  authError.value = ''
  try {
    await api.post('/api/admin/login', { username: username.value.trim(), password: password.value })
    password.value = ''
    await loadState()
    store.notify('Signed in as admin')
  } catch (e) {
    authError.value = e instanceof ApiError ? e.message : 'Authentication failed'
  }
}

async function logout() {
  await api.post('/api/admin/logout')
  await loadState()
  store.notify('Signed out of admin')
}

async function addConcept() {
  if (!conceptName.value.trim()) return
  try {
    await api.post('/api/admin/concepts', {
      name: conceptName.value.trim(),
      aliases: conceptAliases.value.split(',').map((a) => a.trim()).filter(Boolean),
    })
    conceptName.value = ''
    conceptAliases.value = ''
    await loadData()
    store.notify('Concept added; existing content tagged')
  } catch (e) {
    store.notify(e instanceof ApiError ? e.message : 'Could not add the concept')
  }
}

async function removeConcept(id: string) {
  if (!window.confirm('Delete this concept and its mappings?')) return
  await api.delete(`/api/admin/concepts/${id}`)
  await loadData()
}

async function addMapping() {
  if (!mapProfile.value || !mapConcept.value) return
  try {
    await api.post('/api/admin/expertise', { profile_id: mapProfile.value, concept_id: mapConcept.value })
    await loadData()
    store.notify('Expertise mapping added')
  } catch (e) {
    store.notify(e instanceof ApiError ? e.message : 'Could not add the mapping')
  }
}

async function removeMapping(mappingId: string) {
  await api.delete(`/api/admin/expertise/${mappingId}`)
  await loadData()
}

async function runPreview() {
  if (!previewQuery.value.trim()) return
  preview.value = await api.get(`/api/admin/routing-preview?q=${encodeURIComponent(previewQuery.value.trim())}`)
}

async function addAdmin() {
  if (!newAdminUser.value.trim() || !newAdminPass.value) return
  try {
    await api.post('/api/admin/admins', { username: newAdminUser.value.trim(), password: newAdminPass.value })
    newAdminUser.value = ''
    newAdminPass.value = ''
    await loadData()
    store.notify('Additional admin created')
  } catch (e) {
    store.notify(e instanceof ApiError ? e.message : 'Could not create the admin')
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
        <p class="lead">Map pilot profiles to expertise areas so matching questions appear in the right queue.</p>
      </div>
      <div v-if="state?.logged_in" class="row gap8">
        <span class="chip good">ADMIN · {{ state.username }}</span>
        <button class="btn small" @click="logout">Sign out</button>
      </div>
    </div>

    <!-- First-run onboarding / login -->
    <div v-if="state && !state.logged_in" class="card auth-card" data-testid="admin-auth">
      <h2>Admin sign in</h2>
      <p>Admin access is required to manage concepts and expertise mappings.</p>
      <label>Username</label>
      <input v-model="username" type="text" autocomplete="username" data-testid="admin-username" />
      <label>Password</label>
      <input v-model="password" type="password" autocomplete="current-password" data-testid="admin-password" @keyup.enter="submitAuth" />
      <p v-if="authError" class="form-error">{{ authError }}</p>
      <div class="modal-actions">
        <button class="btn primary" data-testid="admin-submit" @click="submitAuth">Sign in</button>
      </div>
    </div>

    <template v-if="state?.logged_in">
      <div class="grid-2">
        <div class="card card-pad">
          <h3>Concepts and aliases</h3>
          <p class="muted" style="font-size: 11px; margin-top: 4px">
            Deterministic matching only: questions and contributions are tagged when they mention a concept or one of its
            aliases.
          </p>
          <div class="row gap8" style="margin-top: 12px; flex-wrap: wrap">
            <input v-model="conceptName" type="text" placeholder="Concept name (e.g. Optima)" style="flex: 1; min-width: 140px" data-testid="concept-name" />
            <input v-model="conceptAliases" type="text" placeholder="Aliases, comma separated" style="flex: 1.4; min-width: 170px" data-testid="concept-aliases" />
            <button class="btn primary" data-testid="add-concept" @click="addConcept">Add</button>
          </div>
          <div style="margin-top: 12px">
            <p v-if="!concepts.length" class="muted" style="font-size: 12px">No concepts yet.</p>
            <div v-for="c in concepts" :key="c.id" class="row between" style="padding: 8px 0; border-top: 1px solid var(--border)">
              <div class="area-chips">
                <span class="chip team">{{ c.name }}</span>
                <span v-for="a in c.aliases" :key="a" class="chip">{{ a }}</span>
              </div>
              <button class="btn small ghost" @click="removeConcept(c.id)">Remove</button>
            </div>
          </div>
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
          <div>Profile</div>
          <div>Expertise areas</div>
          <div>Add area</div>
        </div>
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
            <span class="avatar">{{ m.label.slice(-4, -2).toUpperCase() || 'BP' }}</span>
            <span><strong>{{ m.label }}</strong><span>Unverified pilot profile</span></span>
          </div>
          <div class="area-chips">
            <span v-for="a in m.areas" :key="a.mapping_id" class="chip">
              {{ a.name }}<button title="Remove" @click="removeMapping(a.mapping_id)">×</button>
            </span>
          </div>
          <div></div>
        </div>
      </div>

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
  </section>
</template>
