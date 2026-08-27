<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError, api, type Corroboration, type Item } from '../api'
import EvidenceModal from '../components/EvidenceModal.vue'
import ItemDetailModal from '../components/ItemDetailModal.vue'
import KnowledgeGraph from '../components/KnowledgeGraph.vue'
import QuestionCard from '../components/QuestionCard.vue'
import SuccessModal from '../components/SuccessModal.vue'
import { store } from '../store'

interface PassageHit {
  id: string
  document_id: string
  filename: string
  locator: string
  uploader: string
  snippet: string
}
interface ScratchHit {
  scratchpad_id: string
  line: number
  snippet: string
}
interface SearchResults {
  query: string
  concepts: { id: string; name: string }[]
  items: (Item & { snippet: string })[]
  documents: PassageHit[]
  scratchpad: ScratchHit[]
}
type QuestionRow = Item & { answer_count?: number; matches_me?: boolean }

const route = useRoute()
const router = useRouter()

const text = ref('')
const file = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const busy = ref(false)
const feed = ref<Item[]>([])
const questions = ref<QuestionRow[]>([])
const results = ref<SearchResults | null>(null)
const detailId = ref<string | null>(null)
const evidenceLinkId = ref<string | null>(null)
const expandedQuestion = ref<string | null>(null)
const success = ref<{ corroboration: Corroboration; sharedTotal: number } | null>(null)
const graph = ref<InstanceType<typeof KnowledgeGraph> | null>(null)

const focusConceptIds = computed(() => results.value?.concepts.map((c) => c.id) ?? [])

// Right column: while searching, matched questions with accepted answers first;
// otherwise open questions matching your expertise on top, then newest.
const visibleQuestions = computed<QuestionRow[]>(() => {
  if (results.value) {
    const matched = results.value.items.filter((i) => i.kind === 'question') as QuestionRow[]
    return [...matched].sort((a, b) => {
      const aAcc = a.question_status === 'resolved' ? 0 : 1
      const bAcc = b.question_status === 'resolved' ? 0 : 1
      return aAcc - bAcc || b.created_at.localeCompare(a.created_at)
    })
  }
  return [...questions.value].sort((a, b) => {
    const aTop = a.matches_me && a.question_status === 'open' ? 0 : 1
    const bTop = b.matches_me && b.question_status === 'open' ? 0 : 1
    return aTop - bTop || b.created_at.localeCompare(a.created_at)
  })
})

const knowledgeResults = computed(
  () => (results.value?.items.filter((i) => i.kind !== 'question') ?? []) as (Item & { snippet: string })[],
)

async function loadFeed() {
  ;[feed.value, questions.value] = await Promise.all([
    api.get<Item[]>('/api/feed'),
    api.get<QuestionRow[]>('/api/questions'),
  ])
}

async function runSearch() {
  const q = text.value.trim()
  if (!q) return
  busy.value = true
  try {
    results.value = await api.get<SearchResults>(`/api/search?q=${encodeURIComponent(q)}`)
  } catch (e) {
    store.notify(e instanceof ApiError ? e.message : 'Search failed')
  } finally {
    busy.value = false
  }
}

function clearSearch() {
  results.value = null
}

async function ask() {
  const q = text.value.trim()
  if (!q) return void store.notify('Type your question first')
  busy.value = true
  try {
    const question = await api.post<Item>('/api/questions', { body: q })
    text.value = ''
    results.value = null
    store.notify('Your question is now visible to the whole team')
    await loadFeed()
    expandQuestion(question.id)
  } catch (e) {
    store.notify(e instanceof ApiError ? e.message : 'Could not post the question')
  } finally {
    busy.value = false
  }
}

async function capture() {
  const body = text.value.trim()
  if (!body && !file.value) return void store.notify('Write something or attach a document')
  busy.value = true
  try {
    const form = new FormData()
    form.set('body', body)
    if (file.value) form.set('file', file.value)
    const result = await api.postForm<{ item: Item | null; corroboration: Corroboration; shared_total: number }>(
      '/api/capture',
      form,
    )
    text.value = ''
    file.value = null
    if (fileInput.value) fileInput.value.value = ''
    results.value = null
    success.value = {
      corroboration: result.corroboration,
      sharedTotal: result.item ? result.shared_total : 0,
    }
    await Promise.all([loadFeed(), store.loadProfile()])
    graph.value?.refresh() // the graph grows with each contribution
  } catch (e) {
    store.notify(e instanceof ApiError ? e.message : 'Could not save your knowledge')
  } finally {
    busy.value = false
  }
}

async function markHelped(item: Item) {
  try {
    const r = await api.post<{ created: boolean }>(`/api/items/${item.id}/helped`)
    item.marked_helped = true
    if (r.created) {
      item.helped += 1
      store.notify('Contributor impact increased')
    }
  } catch (e) {
    store.notify(e instanceof ApiError ? e.message : 'Could not mark as helpful')
  }
}

async function expandQuestion(id: string) {
  expandedQuestion.value = id
  await nextTick()
  document.getElementById(`question-${id}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
}

async function onChanged() {
  await loadFeed()
  graph.value?.refresh()
}

async function onQuestionDeleted(id: string) {
  expandedQuestion.value = null
  // Search results are a snapshot; drop the deleted question from them too.
  if (results.value) results.value.items = results.value.items.filter((i) => i.id !== id)
  await onChanged()
}

function pickFile() {
  fileInput.value?.click()
}
function onFile(e: Event) {
  file.value = (e.target as HTMLInputElement).files?.[0] ?? null
}
function fmt(ts: string) {
  return new Date(ts).toLocaleString()
}

async function consumeQuery() {
  const q = route.query
  if (!Object.keys(q).length) return
  if (typeof q.question === 'string') {
    await loadFeed() // the question may be newer than the current list
    await expandQuestion(q.question)
  }
  if (typeof q.item === 'string') detailId.value = q.item
  if (typeof q.q === 'string' && q.q) {
    text.value = q.q
    await runSearch()
  }
  router.replace({ path: '/', query: {} })
}

// Deep links (notifications, old bookmarks) must work when Home is already
// mounted, not only on first load.
watch(() => route.query, consumeQuery)

onMounted(async () => {
  await loadFeed()
  await consumeQuery()
})
</script>

<template>
  <section class="page home">
    <KnowledgeGraph
      ref="graph"
      :focus-concept-ids="focusConceptIds"
      @open-item="detailId = $event"
      @open-question="expandQuestion"
      @evidence="evidenceLinkId = $event"
    />

    <div class="card composer">
      <textarea
        v-model="text"
        class="composer-input"
        placeholder="Search the team's memory, ask a question, or share what you know…"
        data-testid="home-input"
        @keydown.enter.exact.prevent="runSearch"
      ></textarea>
      <div class="composer-actions">
        <div class="row gap8">
          <button class="btn" :disabled="busy" data-testid="do-search" @click="runSearch">Search</button>
          <button class="btn" :disabled="busy" data-testid="do-ask" @click="ask">Ask</button>
          <button class="btn primary" :disabled="busy" data-testid="do-capture" @click="capture">Capture</button>
        </div>
        <div class="row gap8">
          <input ref="fileInput" type="file" accept=".pdf,.docx,.txt,.md" style="display: none" @change="onFile" />
          <button class="btn small ghost" @click="pickFile">＋ Attach</button>
          <span class="muted" style="font-size: 11px">{{ file ? file.name : 'Enter searches · Capture is team-shared' }}</span>
        </div>
      </div>
    </div>

    <div v-if="results" class="search-banner" data-testid="search-banner">
      <span>
        Results for “{{ results.query }}”
        <template v-if="results.concepts.length"> · graph focused on {{ results.concepts.map((c) => c.name).join(', ') }}</template>
      </span>
      <button class="btn small" data-testid="clear-search" @click="clearSearch">× Back to latest</button>
    </div>

    <div class="home-columns">
      <!-- Left: knowledge -->
      <div data-testid="knowledge-column">
        <h3 class="col-title">{{ results ? 'Knowledge found' : 'Latest knowledge' }}</h3>

        <template v-if="results">
          <!-- PRD 2: a failed search must be able to become a team question
               without retyping — the text is still in the box above. -->
          <div v-if="!knowledgeResults.length && !results.documents.length && !results.scratchpad.length" class="empty-ask" data-testid="nothing-found">
            <div>
              <h3>Nobody has written this down yet</h3>
              <p>Put it to the team as a question — your text is still in the box above.</p>
            </div>
            <button class="btn primary" data-testid="ask-from-search" @click="ask">Ask this question</button>
          </div>
          <article v-for="item in knowledgeResults" :key="item.id" class="card result">
            <div>
              <div class="row gap8 wrap">
                <span class="chip team">TEAM</span>
                <span class="chip">{{ item.kind.toUpperCase() }}</span>
                <span v-if="item.contributors > 1" class="chip good">{{ item.contributors }} CONTRIBUTORS</span>
                <span v-if="item.endorsed" class="chip good">SME ENDORSED</span>
              </div>
              <p class="body" v-html="item.snippet"></p>
              <div class="meta">
                <span>{{ item.author }}<template v-if="item.contributors > 1"> and {{ item.contributors - 1 }} more</template></span>
                <span>·</span>
                <span>{{ item.helped }} Helpful marks</span>
              </div>
              <div class="result-actions">
                <button class="btn small" :class="{ success: item.marked_helped }" :disabled="item.is_mine" @click="markHelped(item)">
                  {{ item.marked_helped ? '✓ Marked helpful' : '✓ Helped me' }}
                </button>
                <button v-if="item.kind === 'answer'" class="btn small" @click="item.parent_id && expandQuestion(item.parent_id)">
                  Open question
                </button>
                <button class="btn small" @click="detailId = item.id">Details</button>
              </div>
            </div>
          </article>
          <article v-for="hit in results.scratchpad" :key="hit.scratchpad_id + hit.line" class="card result">
            <div>
              <div class="row gap8"><span class="chip private">PRIVATE</span></div>
              <p class="body">“{{ hit.snippet }}”</p>
              <div class="result-actions">
                <button class="btn small" @click="router.push({ path: '/scratchpad', query: { find: results!.query } })">
                  Open in scratchpad
                </button>
              </div>
            </div>
          </article>
          <article v-for="hit in results.documents" :key="hit.id" class="card result">
            <div>
              <div class="row gap8">
                <span class="chip">DOCUMENT</span>
                <span class="chip good">{{ hit.locator.toUpperCase() }}</span>
              </div>
              <h3 style="margin-top: 8px">{{ hit.filename }}</h3>
              <p class="body" v-html="hit.snippet"></p>
              <div class="result-actions">
                <button class="btn small" @click="router.push({ path: `/documents/${hit.document_id}`, query: { passage: hit.id } })">
                  Open exact passage
                </button>
              </div>
            </div>
          </article>
        </template>

        <template v-else>
          <p v-if="!feed.length" class="muted col-empty">Nothing shared yet. Be the first — one useful sentence is enough.</p>
          <article v-for="item in feed" :key="item.id" class="card result" :data-testid="`feed-${item.id}`">
            <div>
              <div class="row gap8 wrap">
                <span class="chip">{{ item.kind.toUpperCase() }}</span>
                <span v-if="item.contributors > 1" class="chip good">{{ item.contributors }} CONTRIBUTORS</span>
                <span v-if="item.endorsed" class="chip good">SME ENDORSED</span>
              </div>
              <p class="body" style="white-space: pre-wrap">{{ item.body.length > 400 ? item.body.slice(0, 400) + '…' : item.body }}</p>
              <div class="meta">
                <span>{{ item.author }}</span>
                <span>·</span>
                <span>{{ fmt(item.created_at) }}</span>
                <span>·</span>
                <span>{{ item.helped }} Helpful marks</span>
              </div>
              <div class="result-actions">
                <button class="btn small" :class="{ success: item.marked_helped }" :disabled="item.is_mine" @click="markHelped(item)">
                  {{ item.marked_helped ? '✓ Marked helpful' : '✓ Helped me' }}
                </button>
                <button v-if="item.kind === 'answer'" class="btn small" @click="item.parent_id && expandQuestion(item.parent_id)">
                  Open question
                </button>
                <button class="btn small" @click="detailId = item.id">Details</button>
              </div>
            </div>
          </article>
        </template>
      </div>

      <!-- Right: questions -->
      <div data-testid="questions-column">
        <h3 class="col-title">{{ results ? 'Questions found' : 'Questions' }}</h3>
        <p v-if="!visibleQuestions.length" class="muted col-empty">
          {{ results ? 'No questions match this search.' : 'No questions yet. Ask the first one above.' }}
        </p>
        <QuestionCard
          v-for="q in visibleQuestions"
          :key="q.id"
          :question="q"
          :expanded="expandedQuestion === q.id"
          @changed="onChanged"
          @deleted="onQuestionDeleted(q.id)"
        />
      </div>
    </div>

    <SuccessModal
      v-if="success"
      :corroboration="success.corroboration"
      :shared-total="success.sharedTotal"
      @close="success = null"
      @view="success = null"
      @another="success = null"
    />
    <ItemDetailModal v-if="detailId" :item-id="detailId" @close="detailId = null" @changed="onChanged" />
    <EvidenceModal v-if="evidenceLinkId" :link-id="evidenceLinkId" @close="evidenceLinkId = null" @open-item="evidenceLinkId = null; detailId = $event" />
  </section>
</template>
