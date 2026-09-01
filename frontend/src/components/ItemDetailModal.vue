<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ApiError, api, type Item } from '../api'
import AskModal from './AskModal.vue'
import { useAsk } from '../ask'
import { store } from '../store'

interface Detail extends Item {
  concepts: { id: string; name: string }[]
  corrections: Item[]
  revisions: { id: string; correction_id: string; note: string; created_at: string }[]
  group_members: Item[]
  source_item_id: string | null
}

const props = defineProps<{ itemId: string }>()
const emit = defineEmits<{ close: []; changed: [] }>()

const detail = ref<Detail | null>(null)
const correctionDraft = ref('')
const error = ref('')
// The draft clears only once the server replies, so an impatient second click
// would otherwise propose the same correction twice.
const busy = ref(false)
const editing = ref(false)
const bodyDraft = ref('')
const savingEdit = ref(false)
const { ask, askUser, answerAsk } = useAsk()

function startEdit() {
  if (!detail.value) return
  bodyDraft.value = detail.value.body
  editing.value = true
}

async function saveEdit() {
  if (savingEdit.value || !detail.value) return
  if (!bodyDraft.value.trim()) return void store.notify('Write something, or cancel the edit')
  savingEdit.value = true
  try {
    await api.put(`/api/items/${detail.value.id}`, { body: bodyDraft.value.trim() })
    editing.value = false
    store.notify('Your contribution was updated')
    await load()
    emit('changed')
  } catch (e) {
    store.notify(e instanceof ApiError ? e.message : 'Could not save your change')
  } finally {
    savingEdit.value = false
  }
}

async function removeItem() {
  if (!detail.value) return
  const answer = await askUser({
    title: 'Delete this contribution?',
    message: 'It is removed for the whole team and cannot be recovered.',
    confirmLabel: 'Delete',
    danger: true,
  })
  if (answer === null) return
  try {
    await api.delete(`/api/items/${detail.value.id}`)
    store.notify('Your contribution was deleted')
    emit('changed')
    emit('close')
  } catch (e) {
    store.notify(e instanceof ApiError ? e.message : 'Could not delete it')
  }
}

async function load() {
  try {
    detail.value = await api.get<Detail>(`/api/items/${props.itemId}`)
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'Could not load the item'
  }
}

async function markHelped() {
  if (!detail.value) return
  await store.markHelped(detail.value)
  emit('changed')
}

async function endorse() {
  if (!detail.value) return
  try {
    await api.post(`/api/items/${detail.value.id}/endorse`)
    store.notify('Endorsed as an expert')
    await load()
  } catch (e) {
    store.notify(e instanceof ApiError ? e.message : 'Could not endorse')
  }
}

async function submitCorrection() {
  if (busy.value || !detail.value || !correctionDraft.value.trim()) return
  busy.value = true
  try {
    await api.post(`/api/items/${detail.value.id}/corrections`, { body: correctionDraft.value.trim() })
    correctionDraft.value = ''
    store.notify('Correction proposed')
    await load()
  } catch (e) {
    store.notify(e instanceof ApiError ? e.message : 'Could not propose the correction')
  } finally {
    busy.value = false
  }
}

async function adopt(correctionId: string) {
  try {
    await api.post(`/api/corrections/${correctionId}/adopt`)
    store.notify('Correction adopted')
    await load()
    emit('changed')
  } catch (e) {
    store.notify(e instanceof ApiError ? e.message : 'Could not adopt the correction')
  }
}

function fmt(ts: string) {
  return new Date(ts).toLocaleDateString()
}

onMounted(load)
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <AskModal
      v-if="ask"
      :title="ask.title"
      :message="ask.message"
      :input-label="ask.inputLabel"
      :confirm-label="ask.confirmLabel"
      :danger="ask.danger"
      @resolve="answerAsk"
    />
    <div class="modal wide" data-testid="item-detail">
      <p v-if="error" class="form-error">{{ error }}</p>
      <template v-if="detail">
        <div class="row gap8 wrap">
          <span class="chip" :class="detail.visibility === 'team' ? 'team' : 'private'">{{ detail.visibility.toUpperCase() }}</span>
          <span class="chip">{{ detail.kind.toUpperCase() }}</span>
          <span v-if="detail.contributors > 1" class="chip good">{{ detail.contributors }} CONTRIBUTORS</span>
          <span v-if="detail.endorsed" class="chip good">
            ENDORSED{{ detail.endorsements > 1 ? ` ×${detail.endorsements}` : '' }}
          </span>
          <span v-for="c in detail.concepts" :key="c.id" class="chip">{{ c.name }}</span>
        </div>
        <div v-if="!editing" class="detail-body" style="margin-top: 14px">{{ detail.body }}</div>
        <div v-else style="margin-top: 14px">
          <textarea
            v-model="bodyDraft"
            style="width: 100%; height: 120px"
            data-testid="edit-body"
          ></textarea>
          <div class="modal-actions" style="margin-top: 8px">
            <button class="btn small" data-testid="cancel-edit" @click="editing = false">Cancel</button>
            <button class="btn small primary" :disabled="savingEdit" data-testid="save-edit" @click="saveEdit">
              Save change
            </button>
          </div>
        </div>
        <div class="meta" style="margin-top: 12px">
          <span>{{ detail.author }}<template v-if="!detail.author_verified"> · no account</template></span>
          <span>·</span>
          <span>Created {{ fmt(detail.created_at) }}</span>
          <span>·</span>
          <span>Updated {{ fmt(detail.updated_at) }}</span>
          <span>·</span>
          <span>{{ detail.helped }} Helpful marks</span>
          <!-- The excerpt is team-visible like anything else shared; only the
               fact that it came from your scratchpad is private. Saying
               "private" without that distinction read as a broken promise. -->
          <span v-if="detail.source_item_id">
            · You shared this from your scratchpad — the excerpt is team-visible, and only you
            can see where it came from
          </span>
        </div>
        <div class="result-actions">
          <button class="btn small" :class="{ success: detail.marked_helped }" :disabled="detail.is_mine" @click="markHelped">
            {{ detail.marked_helped ? '✓ Marked helpful' : '✓ Helped me' }}
          </button>
          <button
            v-if="!detail.is_mine && !detail.endorsed_by_me"
            class="btn small"
            data-testid="endorse"
            @click="endorse"
          >
            Endorse as expert
          </button>
          <button v-if="detail.is_mine && !editing" class="btn small" data-testid="edit-item" @click="startEdit">Edit</button>
          <button v-if="detail.is_mine" class="btn small ghost" data-testid="delete-item" @click="removeItem">Delete</button>
          <router-link
            v-if="detail.source_document_id"
            class="btn small"
            :to="{ path: `/documents/${detail.source_document_id}`, query: detail.source_passage_id ? { passage: detail.source_passage_id } : {} }"
            @click="emit('close')"
            >Open source document</router-link
          >
        </div>

        <div v-if="detail.group_members.length > 1" class="detail-section">
          <h3>{{ detail.group_members.length }} corroborating contributions</h3>
          <p class="muted" style="font-size: 12px">
            Very similar knowledge from {{ detail.contributors }} contributor{{ detail.contributors === 1 ? '' : 's' }}. Each original is preserved.
          </p>
          <div v-for="m in detail.group_members" :key="m.id" class="correction">
            <div class="meta"><span>{{ m.author }}</span><span>·</span><span>{{ fmt(m.created_at) }}</span></div>
            <p>{{ m.body }}</p>
          </div>
        </div>

        <div class="detail-section">
          <h3>Corrections</h3>
          <div v-for="c in detail.corrections" :key="c.id" class="correction" :class="{ adopted: c.correction_state === 'adopted' }">
            <div class="answer-toolbar">
              <span class="chip" :class="c.correction_state === 'adopted' ? 'good' : 'warn'">
                {{ c.correction_state === 'adopted' ? 'ADOPTED' : 'PROPOSED' }}
              </span>
              <span class="muted" style="font-size: 10px">{{ c.author }} · {{ fmt(c.created_at) }}</span>
            </div>
            <p>{{ c.body }}</p>
            <button
              v-if="c.correction_state !== 'adopted' && detail.is_mine"
              class="btn small"
              @click="adopt(c.id)"
            >
              Adopt correction
            </button>
          </div>
          <textarea
            v-model="correctionDraft"
            style="width: 100%; height: 70px; margin-top: 10px"
            placeholder="Suggest a correction or an update to this knowledge"
          ></textarea>
          <div class="modal-actions" style="margin-top: 8px">
            <button class="btn small" :disabled="busy || !correctionDraft.trim()" @click="submitCorrection">Propose correction</button>
          </div>
        </div>

        <div v-if="detail.revisions.length" class="detail-section">
          <h3>Revision history</h3>
          <div v-for="r in detail.revisions" :key="r.id" class="meta" style="margin-top: 6px">
            <span>{{ fmt(r.created_at) }}</span><span>·</span><span>{{ r.note }}</span>
          </div>
        </div>
      </template>
      <div class="modal-actions">
        <button class="btn" @click="emit('close')">Close</button>
      </div>
    </div>
  </div>
</template>
