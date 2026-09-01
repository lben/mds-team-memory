<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ApiError, api, type Corroboration, type Item } from '../api'
import SuccessModal from '../components/SuccessModal.vue'
import { store } from '../store'
import AskModal from '../components/AskModal.vue'
import { useAsk } from '../ask'

interface Pad {
  id: string
  name: string
  is_default: boolean
  content: string
  updated_at: string
}

const route = useRoute()
const pads = ref<Pad[]>([])
const current = ref<Pad | null>(null)
const editor = ref<HTMLTextAreaElement | null>(null)
const saveState = ref<'saved' | 'saving' | 'idle'>('idle')
const findQuery = ref((route.query.find as string) || '')
const matches = ref<{ line: number; text: string }[]>([])
const selection = ref('')
// The selection clears only once the share succeeds, so without this a second
// click would post the same private excerpt to the team twice.
const sharing = ref(false)
const success = ref<{ corroboration: Corroboration; sharedTotal: number } | null>(null)
let saveTimer = 0

const { ask, askUser, answerAsk } = useAsk()

async function load() {
  try {
    const data = await api.get<{ default: Pad; others: Pad[] }>('/api/scratchpad')
    pads.value = [data.default, ...data.others]
    current.value = pads.value[0]
  } catch (e) {
    store.fail(e, 'Could not load your scratchpad')
  }
}

function onEdit() {
  saveState.value = 'saving'
  window.clearTimeout(saveTimer)
  saveTimer = window.setTimeout(save, 800)
}

async function save() {
  if (!current.value) return
  try {
    await api.put(`/api/scratchpad/${current.value.id}`, { content: current.value.content })
    saveState.value = 'saved'
  } catch (e) {
    saveState.value = 'idle'
    store.notify(e instanceof ApiError ? e.message : 'Could not save')
  }
}

async function find() {
  if (!current.value || !findQuery.value.trim()) {
    matches.value = []
    return
  }
  let data: { matches: typeof matches.value }
  try {
    data = await api.get<{ matches: typeof matches.value }>(
      `/api/scratchpad/${current.value.id}/find?q=${encodeURIComponent(findQuery.value.trim())}`,
    )
  } catch (e) {
    matches.value = []
    return void store.fail(e, 'Could not search your scratchpad')
  }
  matches.value = data.matches
}

function jumpTo(line: number) {
  const el = editor.value
  if (!el || !current.value) return
  const lines = current.value.content.split('\n')
  const position = lines.slice(0, line - 1).join('\n').length + (line > 1 ? 1 : 0)
  el.focus()
  el.setSelectionRange(position, position + (lines[line - 1]?.length ?? 0))
  const lineHeight = 23.4 // 13px * 1.8 line-height
  el.scrollTop = Math.max(0, (line - 3) * lineHeight)
}

function updateSelection() {
  const el = editor.value
  if (!el) return
  selection.value = el.value.substring(el.selectionStart, el.selectionEnd).trim()
}

async function shareSelection() {
  if (sharing.value || !current.value || !selection.value) return
  sharing.value = true
  try {
    window.clearTimeout(saveTimer)
    await save()
    const result = await api.post<{ item: Item; corroboration: Corroboration; shared_total: number }>(
      `/api/scratchpad/${current.value.id}/share`,
      { text: selection.value },
    )
    success.value = { corroboration: result.corroboration, sharedTotal: result.shared_total }
    selection.value = ''
  } catch (e) {
    store.notify(e instanceof ApiError ? e.message : 'Could not share that excerpt')
  } finally {
    sharing.value = false
  }
}

async function createPad() {
  const name = await askUser({
    title: 'New scratchpad',
    inputLabel: 'Name for the additional scratchpad',
    confirmLabel: 'Create',
  })
  if (name === null) return
  if (!name.trim()) return void store.notify('Give the scratchpad a name')
  try {
    await api.post<Pad>('/api/scratchpad', { name: name.trim() })
  } catch (e) {
    return void store.fail(e, 'Could not create that scratchpad')
  }
  await load()
  current.value = pads.value[pads.value.length - 1]
}

function switchPad(id: string) {
  const pad = pads.value.find((p) => p.id === id)
  if (pad) {
    current.value = pad
    matches.value = []
  }
}

onMounted(async () => {
  await load()
  if (findQuery.value) await find()
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
        <div class="eyebrow">Private</div>
        <h1>My scratchpad</h1>
        <p class="lead">
          Long, searchable files for random knowledge. No notes, titles, folders, or
          categorization required — start with this one and add more if you want them.
        </p>
        <p v-if="!store.profile?.verified" class="lead" style="color: var(--accent)">
          You have no account, so this file lives in this browser only. Clearing your cookies
          destroys it, and nobody can get it back for you.
        </p>
      </div>
      <div class="row gap8">
        <!-- What "private" is tied to changed when accounts arrived: with an
             account it follows you, without one it dies with the cookie. -->
        <span class="chip private" data-testid="privacy-chip">
          {{ store.profile?.verified ? 'PRIVATE TO YOU' : 'PRIVATE TO THIS BROWSER ONLY' }}
        </span>
      </div>
    </div>

    <div class="card">
      <div class="scratch-toolbar">
        <select v-if="pads.length > 1" :value="current?.id" @change="switchPad(($event.target as HTMLSelectElement).value)">
          <option v-for="p in pads" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
        <input
          v-model="findQuery"
          type="search"
          placeholder="Find anything in your scratchpad"
          data-testid="scratch-find"
          @keyup.enter="find"
        />
        <button class="btn" @click="find">Find</button>
        <span class="autosave">{{ saveState === 'saving' ? '● Saving…' : saveState === 'saved' ? '● Saved' : '● Saves as you type' }}</span>
      </div>
      <div class="scratch-layout">
        <div class="scratch-editor-wrap">
          <textarea
            v-if="current"
            ref="editor"
            v-model="current.content"
            class="scratch-editor"
            spellcheck="false"
            placeholder="Append anything worth remembering: URLs, commands, contacts, timings…"
            data-testid="scratch-editor"
            @input="onEdit"
            @mouseup="updateSelection"
            @keyup="updateSelection"
          ></textarea>
        </div>
        <aside class="scratch-side">
          <h3>{{ matches.length ? `${matches.length} matches` : 'Find results' }}</h3>
          <p v-if="!matches.length" class="muted" style="font-size: 11px">
            Search your whole scratchpad with plain keywords.
          </p>
          <div v-for="m in matches" :key="m.line" class="find-hit" style="cursor: pointer" @click="jumpTo(m.line)">
            <strong>Line {{ m.line }}</strong>{{ m.text }}
          </div>
          <div class="scratch-hint">
            <strong>Share only what matters.</strong><br />
            Select any text in this file and share that excerpt. The full scratchpad stays private. Company administrators
            may still have system-level access.
          </div>
          <button
            class="btn primary"
            style="width: 100%; margin-top: 12px"
            :disabled="sharing || !selection"
            data-testid="share-selection"
            @click="shareSelection"
          >
            Share selected knowledge
          </button>
          <button class="btn ghost small" style="margin-top: 10px" @click="createPad">＋ Create another scratchpad</button>
        </aside>
      </div>
    </div>

    <SuccessModal
      v-if="success"
      :corroboration="success.corroboration"
      :shared-total="success.sharedTotal"
      @close="success = null"
      @view="success = null; $router.push('/')"
      @another="success = null"
    />
  </section>
</template>
