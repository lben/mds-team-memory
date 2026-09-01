<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ApiError, api } from '../api'
import AskModal from './AskModal.vue'
import { useAsk } from '../ask'
import { store } from '../store'

export interface LinkRow {
  id: string
  src_id: string
  src_name: string
  dst_id: string
  dst_name: string
  type_id: string
  type_name: string
  state: string
  occurrence_count: number
  evidence: string
  reviewed_by: string | null
  review_note: string | null
}
interface ConceptRow {
  id: string
  name: string
  aliases: string[]
}
interface TypeRow {
  id: string
  name: string
  is_builtin: boolean
  usage: number
  is_default: boolean
  selectable: boolean
}

const props = defineProps<{
  centerConceptId: string
  selectedLinkId: string | null
  selectedConceptId: string | null
}>()
const emit = defineEmits<{ changed: []; evidence: [string] }>()

const tab = ref<'links' | 'concepts' | 'types' | 'endorsed'>('links')
const scopeAll = ref(false)
const links = ref<LinkRow[]>([])
const concepts = ref<ConceptRow[]>([])
const types = ref<TypeRow[]>([])
const endorsed = ref<EndorsedRow[]>([])
const loadError = ref('')

interface EndorsedRow {
  profile_id: string
  label: string
  has_account: boolean
  endorsements: number
  topics: string[]
}

/** Anyone can endorse, so this is what the team actually thinks, and it is the
 * evidence to map expertise from. Loaded only when the tab is opened. */
async function loadEndorsed() {
  try {
    endorsed.value = await api.get<EndorsedRow[]>('/api/admin/endorsements')
  } catch (e) {
    fail(e, 'Could not load endorsements')
  }
}
const selectableTypes = computed(() => types.value.filter((t) => t.selectable))

const newConceptName = ref('')
const newConceptAliases = ref('')
const newTypeName = ref('')
const linkSrc = ref('')
const linkDst = ref('')
const linkType = ref('')
const linkNote = ref('')
const editingConcept = ref<string | null>(null)
const editName = ref('')
const editAliases = ref('')

async function loadAll() {
  const query = scopeAll.value || !props.centerConceptId ? '' : `?concept_id=${props.centerConceptId}`
  // Loaded independently: one failing request must not blank the other tables,
  // and a failure has to be visible rather than looking like "nothing here".
  const [l, c, t] = await Promise.allSettled([
    api.get<LinkRow[]>(`/api/graph/links${query}`),
    api.get<ConceptRow[]>('/api/admin/concepts'),
    api.get<TypeRow[]>('/api/graph/relationship-types'),
  ])
  const failures: string[] = []
  if (l.status === 'fulfilled') links.value = l.value
  else failures.push('links')
  if (c.status === 'fulfilled') concepts.value = c.value
  else failures.push('concepts')
  if (t.status === 'fulfilled') types.value = t.value
  else failures.push('relationship types')
  loadError.value = failures.length ? `Could not load ${failures.join(', ')}. Check the server.` : ''

  if (!selectableTypes.value.some((t2) => t2.id === linkType.value)) {
    linkType.value = selectableTypes.value.find((t2) => t2.is_default)?.id ?? selectableTypes.value[0]?.id ?? ''
  }
  // A selection can arrive before the rows exist, so honour it once they do.
  if (props.selectedLinkId) focusRow('links', props.selectedLinkId)
  else if (props.selectedConceptId) focusRow('concepts', props.selectedConceptId)
}

/** A failed action usually means this table is out of date — another tab
 * deleted the row, or the admin session ended. Resync so what is on screen is
 * true, instead of leaving a row that can only ever fail again. */
const { ask, askUser, answerAsk } = useAsk()

async function fail(e: unknown, fallback: string) {
  store.notify(e instanceof ApiError ? e.message : fallback)
  await loadAll()
}

async function setState(link: LinkRow, state: string) {
  // Both decisions can carry a reason. Recording only why something is wrong,
  // never why it is right, left the endorsement unexplained in the evidence view.
  // Cancel must abort the decision. The note is optional, so an empty string
  // from Confirm still goes through; only null means the admin backed out.
  const note = await askUser({
    title: state === 'confirmed' ? 'Approve this link' : 'Reject this link',
    message:
      state === 'rejected'
        ? 'It stays in this table and keeps counting occurrences, so you can reinstate it later.'
        : 'It becomes a solid line on the map for everyone.',
    inputLabel: state === 'rejected' ? 'Why is this link wrong? (optional)' : 'Why is this link right? (optional, shown as the evidence)',
    confirmLabel: state === 'confirmed' ? 'Approve' : 'Reject',
  })
  if (note === null) return

  try {
    await api.patch(`/api/graph/links/${link.id}`, { state, note })
    store.notify(state === 'confirmed' ? 'Link approved' : 'Link rejected — it stays here and keeps counting')
    await loadAll()
    emit('changed')
  } catch (e) {
    fail(e, 'Could not update the link')
  }
}

async function changeType(link: LinkRow, typeId: string) {
  try {
    await api.patch(`/api/graph/links/${link.id}`, { type_id: typeId })
    await loadAll()
    emit('changed')
  } catch (e) {
    fail(e, 'Could not change the relationship type')
  }
}

async function deleteLink(link: LinkRow) {
  const answer = await askUser({
    title: `Delete ${link.src_name} — ${link.dst_name}?`,
    message: 'This is permanent. Rejecting the link instead keeps it inspectable and it carries on counting.',
    confirmLabel: 'Delete permanently',
    danger: true,
  })
  if (answer === null) return
  try {
    await api.delete(`/api/graph/links/${link.id}`)
    await loadAll()
    emit('changed')
  } catch (e) {
    fail(e, 'Could not delete the link')
  }
}

async function createLink() {
  if (!linkSrc.value || !linkDst.value || !linkNote.value.trim()) {
    store.notify('Pick two concepts and say why they are linked')
    return
  }
  try {
    await api.post('/api/graph/links', {
      src_id: linkSrc.value,
      dst_id: linkDst.value,
      type_id: linkType.value,
      note: linkNote.value.trim(),
    })
    linkNote.value = ''
    store.notify('Link added')
    await loadAll()
    emit('changed')
  } catch (e) {
    fail(e, 'Could not add the link')
  }
}

async function createConcept() {
  if (!newConceptName.value.trim()) {
    store.notify('Give the concept a name')
    return
  }
  try {
    await api.post('/api/admin/concepts', {
      name: newConceptName.value.trim(),
      aliases: newConceptAliases.value.split(',').map((a) => a.trim()).filter(Boolean),
    })
    newConceptName.value = ''
    newConceptAliases.value = ''
    store.notify('Concept added; existing content tagged')
    await loadAll()
    emit('changed')
  } catch (e) {
    fail(e, 'Could not add the concept')
  }
}

function startEdit(concept: ConceptRow) {
  editingConcept.value = concept.id
  editName.value = concept.name
  editAliases.value = concept.aliases.join(', ')
}

async function saveConcept(concept: ConceptRow) {
  try {
    await api.put(`/api/admin/concepts/${concept.id}`, {
      name: editName.value.trim(),
      aliases: editAliases.value.split(',').map((a) => a.trim()).filter(Boolean),
    })
    editingConcept.value = null
    store.notify('Concept updated; content retagged')
    await loadAll()
    emit('changed')
  } catch (e) {
    fail(e, 'Could not update the concept')
  }
}

async function deleteConcept(concept: ConceptRow) {
  const answer = await askUser({
    title: `Delete "${concept.name}"?`,
    message: 'Its links, tags and expertise mappings are removed from the map. This cannot be undone.',
    confirmLabel: 'Delete concept',
    danger: true,
  })
  if (answer === null) return
  try {
    await api.delete(`/api/admin/concepts/${concept.id}`)
    await loadAll()
    emit('changed')
  } catch (e) {
    fail(e, 'Could not delete the concept')
  }
}

async function createType() {
  if (!newTypeName.value.trim()) {
    store.notify('Give the relationship type a name')
    return
  }
  try {
    await api.post('/api/admin/relationship-types', { name: newTypeName.value.trim() })
    newTypeName.value = ''
    await loadAll()
  } catch (e) {
    fail(e, 'Could not add the relationship type')
  }
}

async function renameType(row: TypeRow) {
  const next = await askUser({
    title: `Rename "${row.name}"`,
    message: row.usage
      ? `It is used by ${row.usage} link${row.usage === 1 ? '' : 's'}. Renaming updates all of them on the map.`
      : undefined,
    inputLabel: 'New name',
    confirmLabel: 'Rename',
  })
  if (next === null) return
  if (!next.trim()) return void store.notify('Give the relationship type a name')
  if (next.trim() === row.name) return
  try {
    await api.put(`/api/admin/relationship-types/${row.id}`, { name: next.trim() })
    store.notify('Relationship type renamed')
    await loadAll()
    emit('changed')
  } catch (e) {
    fail(e, 'Could not rename the relationship type')
  }
}

async function deleteType(row: TypeRow) {
  const answer = await askUser({
    title: `Delete the relationship type "${row.name}"?`,
    message: 'Only a type nothing uses can be deleted.',
    confirmLabel: 'Delete type',
    danger: true,
  })
  if (answer === null) return
  try {
    await api.delete(`/api/admin/relationship-types/${row.id}`)
    store.notify('Relationship type deleted')
    await loadAll()
  } catch (e) {
    fail(e, 'Could not delete the relationship type')
  }
}

function stateChip(state: string) {
  return state === 'confirmed' ? 'good' : state === 'rejected' ? '' : 'warn'
}

async function focusRow(kind: 'links' | 'concepts', id: string) {
  tab.value = kind
  await nextTick()
  document.getElementById(`row-${id}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
}

watch(() => props.centerConceptId, loadAll)
watch(scopeAll, loadAll)
watch(
  () => props.selectedLinkId,
  async (id) => {
    if (!id) return
    if (!links.value.some((l) => l.id === id)) {
      scopeAll.value = true
      await loadAll()
    }
    focusRow('links', id)
  },
)
watch(
  () => props.selectedConceptId,
  (id) => id && focusRow('concepts', id),
)

loadAll()
</script>

<template>
  <div class="card admin-panel" data-testid="map-admin-panel">
    <AskModal
      v-if="ask"
      :title="ask.title"
      :message="ask.message"
      :input-label="ask.inputLabel"
      :confirm-label="ask.confirmLabel"
      :danger="ask.danger"
      @resolve="answerAsk"
    />
    <div class="panel-tabs">
      <button :class="{ active: tab === 'links' }" data-testid="tab-links" @click="tab = 'links'">Links</button>
      <button :class="{ active: tab === 'concepts' }" data-testid="tab-concepts" @click="tab = 'concepts'">Concepts</button>
      <button :class="{ active: tab === 'types' }" data-testid="tab-types" @click="tab = 'types'">Relationship types</button>
      <button
        :class="{ active: tab === 'endorsed' }"
        data-testid="tab-endorsed"
        @click="tab = 'endorsed'; loadEndorsed()"
      >
        Most endorsed
      </button>
      <span v-if="loadError" class="form-error" style="margin-left: 10px" data-testid="panel-error">{{ loadError }}</span>
      <label v-if="tab === 'links' && centerConceptId" class="scope-toggle">
        <input v-model="scopeAll" type="checkbox" /> Show all concepts
      </label>
      <span v-else-if="tab === 'links'" class="scope-toggle">Showing every link</span>
    </div>

    <!-- Links -->
    <div v-if="tab === 'links'">
      <div class="panel-row">
        <select v-model="linkSrc" data-testid="link-src">
          <option value="" disabled>From concept…</option>
          <option v-for="c in concepts" :key="c.id" :value="c.id">{{ c.name }}</option>
        </select>
        <select v-model="linkType" data-testid="link-type">
          <option v-for="t in selectableTypes" :key="t.id" :value="t.id">{{ t.name }}</option>
        </select>
        <select v-model="linkDst" data-testid="link-dst">
          <option value="" disabled>To concept…</option>
          <option v-for="c in concepts" :key="c.id" :value="c.id">{{ c.name }}</option>
        </select>
        <input v-model="linkNote" type="text" placeholder="Why? (recorded as the evidence)" data-testid="link-note" />
        <button class="btn small primary" data-testid="add-link" @click="createLink">Add link</button>
      </div>

      <div class="panel-head links">
        <div>Link</div><div>State</div><div>Occurrences</div><div>Reviewed</div><div>Actions</div>
      </div>
      <p v-if="!links.length" class="muted empty">No detected or manual links yet.</p>
      <div v-for="link in links" :id="`row-${link.id}`" :key="link.id" class="panel-body links" :class="{ highlight: link.id === selectedLinkId }">
        <div>
          <strong>{{ link.src_name }}</strong>
          <select :value="link.type_id" style="margin: 0 6px" @change="changeType(link, ($event.target as HTMLSelectElement).value)">
            <option v-for="t in selectableTypes" :key="t.id" :value="t.id">{{ t.name }}</option>
          </select>
          <strong>{{ link.dst_name }}</strong>
          <div v-if="link.review_note" class="muted note">“{{ link.review_note }}”</div>
        </div>
        <div><span class="chip" :class="stateChip(link.state)">{{ link.state.toUpperCase() }}</span></div>
        <div>
          <button class="btn small ghost count" :data-testid="`evidence-${link.id}`" @click="emit('evidence', link.id)">
            {{ link.occurrence_count }} ↗
          </button>
        </div>
        <div class="muted note">{{ link.reviewed_by || '—' }}</div>
        <div class="row gap8 wrap">
          <button v-if="link.state !== 'confirmed'" class="btn small" :data-testid="`approve-${link.id}`" @click="setState(link, 'confirmed')">Approve</button>
          <button v-if="link.state !== 'rejected'" class="btn small" :data-testid="`reject-${link.id}`" @click="setState(link, 'rejected')">Reject</button>
          <button class="btn small ghost" @click="deleteLink(link)">Delete</button>
        </div>
      </div>
    </div>

    <!-- Most endorsed: who the team treats as an expert -->
    <div v-else-if="tab === 'endorsed'" data-testid="endorsed-panel">
      <p class="muted empty" style="padding-bottom: 0">
        Anyone can endorse a contribution. This is who the team has actually endorsed, and on what —
        use it to decide who to map as an expert above.
      </p>
      <div class="panel-head concepts"><div>Teammate</div><div>Endorsed on</div><div>Endorsements</div></div>
      <p v-if="!endorsed.length" class="muted empty">Nobody has been endorsed yet.</p>
      <div v-for="e in endorsed" :key="e.profile_id" class="panel-body concepts">
        <div>
          <strong>{{ e.label }}</strong>
          <span v-if="!e.has_account" class="chip warn" style="margin-left: 6px">NO ACCOUNT</span>
        </div>
        <div>
          <span v-for="t in e.topics" :key="t" class="chip">{{ t }}</span>
          <span v-if="!e.topics.length" class="muted">—</span>
        </div>
        <div><strong>{{ e.endorsements }}</strong></div>
      </div>
    </div>

    <!-- Concepts -->
    <div v-else-if="tab === 'concepts'">
      <div class="panel-row">
        <input v-model="newConceptName" type="text" placeholder="Concept name" data-testid="concept-name" />
        <input v-model="newConceptAliases" type="text" placeholder="Aliases, comma separated" data-testid="concept-aliases" />
        <button class="btn small primary" data-testid="add-concept" @click="createConcept">Add concept</button>
      </div>
      <div class="panel-head concepts"><div>Concept</div><div>Aliases</div><div>Actions</div></div>
      <p v-if="!concepts.length" class="muted empty">No concepts yet.</p>
      <div v-for="c in concepts" :id="`row-${c.id}`" :key="c.id" class="panel-body concepts" :class="{ highlight: c.id === selectedConceptId }">
        <template v-if="editingConcept === c.id">
          <input v-model="editName" type="text" />
          <input v-model="editAliases" type="text" placeholder="Aliases, comma separated" />
          <div class="row gap8">
            <button class="btn small primary" @click="saveConcept(c)">Save</button>
            <button class="btn small ghost" @click="editingConcept = null">Cancel</button>
          </div>
        </template>
        <template v-else>
          <strong>{{ c.name }}</strong>
          <div class="area-chips">
            <span v-for="a in c.aliases" :key="a" class="chip">{{ a }}</span>
            <span v-if="!c.aliases.length" class="muted note">—</span>
          </div>
          <div class="row gap8">
            <button class="btn small" @click="startEdit(c)">Edit</button>
            <button class="btn small ghost" @click="deleteConcept(c)">Delete</button>
          </div>
        </template>
      </div>
    </div>

    <!-- Relationship types -->
    <div v-else>
      <div class="panel-row">
        <input v-model="newTypeName" type="text" placeholder="New relationship type (e.g. feeds)" data-testid="type-name" />
        <button class="btn small primary" data-testid="add-type" @click="createType">Add type</button>
      </div>
      <div class="panel-head types"><div>Name</div><div>Used by</div><div>Actions</div></div>
      <div v-for="t in types" :key="t.id" class="panel-body types">
        <div>
          <strong>{{ t.name }}</strong>
          <span v-if="t.is_builtin" class="chip" style="margin-left: 8px">BUILT IN</span>
          <span v-if="!t.selectable" class="chip warn" style="margin-left: 6px">SYSTEM</span>
          <span v-else-if="t.is_default" class="chip good" style="margin-left: 6px">DEFAULT</span>
          <div v-if="!t.selectable" class="muted note" style="margin-top: 4px">
            Generated automatically between duplicate contributions — not offered when linking concepts.
          </div>
        </div>
        <div class="muted note">{{ t.usage }} link{{ t.usage === 1 ? '' : 's' }}</div>
        <div class="row gap8">
          <button class="btn small" :disabled="t.is_builtin" @click="renameType(t)">Rename</button>
          <button class="btn small ghost" :disabled="t.is_builtin" @click="deleteType(t)">Delete</button>
        </div>
      </div>
      <p class="muted empty">
        Built-in types are protected. A type can only be deleted once no links use it. Every type here except
        those marked SYSTEM can be chosen when linking two concepts.
      </p>
    </div>
  </div>
</template>
