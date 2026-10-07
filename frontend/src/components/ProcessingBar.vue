<script setup lang="ts">
import { computed } from 'vue'
import { processing, type Outcome } from '../processing'

const props = defineProps<{ itemId: string }>()

const PHASES = {
  ingesting: 'Ingesting',
  categorizing: 'Categorizing',
  relating: 'Finding relationships',
  connecting: 'Connecting to team knowledge',
}
const VISIBLE = 2

const list = (names: string[]) =>
  names.length < 2 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`

function describe(outcome: Outcome) {
  const name = outcome.name ?? ''
  switch (outcome.kind) {
    case 'new_concept': return `New concept: ${name}`
    case 'confirmed_concept': return `Confirmed ${name} as a concept`
    case 'confirmed_connection': return `Confirmed connection: ${name}`
    case 'tagged': return `Tagged with ${list(outcome.names ?? [])}`
    case 'noted_concept': return `Noted ${name}; one more post naming it will make it a concept`
    case 'noted_connection': return `Noted ${name}; one more post saying so will confirm it`
    default: return ''
  }
}

const progress = computed(() => processing[props.itemId])
const lines = computed(() => {
  const p = progress.value
  if (p?.state !== 'done') return []
  const described = p.outcomes.map(describe).filter(Boolean)
  return described.length ? described : ['Added to team knowledge']
})
</script>

<template>
  <div v-if="progress && progress.state !== 'off'" class="processing" data-testid="processing">
    <div
      class="processing-track"
      role="progressbar"
      aria-label="Processing your contribution"
      :aria-valuenow="progress.percent"
      aria-valuemin="0"
      aria-valuemax="100"
    >
      <div class="processing-fill" :style="{ width: `${progress.percent}%`, '--done': `${progress.percent}%` }"></div>
    </div>
    <p class="processing-text" data-testid="processing-text">
      <template v-if="progress.state === 'working'">{{ PHASES[progress.phase] }}</template>
      <template v-else>
        {{ lines.slice(0, VISIBLE).join(' · ') }}
        <span v-if="lines.length > VISIBLE" class="processing-more" tabindex="0" data-testid="processing-more">
          +{{ lines.length - VISIBLE }} more
          <span class="processing-tip" role="tooltip">
            <span v-for="line in lines.slice(VISIBLE)" :key="line">{{ line }}</span>
          </span>
        </span>
      </template>
    </p>
  </div>
</template>

<style scoped>
.processing { float: right; width: 190px; margin: 0 0 6px 14px; }
.processing-track { height: 3px; border-radius: 3px; background: var(--border); overflow: hidden; }
/* Muted at the start, the success colour at the end, the mix of both in between. */
.processing-fill {
  height: 100%;
  border-radius: 3px;
  background: color-mix(in srgb, var(--success) var(--done), var(--muted-2));
  transition: width 0.6s ease, background-color 0.6s ease;
}
.processing-text { margin: 5px 0 0; text-align: center; color: var(--muted); font-style: italic; font-size: 10px; line-height: 1.35; }
.processing-more { position: relative; text-decoration: underline dotted; cursor: default; white-space: nowrap; }
.processing-tip {
  display: none;
  position: absolute;
  right: 0;
  top: calc(100% + 4px);
  z-index: 30;
  width: 220px;
  padding: 8px 10px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: var(--shadow);
  font-style: normal;
  text-align: left;
  white-space: normal;
  color: var(--ink);
}
.processing-tip span { display: block; }
.processing-tip span + span { margin-top: 4px; }
.processing-more:hover .processing-tip, .processing-more:focus .processing-tip { display: block; }
</style>
