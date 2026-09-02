<script setup lang="ts">
import { useDialog } from '../dialog'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { store } from '../store'

interface Evidence {
  link_id: string
  src_name: string
  dst_name: string
  occurrence_count: number
  items: { id: string; kind: string; body: string; parent_id: string | null; created_at: string }[]
  passages: { id: string; document_id: string; filename: string; locator: string; text: string }[]
}

const props = defineProps<{ linkId: string }>()
const emit = defineEmits<{ close: []; openItem: [string] }>()

const router = useRouter()
const evidence = ref<Evidence | null>(null)
const isAdmin = computed(() => store.auth.is_admin)

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

onMounted(async () => {
  try {
    evidence.value = await api.get<Evidence>(`/api/graph/links/${props.linkId}/evidence`)
  } catch (e) {
    store.fail(e, 'Could not load the evidence for this link')
    emit('close')
  }
})

const dialogRoot = ref<HTMLElement | null>(null)
useDialog(dialogRoot, () => emit('close'))
</script>

<template>
  <div ref="dialogRoot" role="dialog" aria-modal="true" class="modal-backdrop" @click.self="emit('close')">
    <div class="modal wide" data-testid="evidence-modal">
      <template v-if="evidence">
        <h2>Why these are connected</h2>
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
