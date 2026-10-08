<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ApiError, api } from '../api'
import { store } from '../store'

interface Source {
  kind: string
  id: string
  label: string
}
interface Progress {
  step: string
  window?: number
  windows?: number
}
interface Job extends Source {
  state: 'processing' | 'waiting' | 'retrying'
  origin: string
  attempts: number
  created_at: number
  available_at: number
  error: string | null
}
interface Decision {
  kind: string
  name: string
  state: string
  headline: string
  why: string
}
interface Finished extends Source {
  outcome: 'done' | 'failed' | 'interrupted'
  error: string | null
  finished_at: number
  seconds: number
  decisions: Decision[]
}
interface QueueReport {
  now: number
  worker: { running: boolean; pid: number | null; started_at: number | null }
  activity: { live: boolean; name?: string; source?: Source | null; since?: number; written_at: number | null } | null
  current:
    | (Source & { stage: string; stage_since: number; claimed_at: number; progress: Progress | null; attempt: number | null })
    | null
  totals: {
    total: number
    processing: number
    waiting: number
    retrying: number
    reprocessing: number
    by_kind: Record<string, number>
  }
  jobs: Job[]
  offset: number
  limit: number
  recent: Finished[]
}

const PAGE = 50
const REFRESH_MS = 2000
const report = ref<QueueReport | null>(null)
const offset = ref(0)
const loaded = ref(false)
const error = ref('')
let timer: number | undefined
let active = true

async function refresh() {
  if (!store.auth.is_admin) return
  try {
    report.value = await api.get<QueueReport>(`/api/ml/queue?offset=${offset.value}&limit=${PAGE}`)
    error.value = ''
    // Finished jobs can empty the page being viewed; return to the start of the queue.
    if (!report.value.jobs.length && offset.value > 0) {
      offset.value = 0
      await refresh()
    }
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : 'Could not load the ML queue'
    if (e instanceof ApiError && e.status === 401) await store.refreshIdentity() // the sign-in ran out
  }
}

function schedule() {
  timer = window.setTimeout(async () => {
    if (!document.hidden) await refresh()
    if (active) schedule()
  }, REFRESH_MS)
}

async function page(delta: number) {
  offset.value = Math.max(0, offset.value + delta * PAGE)
  await refresh()
}

function duration(seconds: number) {
  const s = Math.max(0, Math.floor(seconds))
  if (s < 120) return `${s}s`
  if (s < 7200) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`
}
function since(timestamp: number) {
  return duration((report.value?.now ?? timestamp) - timestamp)
}
function clock(timestamp: number) {
  return new Date(timestamp * 1000).toLocaleString()
}
function step(progress: Progress | null) {
  if (!progress) return ''
  return progress.windows ? `window ${progress.window} of ${progress.windows}: ${progress.step}` : progress.step
}
function retryText(job: Job) {
  if (!report.value || job.state === 'processing') return 'previous attempt failed'
  return job.available_at <= report.value.now ? 'retry due now' : `next try in ${duration(job.available_at - report.value.now)}`
}

const workerText = computed(() => {
  const r = report.value
  if (!r) return ''
  if (r.worker.running && r.activity?.live) return `Running since ${clock(r.worker.started_at!)}`
  if (r.worker.running) return 'Running; it has not reported its activity yet'
  return 'Not running; queued work is retained'
})
const lastPage = computed(() => !report.value || report.value.offset + report.value.jobs.length >= report.value.totals.total)

onMounted(async () => {
  await store.refreshIdentity() // sign-in and profile together, so the sidebar agrees
  loaded.value = true
  await refresh()
  schedule()
})
onUnmounted(() => {
  active = false
  window.clearTimeout(timer)
})
</script>

<template>
  <section class="page">
    <div class="page-head">
      <div>
        <div class="eyebrow">Admin</div>
        <h1>ML queue</h1>
        <p class="lead">
          What the background ML worker is doing now, every job waiting for it, and what it finished recently.
          Updates every {{ REFRESH_MS / 1000 }} seconds.
        </p>
      </div>
    </div>

    <div v-if="loaded && !store.auth.is_admin" class="card card-pad" data-testid="ml-queue-auth">
      <h2>Administrator sign-in required</h2>
      <p class="muted">Sign in with an administrator account from the profile menu at the bottom left to see the ML queue.</p>
    </div>

    <template v-else-if="report">
      <div class="card card-pad">
        <div class="row between wrap gap8">
          <h3>Worker</h3>
          <span class="chip" :class="report.worker.running ? 'good' : 'warn'" data-testid="ml-worker-state">{{ workerText }}</span>
        </div>
        <p v-if="report.activity?.name" class="queue-now" data-testid="ml-activity">
          <template v-if="report.activity.live">
            <strong>Now:</strong> {{ report.activity.name }}<template v-if="report.activity.source">
              — {{ report.activity.source.label }}</template> (for {{ since(report.activity.since!) }})
          </template>
          <template v-else>
            <strong>Last reported {{ clock(report.activity.written_at!) }}:</strong> {{ report.activity.name }}
          </template>
        </p>
        <div v-if="report.current" class="queue-current" data-testid="ml-current-job">
          <div class="meta">
            <span class="chip team">{{ report.current.kind }}</span>
            <span v-if="report.current.attempt">attempt {{ report.current.attempt }}</span>
            <span>{{ since(report.current.claimed_at) }} on this job</span>
          </div>
          <p class="queue-label">{{ report.current.label }}</p>
          <p data-testid="ml-current-stage">
            <strong>{{ report.current.stage }}</strong><template v-if="report.current.progress">
              — {{ step(report.current.progress) }}</template>
            <span class="muted"> ({{ since(report.current.stage_since) }} in this stage)</span>
          </p>
        </div>
      </div>

      <div class="card card-pad queue-gap" data-testid="ml-totals">
        <h3>Queue: {{ report.totals.total }} job{{ report.totals.total === 1 ? '' : 's' }}</h3>
        <div class="area-chips queue-chips">
          <span class="chip team">{{ report.totals.processing }} processing</span>
          <span class="chip">{{ report.totals.waiting }} waiting</span>
          <span class="chip warn">{{ report.totals.retrying }} failed, retrying</span>
          <span v-for="(count, kind) in report.totals.by_kind" :key="kind" class="chip">{{ kind }}: {{ count }}</span>
          <span class="chip">{{ report.totals.reprocessing }} reprocessing older content</span>
        </div>
      </div>

      <div class="card queue-gap">
        <div class="queue-table-head">
          <h3>Jobs in processing order</h3>
          <div v-if="report.totals.total > PAGE" class="row gap8" data-testid="ml-pager">
            <span class="muted">{{ report.offset + 1 }}–{{ report.offset + report.jobs.length }} of {{ report.totals.total }}</span>
            <button class="btn small" :disabled="report.offset === 0" @click="page(-1)">Previous</button>
            <button class="btn small" :disabled="lastPage" @click="page(1)">Next</button>
          </div>
        </div>
        <p v-if="!report.jobs.length" class="muted queue-empty">The queue is empty.</p>
        <table v-else class="queue-table">
          <thead>
            <tr><th>#</th><th>State</th><th>Source</th><th>Why queued</th><th>Tries</th><th>Queued for</th></tr>
          </thead>
          <tbody>
            <tr v-for="(job, index) in report.jobs" :key="job.kind + job.id" data-testid="ml-job-row" :class="job.state">
              <td>{{ report.offset + index + 1 }}</td>
              <td><span class="chip" :class="{ team: job.state === 'processing', warn: job.state === 'retrying' }">{{ job.state }}</span></td>
              <td>
                <span class="muted">{{ job.kind }}</span> {{ job.label }}
                <div v-if="job.error" class="queue-error">{{ retryText(job) }}; last error: {{ job.error }}</div>
              </td>
              <td class="muted">{{ job.origin }}</td>
              <td>{{ job.attempts }}</td>
              <td>{{ since(job.created_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="card queue-gap">
        <div class="queue-table-head"><h3>Recently finished</h3></div>
        <p v-if="!report.recent.length" class="muted queue-empty">Nothing has finished since the worker started.</p>
        <table v-else class="queue-table">
          <thead>
            <tr><th>Finished</th><th>Outcome</th><th>Source</th><th>Model decisions</th><th>Took</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in report.recent" :key="item.kind + item.id + item.finished_at" data-testid="ml-recent-row">
              <td>{{ clock(item.finished_at) }}</td>
              <td><span class="chip" :class="item.outcome === 'done' ? 'good' : 'warn'">{{ item.outcome }}</span></td>
              <td>
                <span class="muted">{{ item.kind }}</span> {{ item.label }}
                <div v-if="item.error" class="queue-error">{{ item.error }}</div>
              </td>
              <td data-testid="ml-decisions">
                <div v-for="decision in item.decisions" :key="decision.kind + decision.name" class="queue-decision">
                  <span class="chip" :class="{ good: decision.state === 'active', warn: ['held', 'weak'].includes(decision.state) }">{{ decision.headline }}</span> {{ decision.name }}
                  <div class="muted">{{ decision.why }}</div>
                </div>
                <span v-if="!item.decisions.length && item.kind !== 'vocabulary'" class="muted">No findings from this source</span>
              </td>
              <td>{{ item.seconds.toFixed(1) }}s</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <p v-if="error" class="form-error">{{ error }}</p>
  </section>
</template>

<style scoped>
.queue-gap { margin-top: 16px; }
.queue-now { margin: 12px 0 0; font-size: 13px; }
.queue-current { margin-top: 12px; padding: 12px 14px; border-radius: 10px; background: var(--info-soft); }
.queue-current p { margin: 6px 0 0; font-size: 13px; }
.queue-label { overflow-wrap: anywhere; }
.queue-chips { margin-top: 10px; }
.queue-table-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 14px 16px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.queue-empty { padding: 14px 16px; margin: 0; font-size: 12px; }
.queue-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.queue-table th { text-align: left; padding: 9px 12px; font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; border-bottom: 1px solid var(--border); }
.queue-table td { padding: 9px 12px; border-bottom: 1px solid var(--border); vertical-align: top; overflow-wrap: anywhere; }
.queue-table tr:last-child td { border-bottom: 0; }
.queue-table tr.processing td { background: var(--info-soft); }
.queue-decision { margin-bottom: 6px; }
.queue-decision:last-child { margin-bottom: 0; }
.queue-error { margin-top: 4px; color: var(--accent-2); font-size: 11px; }
</style>
