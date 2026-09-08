<script setup lang="ts">
import { useDialog } from '../dialog'
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { knowledgeRevision, store } from '../store'

interface ClaimSource {
  context_source?: boolean
  source_kind: string
  source_id: string
  source_hash: string
  text: string
  quote: string
  start: number
  end: number
  polarity: string
  origin: string
  locator: string
  model_version: string
  document_id: string | null
  filename: string | null
  parent_id: string | null
}

interface Claim {
  finding_key: string
  kind: string
  src_name: string
  dst_name: string
  predicate: string
  directed: boolean
  state: string
  origin: string
  override: string
  policy_version: string
  support_count: number
  conflicts: boolean
  sources: ClaimSource[]
}

interface Evidence {
  link_id: string
  src_name: string
  dst_name: string
  occurrence_count: number
  items: { id: string; kind: string; body: string; parent_id: string | null; created_at: string }[]
  passages: { id: string; document_id: string; filename: string; locator: string; text: string }[]
  summary: { label: string; state: string; origin: string; support_count: number; policy_version: string | null;
    finding_key: string | null; conflicts: boolean } | null
  claims: Claim[]
  review_note: string | null
  reviewed_by: string | null
}

const props = defineProps<{ linkId: string }>()
const emit = defineEmits<{ close: []; openItem: [string] }>()

const router = useRouter()
const evidence = ref<Evidence | null>(null)
const isAdmin = computed(() => store.auth.is_admin)
let requestId = 0
const sourceSlice = (source: ClaimSource, start: number, end: number) => Array.from(source.text).slice(start, end).join('')

/** Jump from the graph to this link's row in the admin curation table. */
function manageLink() {
  emit('close')
  router.push({ path: '/admin/expertise', query: { link: props.linkId } })
}

function openPassage(documentId: string, passageId: string) {
  emit('close')
  router.push({ path: `/documents/${documentId}`, query: { passage: passageId } })
}

function openQuestion(item: Evidence['items'][number]) {
  emit('close')
  router.push(`/questions/${item.kind === 'answer' ? item.parent_id : item.id}`)
}

function openSource(source: ClaimSource) {
  if (source.document_id) return openPassage(source.document_id, source.source_id)
  emit('close')
  if (source.origin === 'question' || source.origin === 'answer') {
    router.push(`/questions/${source.origin === 'answer' ? source.parent_id : source.source_id}`)
  } else emit('openItem', source.source_id)
}

const stateLabel = (state: string) => ({ active: 'Active', held: 'Awaiting evidence', weak: 'Weak association',
  withdrawn: 'Unsupported', suppressed: 'Suppressed' }[state] || state)

async function load() {
  const currentRequest = ++requestId
  try {
    const result = await api.get<Evidence>(`/api/graph/links/${props.linkId}/evidence`)
    if (currentRequest === requestId) evidence.value = result
  } catch (e) {
    if (currentRequest !== requestId) return
    store.fail(e, 'Could not load the evidence for this link')
    emit('close')
  }
}
onMounted(load)
watch(knowledgeRevision, load)
watch(() => props.linkId, load)

const dialogRoot = ref<HTMLElement | null>(null)
useDialog(dialogRoot, () => emit('close'))
</script>

<template>
  <div ref="dialogRoot" role="dialog" aria-modal="true" class="modal-backdrop" @click.self="emit('close')">
    <div class="modal wide" data-testid="evidence-modal">
      <template v-if="evidence">
        <h2>Why these are connected</h2>
        <div v-if="evidence.summary" class="detail-section">
          <strong>{{ evidence.summary.label }}</strong>
          <div class="meta">
            <span class="chip">{{ stateLabel(evidence.summary.state) }}</span>
            <span>{{ evidence.summary.origin === 'manual' ? 'Manual assertion' : evidence.summary.origin === 'legacy' ? 'Shared context' : 'Automatic' }}</span>
            <span v-if="evidence.summary.policy_version">Policy: {{ evidence.summary.policy_version }}</span>
          </div>
          <p v-if="evidence.summary.origin === 'manual'" class="muted">
            This manual decision stays fixed when source evidence changes.
          </p>
          <p v-if="evidence.summary.conflicts" class="muted">The current evidence includes opposing claims.</p>
        </div>
        <p v-else class="muted">This link is hidden because it is suppressed or has no current support.</p>
        <p v-if="evidence.review_note">{{ evidence.reviewed_by || 'Admin' }}: {{ evidence.review_note }}</p>
        <div v-if="evidence.claims.length" class="detail-section">
          <h3>Claims and alternatives</h3>
          <p class="muted">These states describe source support. They do not establish that a claim is true.</p>
          <div v-for="claim in evidence.claims" :key="claim.finding_key" class="correction">
            <strong>{{ claim.src_name }} {{ claim.directed ? '→' : '—' }} {{ claim.dst_name }}: {{ claim.predicate }}</strong>
            <div class="meta">
              <span class="chip">{{ stateLabel(claim.state) }}</span>
              <span>{{ claim.origin }}<template v-if="claim.override !== 'automatic'"> · {{ claim.override }}</template></span>
              <span>{{ claim.support_count }} independent supporting source groups</span>
              <span v-if="claim.conflicts">Opposing evidence</span>
            </div>
            <p class="muted">Policy: {{ claim.policy_version }}</p>
            <div v-for="source in claim.sources" :key="`${source.source_kind}:${source.source_id}:${source.start}:${source.polarity}`" class="detail-section">
              <div class="meta">
                <span>{{ source.filename || source.origin }} {{ source.locator }}</span>
                <span>{{ source.context_source ? 'Similar context; association only' : source.polarity === 'negative' ? 'Opposing claim' : 'Supporting context' }}</span>
                <span>Characters {{ source.start }}–{{ source.end }} (zero-based; end excluded)</span>
              </div>
              <p style="white-space: pre-wrap">{{ sourceSlice(source, Math.max(0, source.start - 120), source.start) }}<mark>{{ source.quote }}</mark>{{ sourceSlice(source, source.end, source.end + 120) }}</p>
              <details><summary class="muted">Source and model version</summary>
                <p class="muted" style="overflow-wrap: anywhere">Source: {{ source.source_id }} · {{ source.source_hash }}<br />Model: {{ source.model_version }}</p>
              </details>
              <button class="btn small" @click="openSource(source)">Open source</button>
            </div>
            <p v-if="!claim.sources.length" class="muted">No current team-visible source supports this claim.</p>
          </div>
        </div>
        <h3 v-if="evidence.claims.length">Other shared context</h3>
        <p>
          <strong>{{ evidence.src_name }}</strong> and <strong>{{ evidence.dst_name }}</strong> are mentioned together in
          {{ evidence.occurrence_count }} team {{ evidence.occurrence_count === 1 ? 'entry' : 'entries' }}.
        </p>

        <div v-if="evidence.items.length" class="detail-section">
          <h3>Contributions</h3>
          <div v-for="item in evidence.items" :key="item.id" class="correction">
            <div class="meta">
              <span class="chip">{{ item.kind.toUpperCase() }}</span>
              <span>{{ new Date(item.created_at).toLocaleDateString() }}</span>
            </div>
            <p>{{ item.body }}</p>
            <button
              v-if="item.kind === 'question' || item.kind === 'answer'"
              class="btn small"
              @click="openQuestion(item)"
            >
              Open question
            </button>
            <button v-else class="btn small" @click="emit('openItem', item.id)">Open contribution</button>
          </div>
        </div>

        <div v-if="evidence.passages.length" class="detail-section">
          <h3>Document passages</h3>
          <div v-for="p in evidence.passages" :key="p.id" class="correction">
            <div class="meta"><span class="chip">{{ p.filename }}</span><span>{{ p.locator }}</span></div>
            <p>{{ p.text }}</p>
            <button class="btn small" @click="openPassage(p.document_id, p.id)">Open exact passage</button>
          </div>
        </div>

        <p v-if="!evidence.items.length && !evidence.passages.length" class="muted">
          No team-visible evidence remains for this link.
        </p>
      </template>
      <div class="modal-actions">
        <button v-if="isAdmin" class="btn" data-testid="manage-link" @click="manageLink">
          Manage this link
        </button>
        <button class="btn" @click="emit('close')">Close</button>
      </div>
    </div>
  </div>
</template>
