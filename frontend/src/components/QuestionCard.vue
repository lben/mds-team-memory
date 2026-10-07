<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { api, type Item, type TopicFeedbackContext } from '../api'
import { knowledgeRevision, store } from '../store'
import AskModal from './AskModal.vue'
import { useAsk } from '../ask'
import HelpfulActions from './HelpfulActions.vue'
import ProcessingBar from './ProcessingBar.vue'
import { track } from '../processing'
import TopicCreditPanel from './TopicCreditPanel.vue'

interface QuestionDetail extends Item {
  answers: Item[]
  concepts: { id: string; name: string }[]
  suggested_experts: string[]
}

const { ask, askUser, answerAsk } = useAsk()

const props = defineProps<{ question: Item & { answer_count?: number; matches_me?: boolean }; expanded?: boolean }>()
const emit = defineEmits<{ changed: []; deleted: [] }>()

const open = ref(props.expanded ?? false)
const detail = ref<QuestionDetail | null>(null)
const answerDraft = ref('')
// The draft is only cleared once the server replies, so without this an
// impatient second click posts the same answer twice.
const busy = ref(false)
const topicAnswerId = ref<string | null>(null)
const acceptedTopics = ref<TopicFeedbackContext | null>(null)
let feedbackTrigger: HTMLElement | null = null

async function loadDetail() {
  try {
    detail.value = await api.get<QuestionDetail>(`/api/questions/${props.question.id}`)
    acceptedTopics.value = null
    if (detail.value.is_mine && detail.value.accepted_answer_id) {
      const answerId = detail.value.accepted_answer_id
      const feedback = await api.get<TopicFeedbackContext>(`/api/items/${answerId}/topic-feedback`)
      if (detail.value?.accepted_answer_id === answerId) acceptedTopics.value = feedback
    }
  } catch (e) {
    store.fail(e, 'Could not open that question')
  }
}

async function toggle() {
  open.value = !open.value
  if (open.value && !detail.value) await loadDetail()
}

async function postAnswer() {
  if (busy.value || !detail.value || !answerDraft.value.trim()) return
  busy.value = true
  try {
    const answer = await api.post<Item>(`/api/questions/${detail.value.id}/answers`, { body: answerDraft.value.trim() })
    track(answer.id)
    answerDraft.value = ''
    store.notify('Answer posted to the whole team')
    await loadDetail()
    emit('changed')
  } catch (e) {
    store.fail(e, 'Could not post the answer')
  } finally {
    busy.value = false
  }
}

function openTopicFeedback(answer: Item, event: Event) {
  feedbackTrigger = event.currentTarget as HTMLElement
  topicAnswerId.value = answer.id
}

async function closeTopicFeedback() {
  topicAnswerId.value = null
  await nextTick()
  feedbackTrigger?.focus()
}

async function topicFeedbackSaved() {
  const newlyAccepted = !detail.value?.accepted_answer_id
  await loadDetail()
  emit('changed')
  if (newlyAccepted) {
    store.notify('Answer accepted')
    topicAnswerId.value = null
    await nextTick()
    document.getElementById(`accepted-feedback-${props.question.id}`)?.focus()
  }
}

async function deleteQuestion() {
  const answer = await askUser({
    title: 'Delete this question?',
    message: 'This is permanent, and only works while nobody has answered it.',
    confirmLabel: 'Delete question',
    danger: true,
  })
  if (answer === null) return
  try {
    await api.delete(`/api/questions/${props.question.id}`)
    store.notify('Question deleted')
    emit('deleted')
  } catch (e) {
    store.fail(e, 'Could not delete the question')
  }
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
  () => props.expanded,
  async (v) => {
    if (v) {
      open.value = true
      if (!detail.value) await loadDetail()
    }
  },
)
if (open.value) loadDetail()
watch(knowledgeRevision, () => { if (open.value) loadDetail() })
</script>

<template>
  <article :id="`question-${question.id}`" class="card question-card" :data-testid="`question-${question.id}`">
    <AskModal
      v-if="ask"
      :title="ask.title"
      :message="ask.message"
      :input-label="ask.inputLabel"
      :confirm-label="ask.confirmLabel"
      :danger="ask.danger"
      @resolve="answerAsk"
    />
    <div class="q-head" @click="toggle">
      <ProcessingBar :item-id="question.id" />
      <div class="row gap8 wrap">
        <span class="chip" :class="statusChip(question.question_status)">
          {{ (question.question_status || 'open').toUpperCase() }}
        </span>
        <span v-if="question.matches_me && question.question_status === 'open'" class="chip team">NEEDS YOUR EXPERTISE</span>
      </div>
      <strong class="q-body">{{ question.body.length > 140 ? question.body.slice(0, 140) + '…' : question.body }}</strong>
      <div class="meta">
        <span>Asked by {{ question.author }}</span>
        <span>·</span>
        <span>{{ (detail ? detail.answers.length : question.answer_count) ?? 0 }} answers</span>
        <span>·</span>
        <span>{{ fmt(question.created_at) }}</span>
        <span class="q-toggle">{{ open ? '▲' : '▼' }}</span>
      </div>
    </div>

    <div v-if="open && detail" class="q-detail">
      <div v-if="detail.body.length > 140" class="body" style="white-space: pre-wrap">{{ detail.body }}</div>
      <div class="meta" style="margin-top: 6px">
        <span v-for="c in detail.concepts" :key="c.id" class="chip">{{ c.name }}</span>
        <span v-if="detail.suggested_experts.length">Suggested experts: {{ detail.suggested_experts.join(', ') }}</span>
      </div>

      <div
        v-for="answer in detail.answers"
        :key="answer.id"
        class="answer"
        :class="{ accepted: answer.id === detail.accepted_answer_id }"
      >
        <ProcessingBar :item-id="answer.id" />
        <div class="answer-toolbar">
          <div class="row gap8 wrap">
            <span v-if="answer.id === detail.accepted_answer_id" class="chip good">ACCEPTED</span>
            <span v-if="answer.endorsed" class="chip good">ENDORSED{{ answer.endorsements > 1 ? ` ×${answer.endorsements}` : '' }}</span>
          </div>
          <span class="muted" style="font-size: 10px">{{ answer.author }} · {{ fmt(answer.created_at) }}</span>
        </div>
        <p>{{ answer.body }}</p>
        <div class="meta"><span>{{ answer.helped }} Helpful marks</span></div>
        <div class="result-actions">
          <HelpfulActions :item="answer" @changed="emit('changed')" />
          <button
            v-if="detail.is_mine && !detail.accepted_answer_id"
            class="btn small"
            data-testid="accept-answer"
            @click="openTopicFeedback(answer, $event)"
          >
            Accept answer
          </button>
          <button
            v-if="detail.is_mine && detail.accepted_answer_id === answer.id"
            :id="`accepted-feedback-${question.id}`"
            class="btn small"
            data-testid="edit-accepted-topics"
            @click="openTopicFeedback(answer, $event)"
          >Edit topic feedback</button>
          <button v-if="!answer.is_mine && !answer.endorsed_by_me && answer.author_verified" class="btn small" @click="store.endorse(answer)">
            Endorse contribution
          </button>
        </div>
        <p v-if="detail.is_mine && answer.id === detail.accepted_answer_id && acceptedTopics" class="muted" data-testid="accepted-topic-summary">
          <template v-if="acceptedTopics.feedback.accepted.some(t => t.state === 'current')">Resolved topics: {{ acceptedTopics.feedback.accepted.filter(t => t.state === 'current').map(t => t.name).join(', ') }}</template>
          <template v-else-if="acceptedTopics.feedback.accepted.some(t => t.state === 'stale')">The answer or topic changed. Topic feedback needs confirmation.</template>
          <template v-else>Accepted · no topic confirmed</template>
        </p>
        <TopicCreditPanel
          v-if="topicAnswerId === answer.id"
          :item-id="answer.id"
          kind="accepted"
          :accept-question-id="!detail.accepted_answer_id ? detail.id : undefined"
          @close="closeTopicFeedback"
          @saved="topicFeedbackSaved"
        />
      </div>

      <div class="answer-compose">
        <textarea
          v-model="answerDraft"
          placeholder="Write the answer you know. No title or tags required."
          data-testid="answer-text"
        ></textarea>
        <div class="row between" style="margin-top: 8px">
          <button
            v-if="detail.is_mine && !detail.answers.length"
            class="btn small ghost"
            data-testid="delete-question"
            @click="deleteQuestion"
          >
            Delete question
          </button>
          <span v-else class="muted" style="font-size: 10px">Visible to the whole team immediately.</span>
          <button class="btn small primary" :disabled="busy || !answerDraft.trim()" data-testid="post-answer" @click="postAnswer">
            Post answer
          </button>
        </div>
      </div>
    </div>
  </article>
</template>
