import { reactive } from 'vue'
import { api } from './api'

/** What the server reports for one of your own new contributions. */
export type Outcome = { kind: string; name?: string; names?: string[] }
export type Progress =
  | { state: 'off' }
  | { state: 'working'; phase: 'ingesting' | 'categorizing' | 'relating' | 'connecting'; percent: number }
  | { state: 'done'; percent: 100; outcomes: Outcome[] }

/** Progress of the contributions submitted from this page, by item id. */
export const processing = reactive<Record<string, Progress>>({})

const POLL_MS = 1500
const followed = new Set<string>()
let timer = 0
let polling = false

/** Follow a contribution you just submitted until the server has finished with it.
 * Nothing is shown until the server says what is happening. */
export function track(id: string) {
  followed.add(id)
  if (polling) return // the poll in flight schedules the next one, which includes it
  window.clearTimeout(timer)
  void poll()
}

async function poll() {
  polling = true
  const pending = [...followed]
  try {
    const query = pending.map((id) => `ids=${encodeURIComponent(id)}`).join('&')
    const latest = await api.get<Record<string, Progress>>(`/api/ml/contributions?${query}`)
    for (const id of pending) {
      const next = latest[id]
      const previous = processing[id]
      if (next?.state !== 'working') followed.delete(id) // finished, switched off, deleted or not yours
      if (!next) delete processing[id]
      // The bar only moves forward: a requeued job restarts on the server, not on screen.
      else if (next.state === 'working' && previous?.state === 'working') {
        processing[id] = { ...next, percent: Math.max(previous.percent, next.percent) }
      } else processing[id] = next
    }
  } catch {
    /* Retry after a transient failure. */
  }
  polling = false
  timer = followed.size ? window.setTimeout(poll, POLL_MS) : 0
}
