<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { ApiError, api, type TopicFeedbackContext, type TopicFeedbackKind, type TopicFeedbackOption } from '../api'
import { knowledgeRevision, store } from '../store'

const props = defineProps<{
  itemId: string
  kind: TopicFeedbackKind
  /** Present only when this action must also accept the answer. */
  acceptQuestionId?: string
}>()
const emit = defineEmits<{ close: []; saved: [TopicFeedbackContext | null] }>()
const headingId = useId()
const hintId = useId()
const root = ref<HTMLElement | null>(null)
const context = ref<TopicFeedbackContext | null>(null)
const selected = ref<string[]>([])
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const searchQuery = ref('')
const loadedQuery = ref('')
const knownOptions = ref<TopicFeedbackOption[]>([])
let requestId = 0
let searchTimer: ReturnType<typeof setTimeout> | undefined
let refreshAfterSave = false

const entries = computed(() => context.value?.feedback[props.kind] ?? [])
const current = computed(() => entries.value.filter(e => e.state === 'current'))
const stale = computed(() => entries.value.filter(e => e.state === 'stale'))
const options = computed(() => {
  const shown = context.value?.topics.filter(t => loadedQuery.value || t.suggested_for.includes(props.kind)
    || entries.value.some(e => e.concept_id === t.concept_id && e.state !== 'revoked')) ?? []
  return [...shown, ...knownOptions.value.filter(t => selected.value.includes(t.concept_id)
    && !shown.some(option => option.concept_id === t.concept_id))]
})
const removable = computed(() => current.value.length > 0 || stale.value.length > 0)
const canConfirm = computed(() => !!context.value && (props.kind === 'accepted'
  ? context.value.can_confirm_accepted : context.value.can_confirm_helped))
const title = computed(() => props.kind === 'accepted'
  ? 'Which question topics did this answer resolve for you?'
  : 'Which topics did this help you with?')
const tooManyTopics = computed(() => selected.value.length > 20)

async function load(forceClear = false) {
  if (busy.value && !forceClear) return
  const request = ++requestId
  const query = searchQuery.value.trim()
  loading.value = true
  try {
    const value = await api.get<TopicFeedbackContext>(`/api/items/${props.itemId}/topic-feedback${query ? `?q=${encodeURIComponent(query)}` : ''}`)
    if (request !== requestId) return
    const changedIdentity = value.topics.some(topic => selected.value.includes(topic.concept_id)
      && knownOptions.value.some(old => old.concept_id === topic.concept_id && old.identity_revision !== topic.identity_revision))
    if (forceClear || changedIdentity || (context.value && context.value.context_token !== value.context_token)) {
      selected.value = []
      knownOptions.value = []
      notice.value = 'The contribution or its topic feedback changed. Review it and choose topics again.'
    }
    context.value = value
    loadedQuery.value = query
    knownOptions.value = [...value.topics, ...knownOptions.value.filter(t => !value.topics.some(current => current.concept_id === t.concept_id))]
    error.value = ''
  } catch (e) {
    if (request !== requestId) return
    // Keep a failed refresh from letting a stale draft submit as current.
    selected.value = []
    context.value = null
    error.value = e instanceof ApiError ? e.message : 'Could not load topic feedback. Try again.'
  } finally {
    if (request === requestId) loading.value = false
  }
}

async function save(mode: 'confirm' | 'remove' | 'broad') {
  if (busy.value || (mode !== 'broad' && (loading.value || !context.value))) return
  if (mode === 'confirm' && (!canConfirm.value || !selected.value.length || tooManyTopics.value)) return
  busy.value = true
  clearTimeout(searchTimer)
  requestId++
  loading.value = false
  error.value = ''
  try {
    const topics = mode === 'confirm' ? knownOptions.value.filter(t => selected.value.includes(t.concept_id))
      .map(({ concept_id, identity_revision }) => ({ concept_id, identity_revision })) : []
    if (props.acceptQuestionId) {
      await api.post(`/api/questions/${props.acceptQuestionId}/accept`, {
        answer_id: props.itemId,
        ...(mode === 'confirm' ? { topic_feedback: { expected_context: context.value!.context_token, topics } } : {}),
      })
      emit('saved', null)
    } else {
      const saved = await api.put<TopicFeedbackContext>(`/api/items/${props.itemId}/topic-feedback`, {
        kind: props.kind, expected_context: context.value!.context_token, topics,
      })
      context.value = saved
      selected.value = []
      knownOptions.value = saved.topics
      searchQuery.value = ''
      loadedQuery.value = ''
      notice.value = mode === 'remove' ? 'Topic credit removed. Your original feedback is unchanged.' : 'Topic feedback saved.'
      emit('saved', saved)
    }
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      await load(true)
    } else {
      error.value = e instanceof ApiError ? e.message : 'Could not save topic feedback. Try again.'
      if (props.kind === 'helped') error.value += ' Your helpful mark is still saved.'
    }
  } finally {
    busy.value = false
    if (refreshAfterSave) {
      refreshAfterSave = false
      await load()
    }
  }
}

onMounted(async () => {
  await load()
  await nextTick()
  root.value?.focus()
})
onBeforeUnmount(() => { requestId++; clearTimeout(searchTimer) })
watch(knowledgeRevision, () => {
  if (busy.value) refreshAfterSave = true
  else load()
})
watch(searchQuery, () => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => load(), 250)
})
watch(() => [props.itemId, props.kind], () => { context.value = null; selected.value = []; knownOptions.value = []; load() })
watch(() => [store.auth.signed_in, store.auth.username, store.profile?.id], () => {
  context.value = null
  selected.value = []
  knownOptions.value = []
  notice.value = 'Your account changed. Review the contribution before confirming topics.'
  if (busy.value) refreshAfterSave = true
  else load()
})
</script>

<template>
  <section ref="root" class="topic-credit" tabindex="-1" :aria-labelledby="headingId" data-testid="topic-credit-panel" @click.stop>
    <h3 :id="headingId">{{ title }}</h3>
    <p :id="hintId" class="topic-hint">
      {{ kind === 'accepted' ? 'Choose only the topics it resolved.' : 'Choose the topics you used or learned about, not topics merely mentioned.' }}
      Your feedback helps us suggest people for future questions.
    </p>
    <p v-if="loading" role="status">Loading current feedback…</p>
    <template v-if="context">
      <div class="topic-context">
        <template v-if="context.question">
          <strong>Question · {{ context.question.author }}</strong>
          <p>{{ context.question.body }}</p>
        </template>
        <strong>{{ context.question ? 'Answer' : 'Contribution' }} · {{ context.item.author }}</strong>
        <p>{{ context.item.body }}</p>
      </div>
      <p v-if="current.length" data-testid="current-topic-feedback">
        Your confirmed topics: <strong>{{ current.map(t => t.name).join(', ') }}</strong>.
        Choose topics below to replace this selection.
      </p>
      <p v-else-if="!stale.length" class="topic-hint">No topic confirmed. Broad feedback still counts toward contributor impact.</p>
      <p v-if="stale.length" data-testid="stale-topic-feedback">
        Your feedback for {{ stale.map(t => t.name).join(', ') }} needs confirmation because the contribution or topic changed.
      </p>
      <p v-if="!context.signed_in">Sign in to confirm topics. You can still leave broad feedback.</p>
      <p v-else-if="!canConfirm">Topic credit is not available for this contribution and account.</p>
      <details v-if="canConfirm" class="topic-search">
        <summary>Find another topic</summary>
        <label :for="`${headingId}-search`">Search existing topics</label>
        <input :id="`${headingId}-search`" v-model="searchQuery" type="search" :disabled="busy" placeholder="Topic name" />
        <p class="topic-hint">Choose a topic only if this contribution helped you with it.</p>
      </details>
      <fieldset v-if="canConfirm" :disabled="busy || loading" :aria-describedby="hintId">
        <legend>{{ current.length ? 'Select the topics to keep or replace' : 'Select topics (optional)' }}</legend>
        <label v-for="topic in options" :key="topic.concept_id" class="topic-option">
          <input v-model="selected" type="checkbox" :value="topic.concept_id" />
          <span>{{ topic.name }}</span>
        </label>
        <p v-if="!options.length">{{ loadedQuery ? 'No matching topics found.' : 'No topic choices are available yet.' }}</p>
      </fieldset>
      <p v-if="tooManyTopics" role="alert">Choose no more than 20 topics.</p>
      <p class="topic-hint">If none of these topics fit, leave them unchecked.</p>
    </template>
    <p v-if="notice" role="status" data-testid="topic-feedback-status">{{ notice }}</p>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <div class="result-actions">
      <button v-if="canConfirm" class="btn small primary" :disabled="busy || loading || !selected.length || tooManyTopics" data-testid="confirm-topic-credit" @click="save('confirm')">
        {{ acceptQuestionId ? 'Accept and confirm topics' : 'Save topic feedback' }}
      </button>
      <button v-if="acceptQuestionId" class="btn small" :disabled="busy" data-testid="accept-without-topics" @click="save('broad')">Accept without topic credit</button>
      <button v-else-if="removable" class="btn small" :disabled="busy || loading" data-testid="remove-topic-credit" @click="save('remove')">Remove topic credit</button>
      <button v-if="!context && !loading" class="btn small" :disabled="busy" @click="load()">Retry loading topics</button>
      <button class="btn small ghost" :disabled="busy" data-testid="close-topic-credit" @click="emit('close')">{{ acceptQuestionId ? 'Cancel' : 'Close' }}</button>
    </div>
  </section>
</template>

<style scoped>
.topic-credit { margin-top: 14px; padding: 16px; border: 1px solid var(--border-strong); border-radius: 10px; background: #f8faff; }
.topic-credit h3 { line-height: 1.45; }
.topic-credit p { font-size: 12px; line-height: 1.5; margin: 10px 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.topic-hint { color: var(--muted); }
.topic-context { margin: 12px 0; padding: 12px; border: 1px solid var(--border); border-radius: 8px; background: white; }
.topic-context strong { font-size: 11px; }
fieldset { margin: 12px 0; border: 0; padding: 0; }
legend { font-weight: 650; font-size: 12px; margin-bottom: 8px; }
.topic-option { display: flex; align-items: flex-start; gap: 8px; padding: 8px 0; cursor: pointer; font-size: 13px; overflow-wrap: anywhere; }
.topic-option input { flex: 0 0 auto; margin: 2px 0 0; }
.topic-search { margin-top: 12px; font-size: 12px; }
.topic-search summary { cursor: pointer; }
.topic-search label { display: block; margin: 12px 0 6px; }
.topic-search input { width: 100%; }
</style>
