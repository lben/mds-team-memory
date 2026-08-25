<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError, api, type Corroboration, type Item } from '../api'
import SuccessModal from '../components/SuccessModal.vue'
import { store } from '../store'

const route = useRoute()
const router = useRouter()

const body = ref((route.query.prefill as string) || '')
const file = ref<File | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const sharing = ref(false)
const success = ref<{ corroboration: Corroboration; item: Item | null } | null>(null)
const matching = ref<Item[]>([])

function pickFile() {
  fileInput.value?.click()
}
function onFile(e: Event) {
  file.value = (e.target as HTMLInputElement).files?.[0] ?? null
}

async function share() {
  if (!body.value.trim() && !file.value) {
    store.notify('Write something or attach a document')
    return
  }
  sharing.value = true
  try {
    const form = new FormData()
    form.set('body', body.value)
    if (file.value) form.set('file', file.value)
    const result = await api.postForm<{ item: Item | null; corroboration: Corroboration; document_id: string | null }>(
      '/api/capture',
      form,
    )
    success.value = { corroboration: result.corroboration, item: result.item }
    await store.loadProfile()
  } catch (e) {
    store.notify(e instanceof ApiError ? e.message : 'Could not save your knowledge')
  } finally {
    sharing.value = false
  }
}

function viewContribution() {
  const item = success.value?.item
  success.value = null
  if (item) router.push({ path: '/search', query: { item: item.id, q: item.body.split(/\s+/).slice(0, 6).join(' ') } })
  else router.push('/documents')
}

function addAnother() {
  success.value = null
  body.value = ''
  file.value = null
  if (fileInput.value) fileInput.value.value = ''
}

onMounted(async () => {
  try {
    matching.value = await api.get<Item[]>('/api/questions?mine_expertise=true')
  } catch {
    matching.value = []
  }
})
</script>

<template>
  <section class="page">
    <div class="page-head">
      <div>
        <div class="eyebrow">/capture</div>
        <h1>Capture knowledge</h1>
        <p class="lead">One useful sentence is enough. Share the small detail now, before it disappears.</p>
      </div>
    </div>

    <div class="card capture-box">
      <div class="capture-head">
        <div>
          <h3>Quick Capture</h3>
          <p>Team-shared by default.</p>
        </div>
        <span class="chip team">TEAM-SHARED</span>
      </div>
      <textarea v-model="body" class="capture-textarea" placeholder="Add your knowledge..." data-testid="capture-text"></textarea>
      <div class="capture-footer">
        <div class="row gap8">
          <button class="btn" @click="pickFile">＋ Attach a file</button>
          <input ref="fileInput" type="file" accept=".pdf,.docx,.txt,.md" style="display: none" @change="onFile" />
          <span class="note">{{ file ? file.name : 'No title, tags, folder, or content type required.' }}</span>
        </div>
        <button class="btn primary" :disabled="sharing" data-testid="share-knowledge" @click="share">Share knowledge</button>
      </div>
    </div>

    <div class="grid-2" style="margin-top: 16px">
      <div class="card stat-card">
        <h3>Your knowledge is helping</h3>
        <p class="muted" style="font-size: 11px">Impact appears only when teammates use your contributions.</p>
        <div class="stats">
          <div class="stat"><strong>{{ store.profile?.totals.helped ?? 0 }}</strong><span>Helpful marks</span></div>
          <div class="stat"><strong>{{ store.profile?.totals.accepted ?? 0 }}</strong><span>Accepted answers</span></div>
          <div class="stat"><strong>{{ store.profile?.totals.corrections ?? 0 }}</strong><span>Adopted corrections</span></div>
        </div>
      </div>
      <div class="card stat-card">
        <h3>Open questions matching you</h3>
        <p class="muted" style="font-size: 11px">Based on your pilot expertise profile.</p>
        <div v-if="!matching.length" class="muted" style="font-size: 12px; margin-top: 10px">
          No matching open questions right now.
        </div>
        <div
          v-for="q in matching.slice(0, 3)"
          :key="q.id"
          class="question-mini"
          :class="{ featured: q.question_status === 'open' }"
          @click="$router.push(`/questions/${q.id}`)"
        >
          <strong>{{ q.body.length > 90 ? q.body.slice(0, 90) + '…' : q.body }}</strong>
          <span>{{ q.question_status?.toUpperCase() }} · {{ q.answer_count ?? 0 }} answers</span>
        </div>
      </div>
    </div>

    <SuccessModal
      v-if="success"
      :corroboration="success.corroboration"
      @close="success = null"
      @view="viewContribution"
      @another="addAnother"
    />
  </section>
</template>
