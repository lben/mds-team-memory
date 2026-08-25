<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError, api, type Item } from '../api'
import ItemDetailModal from '../components/ItemDetailModal.vue'
import { store } from '../store'

interface PassageHit {
  type: string
  id: string
  document_id: string
  filename: string
  locator: string
  uploader: string
  uploaded_at: string
  snippet: string
}
interface ScratchHit {
  type: string
  scratchpad_id: string
  line: number
  snippet: string
}
interface Results {
  query: string
  items: (Item & { snippet: string })[]
  documents: PassageHit[]
  scratchpad: ScratchHit[]
}

const route = useRoute()
const router = useRouter()
const query = ref((route.query.q as string) || '')
const results = ref<Results | null>(null)
const searching = ref(false)
const detailId = ref<string | null>(null)

async function runSearch() {
  const q = query.value.trim()
  if (!q) return
  searching.value = true
  try {
    results.value = await api.get<Results>(`/api/search?q=${encodeURIComponent(q)}`)
  } catch (e) {
    store.notify(e instanceof ApiError ? e.message : 'Search failed')
  } finally {
    searching.value = false
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

async function askQuestion() {
  const q = (results.value?.query || query.value).trim()
  if (!q) return
  const question = await api.post<Item>('/api/questions', { body: q })
  store.notify('Your question is now visible to the whole team')
  router.push(`/questions/${question.id}`)
}

function shareKnowledge() {
  router.push({ path: '/capture', query: { prefill: query.value } })
}

onMounted(() => {
  if (route.query.item) detailId.value = route.query.item as string
  if (query.value) runSearch()
})
</script>

<template>
  <section class="page">
    <div class="page-head">
      <div>
        <div class="eyebrow">Find first</div>
        <h1>Search team memory</h1>
        <p class="lead">
          Keyword search across your private scratchpad, team knowledge, questions, answers, and uploaded documents.
        </p>
      </div>
    </div>

    <div class="card search-panel">
      <input
        v-model="query"
        class="search-input"
        type="search"
        placeholder="Search team memory… use quotes for exact phrases"
        aria-label="Search"
        data-testid="search-input"
        @keyup.enter="runSearch"
      />
      <button class="btn primary" :disabled="searching" data-testid="run-search" @click="runSearch">Search</button>
    </div>

    <div v-if="results" class="result-list" data-testid="search-results">
      <p v-if="!results.items.length && !results.documents.length && !results.scratchpad.length" class="muted">
        No matches in team knowledge, documents, or your scratchpad.
      </p>

      <article v-for="item in results.items" :key="item.id" class="card result">
        <div>
          <div class="row gap8 wrap">
            <span class="chip team">TEAM</span>
            <span class="chip">{{ item.kind.toUpperCase() }}</span>
            <span v-if="item.contributors > 1" class="chip good">{{ item.contributors }} CONTRIBUTORS</span>
            <span v-if="item.endorsed" class="chip good">SME ENDORSED</span>
            <span v-if="item.question_status" class="chip" :class="item.question_status === 'resolved' ? 'good' : 'warn'">
              {{ item.question_status.toUpperCase() }}
            </span>
          </div>
          <p class="body" v-html="item.snippet"></p>
          <div class="meta">
            <span>{{ item.author }}<template v-if="item.contributors > 1"> and {{ item.contributors - 1 }} more</template></span>
            <span>·</span>
            <span>Updated {{ new Date(item.updated_at).toLocaleDateString() }}</span>
            <span>·</span>
            <span>{{ item.helped }} Helpful marks</span>
          </div>
          <div class="result-actions">
            <button
              class="btn small"
              :class="{ success: item.marked_helped }"
              :disabled="item.is_mine"
              @click="markHelped(item)"
            >
              {{ item.marked_helped ? '✓ Marked helpful' : '✓ Helped me' }}
            </button>
            <button
              v-if="item.kind === 'question' || item.kind === 'answer'"
              class="btn small"
              @click="router.push(`/questions/${item.kind === 'answer' ? item.parent_id : item.id}`)"
            >
              Open question
            </button>
            <button class="btn small" @click="detailId = item.id">
              {{ item.group_size > 1 ? `View all ${item.group_size} contributions` : 'Details & corrections' }}
            </button>
          </div>
        </div>
        <aside v-if="item.contributors > 1" class="side-summary">
          <strong>{{ item.contributors >= 3 ? 'High' : 'Growing' }} confidence</strong>
          <span>{{ item.contributors }} independent contributors</span>
          <span style="margin-top: 10px">No SME endorsement required to use it.</span>
        </aside>
      </article>

      <article v-for="hit in results.scratchpad" :key="hit.scratchpad_id + hit.line" class="card result">
        <div>
          <div class="row gap8"><span class="chip private">PRIVATE</span></div>
          <h3 style="margin-top: 10px">Scratchpad match</h3>
          <p class="body">“{{ hit.snippet }}”</p>
          <div class="meta"><span>Your private scratchpad</span><span>·</span><span>Line {{ hit.line }}</span></div>
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
          <h3 style="margin-top: 10px">{{ hit.filename }}</h3>
          <p class="body" v-html="hit.snippet"></p>
          <div class="meta">
            <span>Uploaded by {{ hit.uploader }}</span>
            <span>·</span>
            <span>{{ new Date(hit.uploaded_at).toLocaleDateString() }}</span>
          </div>
          <div class="result-actions">
            <button
              class="btn small"
              @click="router.push({ path: `/documents/${hit.document_id}`, query: { passage: hit.id } })"
            >
              Open exact passage
            </button>
          </div>
        </div>
      </article>

      <div class="empty-ask">
        <div>
          <h3>{{ results.items.length || results.documents.length ? 'Still missing the answer?' : 'No useful result?' }}</h3>
          <p>The same search text can become a team-visible question without retyping.</p>
        </div>
        <div class="row gap8">
          <button class="btn" @click="shareKnowledge">Share knowledge instead</button>
          <button class="btn primary" data-testid="ask-from-search" @click="askQuestion">Ask this question</button>
        </div>
      </div>
    </div>

    <ItemDetailModal v-if="detailId" :item-id="detailId" @close="detailId = null" @changed="runSearch" />
  </section>
</template>
