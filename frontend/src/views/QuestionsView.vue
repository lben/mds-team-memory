<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError, api, type Item } from '../api'
import { store } from '../store'

interface QuestionDetail extends Item {
  answers: Item[]
  concepts: { id: string; name: string }[]
  suggested_experts: string[]
}

const route = useRoute()
const router = useRouter()

const questions = ref<Item[]>([])
const detail = ref<QuestionDetail | null>(null)
const answerDraft = ref('')
const showComposer = ref(false)
const composerDraft = ref('')

async function loadList() {
  questions.value = await api.get<Item[]>('/api/questions')
}

async function open(id: string) {
  router.push(`/questions/${id}`)
}

async function loadDetail(id: string) {
  detail.value = await api.get<QuestionDetail>(`/api/questions/${id}`)
}

async function postAnswer() {
  if (!detail.value || !answerDraft.value.trim()) return
  await api.post(`/api/questions/${detail.value.id}/answers`, { body: answerDraft.value.trim() })
  answerDraft.value = ''
  store.notify('Answer posted to the whole team')
  await Promise.all([loadDetail(detail.value.id), loadList()])
}

async function accept(answer: Item) {
  if (!detail.value) return
  try {
    await api.post(`/api/questions/${detail.value.id}/accept`, { answer_id: answer.id })
    store.notify('Answer accepted')
    await Promise.all([loadDetail(detail.value.id), loadList()])
  } catch (e) {
    store.notify(e instanceof ApiError ? e.message : 'Could not accept the answer')
  }
}

async function markHelped(answer: Item) {
  try {
    const r = await api.post<{ created: boolean }>(`/api/items/${answer.id}/helped`)
    answer.marked_helped = true
    if (r.created) {
      answer.helped += 1
      store.notify('Contributor impact increased')
    }
  } catch (e) {
    store.notify(e instanceof ApiError ? e.message : 'Could not mark as helpful')
  }
}

async function endorse(answer: Item) {
  try {
    await api.post(`/api/items/${answer.id}/endorse`)
    answer.endorsed = true
    store.notify('Endorsed as an expert')
  } catch (e) {
    store.notify(e instanceof ApiError ? e.message : 'Could not endorse')
  }
}

async function createQuestion() {
  if (!composerDraft.value.trim()) return
  const q = await api.post<Item>('/api/questions', { body: composerDraft.value.trim() })
  composerDraft.value = ''
  showComposer.value = false
  store.notify('Your question is now visible to the whole team')
  await loadList()
  open(q.id)
}

function statusChip(status: string | null) {
  if (status === 'resolved') return 'good'
  if (status === 'open') return 'warn'
  return ''
}

function fmt(ts: string) {
  return new Date(ts).toLocaleString()
}

watch(
  () => route.params.id,
  async (id) => {
    if (typeof id === 'string' && id) await loadDetail(id)
  },
)

onMounted(async () => {
  await loadList()
  const id = route.params.id
  if (typeof id === 'string' && id) await loadDetail(id)
  else if (questions.value.length) await loadDetail(questions.value[0].id)
  if (route.query.ask) showComposer.value = true
})
</script>

<template>
  <section class="page">
    <div class="page-head">
      <div>
        <div class="eyebrow">Q&amp;A</div>
        <h1>Questions</h1>
        <p class="lead">Questions publish immediately. Accepted, Helpful, and SME endorsed are separate signals.</p>
      </div>
      <button class="btn primary" data-testid="new-question" @click="showComposer = true">Ask question</button>
    </div>

    <div class="split">
      <div class="card list-card">
        <div class="list-head"><strong>Team questions</strong></div>
        <div class="list-body" data-testid="question-list">
          <p v-if="!questions.length" class="muted" style="padding: 16px; font-size: 12px">
            No questions yet. Ask the first one.
          </p>
          <div
            v-for="q in questions"
            :key="q.id"
            class="list-item"
            :class="{ active: detail?.id === q.id }"
            @click="open(q.id)"
          >
            <strong>{{ q.body.length > 120 ? q.body.slice(0, 120) + '…' : q.body }}</strong>
            <div class="meta">
              <span class="chip" :class="statusChip(q.question_status)">{{ (q.question_status || 'open').toUpperCase() }}</span>
              <span>{{ q.answer_count ?? 0 }} answers</span>
            </div>
          </div>
        </div>
      </div>

      <div v-if="detail" class="card question-detail" data-testid="question-detail">
        <div class="row between wrap gap8">
          <span class="chip" :class="statusChip(detail.question_status)">{{ (detail.question_status || 'open').toUpperCase() }}</span>
          <span class="muted" style="font-size: 11px">Asked by {{ detail.author }} · {{ fmt(detail.created_at) }}</span>
        </div>
        <h2 style="margin-top: 12px">{{ detail.body.length > 160 ? detail.body.slice(0, 160) + '…' : detail.body }}</h2>
        <div class="meta" style="margin-top: 10px">
          <span v-for="c in detail.concepts" :key="c.id" class="chip">{{ c.name }}</span>
          <span v-if="detail.suggested_experts.length">Suggested experts: {{ detail.suggested_experts.join(', ') }}</span>
        </div>

        <div
          v-for="answer in detail.answers"
          :key="answer.id"
          class="answer"
          :class="{ accepted: answer.id === detail.accepted_answer_id }"
        >
          <div class="answer-toolbar">
            <div class="row gap8 wrap">
              <span v-if="answer.id === detail.accepted_answer_id" class="chip good">ACCEPTED</span>
              <span v-if="answer.endorsed" class="chip good">SME ENDORSED</span>
              <span v-if="answer.contributors > 1" class="chip">CORROBORATED · {{ answer.contributors }}</span>
            </div>
            <span class="muted" style="font-size: 10px">{{ answer.author }} · {{ fmt(answer.created_at) }}</span>
          </div>
          <p>{{ answer.body }}</p>
          <div class="meta"><span>{{ answer.helped }} Helpful marks</span></div>
          <div class="result-actions">
            <button
              class="btn small"
              :class="{ success: answer.marked_helped }"
              :disabled="answer.is_mine"
              @click="markHelped(answer)"
            >
              {{ answer.marked_helped ? '✓ Marked helpful' : '✓ Helped me' }}
            </button>
            <button
              v-if="detail.is_mine && !detail.accepted_answer_id"
              class="btn small"
              data-testid="accept-answer"
              @click="accept(answer)"
            >
              Accept answer
            </button>
            <button v-if="!answer.is_mine && !answer.endorsed" class="btn small" @click="endorse(answer)">
              Endorse as expert
            </button>
          </div>
        </div>

        <div class="answer-compose">
          <h3>Add an answer</h3>
          <textarea
            v-model="answerDraft"
            placeholder="Write the answer you know. No title or tags required."
            data-testid="answer-text"
          ></textarea>
          <div class="row between" style="margin-top: 9px">
            <span class="muted" style="font-size: 10px">Visible to the whole team immediately.</span>
            <button class="btn primary" :disabled="!answerDraft.trim()" data-testid="post-answer" @click="postAnswer">
              Post answer
            </button>
          </div>
        </div>
      </div>
      <div v-else class="card question-detail muted">Select a question, or ask the first one.</div>
    </div>

    <div v-if="showComposer" class="modal-backdrop" @click.self="showComposer = false">
      <div class="modal">
        <h2>Ask the team</h2>
        <p>Your question publishes immediately and routes to mapped experts.</p>
        <textarea
          v-model="composerDraft"
          style="width: 100%; height: 110px; margin-top: 10px"
          placeholder="What do you need to know?"
          data-testid="question-text"
        ></textarea>
        <div class="modal-actions">
          <button class="btn" @click="showComposer = false">Cancel</button>
          <button class="btn primary" :disabled="!composerDraft.trim()" data-testid="post-question" @click="createQuestion">
            Post question
          </button>
        </div>
      </div>
    </div>
  </section>
</template>
