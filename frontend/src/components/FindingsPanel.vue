<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { knowledgeRevision, store } from '../store'

interface Finding {
  key: string
  kind: string
  state: string
  canonical_id: string | null
  payload: Record<string, string>
  policy_version: string
}
interface Evidence { key: string; source_kind: string; source_id: string; quote?: string; locator?: string; originals?: number; actors?: string[]; accepted_answers?: number }
const props = defineProps<{ concepts: { id: string; name: string }[]; profiles: { id: string; label: string }[] }>()
const emit = defineEmits<{ changed: [] }>()
const state = ref('active')
const kind = ref('')
const offset = ref(0)
const total = ref(0)
const rows = ref<Finding[]>([])
const selected = ref<{ finding: Finding; evidence: Evidence[] } | null>(null)
const busy = ref(false)
const correctedTopic = ref('')
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
  try {
    const data = await api.get<{ total: number; findings: Finding[] }>(`/api/ml/findings?state=${state.value}&kind=${kind.value}&offset=${offset.value}`)
    rows.value = data.findings
    total.value = data.total
  } catch (error) { store.fail(error, 'Could not load automatic findings') }
}
async function inspect(row: Finding) {
  try {
    const data = await api.get<Finding & { evidence: Evidence[] }>(`/api/ml/findings/${row.key}`)
    selected.value = { finding: data, evidence: data.evidence }
    correctedTopic.value = data.payload.concept_id || ''
  } catch (error) { store.fail(error, 'Could not load the supporting evidence') }
}
async function correctTopic() {
  if (!selected.value || !correctedTopic.value || busy.value) return
  busy.value = true
  try {
    await api.put(`/api/ml/findings/${selected.value.finding.key}/topic`, { concept_id: correctedTopic.value })
    selected.value = null
    await load()
    emit('changed')
    store.notify('Topic correction saved')
  } catch (error) { store.fail(error, 'Could not correct the topic') }
  finally { busy.value = false }
}
async function decide(row: Finding, mode: string) {
  if (busy.value) return
  busy.value = true
  try {
    await api.put(`/api/ml/findings/${row.key}/decision`, { mode })
    selected.value = null
    await load()
    emit('changed')
    store.notify(mode === 'automatic' ? 'Automatic checks will reassess this finding' : mode === 'suppressed' ? 'Finding suppressed until you restore it' : 'Your decision is now fixed')
  } catch (error) { store.fail(error, 'Could not save the decision') }
  finally { busy.value = false }
}
watch([state, kind], () => { offset.value = 0; selected.value = null; load() })
watch(offset, load)
watch(knowledgeRevision, load)
onMounted(load)
</script>

<template>
  <section class="card findings" data-testid="ml-findings">
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
      <div class="row gap8"><strong>{{ title(selected.finding) }}</strong><button class="btn small" @click="selected = null">Close evidence</button></div>
      <p class="muted">Policy: {{ selected.finding.policy_version }}. A model score is not a probability that a claim is correct.</p>
      <div v-if="selected.finding.kind === 'mention'" class="row gap8 filters">
        <select v-model="correctedTopic" aria-label="Correct topic">
          <option v-for="concept in concepts" :key="concept.id" :value="concept.id">{{ concept.name }}</option>
        </select>
        <button class="btn small" :disabled="busy || !correctedTopic" @click="correctTopic">Save topic correction</button>
      </div>
      <p v-if="!selected.evidence.length" class="muted">No current supporting evidence. A manual decision can remain fixed.</p>
      <div v-for="entry in selected.evidence" :key="entry.key" class="evidence-entry">
        <blockquote v-if="entry.quote">{{ entry.quote }}</blockquote>
        <p v-else-if="entry.actors">{{ entry.originals }} contributions · {{ entry.actors.length }} independent people · {{ entry.accepted_answers }} accepted answers</p>
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
