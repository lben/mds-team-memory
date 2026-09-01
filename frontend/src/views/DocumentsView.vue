<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError, api, type Corroboration, type Item } from '../api'
import AskModal from '../components/AskModal.vue'
import SuccessModal from '../components/SuccessModal.vue'
import { useAsk } from '../ask'
import { store } from '../store'

interface Doc {
  id: string
  filename: string
  uploader: string
  is_mine: boolean
  uploaded_at: string
  status: string
  passage_count: number
  passages?: { id: string; ord: number; text: string; locator: string }[]
}

const route = useRoute()
const router = useRouter()
const docs = ref<Doc[]>([])
const current = ref<Doc | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const uploading = ref(false)
const matchedPassage = ref<string | null>(null)
const success = ref<{ corroboration: Corroboration; sharedTotal: number } | null>(null)
// Holds the passage being shared, so a second click cannot post it twice.
const sharing = ref('')

const { ask, askUser, answerAsk } = useAsk()

async function removeDocument() {
  if (!current.value) return
  const answer = await askUser({
    title: `Delete "${current.value.filename}"?`,
    message:
      'The file and its extracted text are removed for good. Passages a teammate has already shared with the team stay, without their link back to this file.',
    confirmLabel: 'Delete document',
    danger: true,
  })
  if (answer === null) return
  try {
    const r = await api.delete<{ kept_shared_excerpts: number }>(`/api/documents/${current.value.id}`)
    store.notify(
      r.kept_shared_excerpts
        ? `Document deleted. ${r.kept_shared_excerpts} shared excerpt${r.kept_shared_excerpts === 1 ? '' : 's'} kept.`
        : 'Document deleted',
    )
    current.value = null
    matchedPassage.value = null
    await load()
    router.push('/documents')
  } catch (e) {
    store.fail(e, 'Could not delete that document')
  }
}

async function load() {
  try {
    docs.value = await api.get<Doc[]>('/api/documents')
  } catch (e) {
    store.fail(e, 'Could not load the document list')
  }
}

async function open(id: string, passage?: string) {
  try {
    current.value = await api.get<Doc>(`/api/documents/${id}`)
  } catch (e) {
    // Leaving the previous document on screen under the new id tells the reader
    // they are looking at a file they are not. Fall back to the page's own
    // "nothing selected" state and say what happened.
    current.value = null
    matchedPassage.value = null
    store.notify(e instanceof ApiError ? e.message : 'Could not open that document')
    return
  }
  matchedPassage.value = passage ?? null
  if (passage) {
    await nextTick()
    document.getElementById(`passage-${passage}`)?.scrollIntoView({ block: 'center' })
  }
}

function pick() {
  fileInput.value?.click()
}

async function upload(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  uploading.value = true
  try {
    const form = new FormData()
    form.set('file', file)
    const doc = await api.postForm<Doc>('/api/documents', form)
    store.notify('Document uploaded and text extracted')
    await load()
    router.push(`/documents/${doc.id}`)
  } catch (err) {
    store.notify(err instanceof ApiError ? err.message : 'Upload failed')
  } finally {
    uploading.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

async function sharePassage(passageId: string) {
  if (sharing.value) return
  sharing.value = passageId
  try {
    const result = await api.post<{ item: Item; corroboration: Corroboration; shared_total: number }>(
      `/api/passages/${passageId}/share`,
      {},
    )
    success.value = { corroboration: result.corroboration, sharedTotal: result.shared_total }
  } catch (e) {
    store.notify(e instanceof ApiError ? e.message : 'Could not share that passage')
  } finally {
    sharing.value = ''
  }
}

function fmt(ts: string) {
  return new Date(ts).toLocaleDateString()
}

watch(
  () => [route.params.id, route.query.passage],
  async ([id, passage]) => {
    if (typeof id === 'string' && id) await open(id, typeof passage === 'string' ? passage : undefined)
  },
)

onMounted(async () => {
  await load()
  const id = route.params.id
  if (typeof id === 'string' && id) {
    await open(id, typeof route.query.passage === 'string' ? route.query.passage : undefined)
  } else if (docs.value.length) {
    await open(docs.value[0].id)
  }
})
</script>

<template>
  <section class="page">
    <AskModal
      v-if="ask"
      :title="ask.title"
      :message="ask.message"
      :input-label="ask.inputLabel"
      :confirm-label="ask.confirmLabel"
      :danger="ask.danger"
      @resolve="answerAsk"
    />
    <div class="page-head">
      <div>
        <div class="eyebrow">Uploaded sources</div>
        <h1>Documents</h1>
        <p class="lead">Upload a file, search its extracted text, and share exact passages as team knowledge.</p>
      </div>
    </div>

    <div class="upload-zone">
      <div>
        <h3>Upload a document</h3>
        <p>PDF, DOCX, TXT, or Markdown · owner and upload date recorded automatically · original file preserved.</p>
      </div>
      <input ref="fileInput" type="file" accept=".pdf,.docx,.txt,.md" style="display: none" @change="upload" />
      <button class="btn primary" :disabled="uploading" data-testid="upload-doc" @click="pick">
        {{ uploading ? 'Uploading…' : 'Upload file' }}
      </button>
    </div>

    <div class="doc-layout">
      <div class="card doc-list">
        <div class="doc-rows" data-testid="doc-list">
          <p v-if="!docs.length" class="muted" style="padding: 16px; font-size: 12px">No documents uploaded yet.</p>
          <div
            v-for="d in docs"
            :key="d.id"
            class="doc-row"
            :class="{ active: current?.id === d.id }"
            @click="router.push(`/documents/${d.id}`)"
          >
            <strong>{{ d.filename }}</strong>
            <p>Uploaded by {{ d.uploader }} · {{ fmt(d.uploaded_at) }}</p>
            <span class="chip good">TEXT EXTRACTED · {{ d.passage_count }} PASSAGES</span>
          </div>
        </div>
      </div>

      <div v-if="current" class="card viewer" data-testid="doc-viewer">
        <div class="viewer-head">
          <div>
            <h2>{{ current.filename }}</h2>
            <div class="meta" style="margin-top: 7px">
              <span>Owner: {{ current.uploader }}</span>
              <span>·</span>
              <span>Uploaded {{ fmt(current.uploaded_at) }}</span>
            </div>
          </div>
          <div class="row gap8">
            <span class="chip good">SOURCE</span>
            <a class="btn small" :href="`/api/documents/${current.id}/file`">Download original</a>
            <button
              v-if="current.is_mine"
              class="btn small ghost"
              data-testid="delete-document"
              @click="removeDocument"
            >
              Delete
            </button>
          </div>
        </div>
        <div class="paper">
          <div
            v-for="p in current.passages"
            :id="`passage-${p.id}`"
            :key="p.id"
            class="passage"
            :class="{ matched: p.id === matchedPassage }"
          >
            <button class="btn small share-passage" :disabled="!!sharing" @click="sharePassage(p.id)">Share this passage</button>
            <div class="locator">{{ p.locator }}</div>
            <p>{{ p.text }}</p>
          </div>
        </div>
      </div>
      <div v-else class="card viewer muted">Upload or select a document.</div>
    </div>

    <SuccessModal
      v-if="success"
      :corroboration="success.corroboration"
      :shared-total="success.sharedTotal"
      @close="success = null"
      @view="success = null; router.push('/')"
      @another="success = null"
    />
  </section>
</template>
