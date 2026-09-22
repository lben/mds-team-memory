<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ApiError, api } from '../api'
import { useAsk } from '../ask'
import AskModal from './AskModal.vue'
import { knowledgeRevision, store } from '../store'

interface Finding {
  key: string
  kind: string
  state: string
  canonical_id: string | null
  payload: Record<string, string>
  policy_version: string
}
interface Evidence {
  key: string; source_kind: string; source_id: string; quote?: string; locator?: string
  originals?: number; actors?: string[]; accepted_answers?: number
  topic_confirmations?: { id: string; kind: 'helped' | 'accepted'; item_id: string; concept_id: string }[]
}
const props = defineProps<{ concepts: { id: string; name: string }[]; profiles: { id: string; label: string }[] }>()
const emit = defineEmits<{ changed: [] }>()
const state = ref('active')
const kind = ref('')
const offset = ref(0)
const total = ref(0)
const rows = ref<Finding[]>([])
const selected = ref<{ finding: Finding; evidence: Evidence[] } | null>(null)
const selectedKey = ref<string | null>(null)
let listRequest = 0
let detailRequest = 0
const busy = ref(false)
const correctedTopic = ref('')
const { ask, askUser, answerAsk } = useAsk()
const names = computed(() => new Map(props.concepts.map(c => [c.id, c.name])))
const people = computed(() => new Map(props.profiles.map(p => [p.id, p.label])))
const labels: Record<string, string> = { active: 'Applied automatically', held: 'Awaiting evidence', weak: 'Weak association', stale: 'Rechecking evidence', withdrawn: 'Support removed', suppressed: 'Suppressed', pinned: 'Manually fixed' }
function title(row: Finding) {
  const p = row.payload
  if (row.kind === 'concept') return p.name
  if (row.kind === 'alias') return `${p.alias} → ${names.value.get(p.concept_id) || 'Concept'}`
  if (row.kind === 'expertise') return `${people.value.get(p.profile_id) || 'Account holder'} · ${names.value.get(p.concept_id) || 'Concept'}`
  if (row.kind === 'mention') return `Topic: ${names.value.get(p.concept_id) || 'Concept'}`
  return `${names.value.get(p.src_id) || 'Concept'} ${p.predicate?.replaceAll('_', ' ') || '↔'} ${names.value.get(p.dst_id) || 'Concept'}`
}
function editLink(row: Finding) {
  if (row.kind === 'concept') return { path: '/admin/expertise', query: { concept: row.canonical_id || undefined } }
  if (row.kind === 'alias') return { path: '/admin/expertise', query: { concept: row.payload.concept_id } }
  return { path: '/admin/expertise', query: { link: row.canonical_id || undefined } }
}
async function load() {
  const request = ++listRequest
  try {
    const data = await api.get<{ total: number; findings: Finding[] }>(`/api/ml/findings?state=${state.value}&kind=${kind.value}&offset=${offset.value}`)
    if (request !== listRequest) return
    rows.value = data.findings
    total.value = data.total
  } catch (error) { if (request === listRequest) store.fail(error, 'Could not load automatic findings') }
}
function clearSelection() {
  detailRequest++
  selectedKey.value = null
  selected.value = null
  correctedTopic.value = ''
}
async function loadSelected() {
  const key = selectedKey.value
  if (!key) return
  const request = ++detailRequest
  const previousTopic = selected.value?.finding.payload.concept_id || ''
  try {
    const data = await api.get<Finding & { evidence: Evidence[] }>(`/api/ml/findings/${key}`)
    if (request !== detailRequest || selectedKey.value !== key) return
    selected.value = { finding: data, evidence: data.evidence }
    if (correctedTopic.value === previousTopic) correctedTopic.value = data.payload.concept_id || ''
  } catch (error) {
    if (request !== detailRequest || selectedKey.value !== key) return
    clearSelection()
    if (!(error instanceof ApiError && error.status === 404)) store.fail(error, 'Could not load the supporting evidence')
  }
}
async function inspect(row: Finding) {
  if (selectedKey.value !== row.key) clearSelection()
  selectedKey.value = row.key
  await loadSelected()
}
async function correctTopic() {
  if (!selected.value || !correctedTopic.value || busy.value) return
  busy.value = true
  try {
    await api.put(`/api/ml/findings/${selected.value.finding.key}/topic`, { concept_id: correctedTopic.value })
    clearSelection()
    await load()
    emit('changed')
    store.notify('Topic correction saved')
  } catch (error) { store.fail(error, 'Could not correct the topic') }
  finally { busy.value = false }
}
async function decide(row: Finding, mode: string) {
  if (busy.value) return
  // Restoring automation on an expertise mapping releases manual authority and
  // lets the automatic checks re-derive it; without qualifying evidence the
  // mapping is withdrawn. That is destructive, so explain and confirm first.
  if (mode === 'automatic' && row.kind === 'expertise') {
    const answer = await askUser({
      title: 'Release this expertise mapping?',
      message: 'Automatic checks will re-derive the mapping from the team\'s contributions. Without qualifying evidence it will be withdrawn and removed from routing.',
      confirmLabel: 'Release and reassess',
      danger: true,
    })
    if (answer === null) return
  }
  busy.value = true
  try {
    const result = await api.put<{ state: string }>(`/api/ml/findings/${row.key}/decision`, { mode })
    clearSelection()
    await load()
    emit('changed')
    if (mode === 'automatic' && row.kind === 'expertise') {
      store.notify(result.state === 'active'
        ? 'Mapping released; automatic checks kept it active'
        : result.state === 'held'
          ? 'Mapping released; automatic checks are awaiting more evidence'
          : 'Mapping released and withdrawn — no qualifying automatic evidence')
    } else {
      store.notify(mode === 'automatic' ? 'Automatic checks will reassess this finding' : mode === 'suppressed' ? 'Finding suppressed until you restore it' : 'Your decision is now fixed')
    }
  } catch (error) { store.fail(error, 'Could not save the decision') }
  finally { busy.value = false }
}
watch([state, kind], () => { offset.value = 0; clearSelection(); load() })
watch(offset, load)
watch(knowledgeRevision, () => { load(); loadSelected() })
onMounted(load)
onBeforeUnmount(() => { listRequest++; clearSelection() })
</script>

<template>
  <section class="card findings" data-testid="ml-findings">
    <AskModal
      v-if="ask"
      :title="ask.title"
      :message="ask.message"
      :input-label="ask.inputLabel"
      :confirm-label="ask.confirmLabel"
      :danger="ask.danger"
      @resolve="answerAsk"
    />
    <h3>Automatic knowledge</h3>
    <p class="muted">Qualified findings take effect on their own. Uncertain findings wait for more evidence. Review is optional.</p>
    <div class="row gap8 filters">
      <select v-model="state" aria-label="Finding state">
        <option value="">All states</option>
        <option v-for="(label, value) in labels" :key="value" :value="value">{{ label }}</option>
      </select>
      <select v-model="kind" aria-label="Finding type">
        <option value="">All types</option><option value="concept">Concepts</option><option value="alias">Aliases</option>
        <option value="mention">Topic matches</option><option value="relationship">Relationships</option>
        <option value="association">Associations</option><option value="expertise">Expertise</option>
      </select>
      <span class="muted">{{ total }} findings</span>
    </div>
    <p v-if="!rows.length" class="muted">No findings in this view.</p>
    <div v-for="row in rows" :key="row.key" class="finding-row" :data-testid="`finding-${row.key}`">
      <div><strong>{{ title(row) }}</strong><div class="muted">{{ row.kind }} · {{ labels[row.state] || row.state }}</div></div>
      <div class="row gap8 actions">
        <button class="btn small" @click="inspect(row)">Evidence</button>
        <router-link v-if="row.canonical_id && ['concept', 'alias', 'relationship', 'association', 'relationship_pair'].includes(row.kind)" class="btn small" :to="editLink(row)">Edit</router-link>
        <button v-if="row.state !== 'suppressed'" class="btn small" :disabled="busy" @click="decide(row, 'suppressed')">Suppress</button>
        <button v-if="['suppressed', 'pinned'].includes(row.state)" class="btn small" :disabled="busy" @click="decide(row, 'automatic')">Restore automation</button>
        <button v-else class="btn small" :disabled="busy" @click="decide(row, 'pinned')">Keep fixed</button>
      </div>
    </div>
    <div v-if="total > 50" class="row gap8 filters">
      <button class="btn small" :disabled="offset === 0" @click="offset = Math.max(0, offset - 50)">Previous</button>
      <button class="btn small" :disabled="offset + 50 >= total" @click="offset += 50">Next</button>
    </div>
    <div v-if="selected" class="evidence-box">
      <div class="row gap8"><strong>{{ title(selected.finding) }}</strong><button class="btn small" @click="clearSelection">Close evidence</button></div>
      <p class="muted">{{ labels[selected.finding.state] || selected.finding.state }} · Policy: {{ selected.finding.policy_version }}. A model score is not a probability that a claim is correct.</p>
      <div v-if="selected.finding.kind === 'mention'" class="row gap8 filters">
        <select v-model="correctedTopic" aria-label="Correct topic">
          <option v-for="concept in concepts" :key="concept.id" :value="concept.id">{{ concept.name }}</option>
        </select>
        <button class="btn small" :disabled="busy || !correctedTopic" @click="correctTopic">Save topic correction</button>
      </div>
      <p v-if="!selected.evidence.length" class="muted">No current supporting evidence. A manual decision can remain fixed.</p>
      <div v-for="entry in selected.evidence" :key="entry.key" class="evidence-entry">
        <blockquote v-if="entry.quote">{{ entry.quote }}</blockquote>
        <p v-else-if="entry.actors">Confirmed topic feedback: {{ entry.originals }} contribution groups · {{ entry.actors.length }} accounts · {{ entry.accepted_answers }} accepted answers</p>
        <ul v-if="entry.topic_confirmations?.length">
          <li v-for="confirmation in entry.topic_confirmations" :key="confirmation.id">
            <router-link :to="{ path: '/', query: { item: confirmation.item_id } }">
              {{ confirmation.kind === 'accepted' ? 'Accepted topic confirmation' : 'Helpful topic confirmation' }}
            </router-link>
          </li>
        </ul>
        <span class="muted">{{ entry.locator }}</span>
        <router-link v-if="entry.source_kind === 'item'" :to="{ path: '/', query: { item: entry.source_id } }">Open contribution</router-link>
      </div>
    </div>
  </section>
</template>

<style scoped>
.findings { margin-bottom: 16px; padding: 20px; }
.filters { flex-wrap: wrap; margin: 14px 0; }
.finding-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 0; border-top: 1px solid var(--border); }
.actions { flex-wrap: wrap; }
.evidence-box { margin-top: 16px; padding: 16px; background: var(--bg); }
.evidence-entry { margin-top: 14px; }
blockquote { margin: 8px 0; white-space: pre-wrap; }
@media (max-width: 760px) { .finding-row { align-items: flex-start; flex-direction: column; } }
</style>
