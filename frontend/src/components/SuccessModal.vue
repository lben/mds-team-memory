<script setup lang="ts">
import type { Corroboration } from '../api'

defineProps<{ corroboration: Corroboration | null; sharedTotal?: number | null }>()

function ordinal(n: number): string {
  const rest = n % 100
  if (rest >= 11 && rest <= 13) return `${n}th`
  return `${n}${['th', 'st', 'nd', 'rd'][n % 10] ?? 'th'}`
}
const emit = defineEmits<{ close: []; view: []; another: [] }>()
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <div class="modal" data-testid="success-modal">
      <div class="success-icon">✓</div>
      <h2>Thank you. Your knowledge is now helping the team.</h2>
      <p>Your contribution was saved immediately. No approval is required.</p>
      <p v-if="sharedTotal" class="shared-note" data-testid="shared-total">
        That is the <strong>{{ ordinal(sharedTotal) }}</strong> piece of knowledge you have shared with the team.
      </p>
      <div v-if="corroboration && corroboration.group_size > 1" class="corroboration">
        <strong>Your contribution supports {{ corroboration.group_size - 1 }} existing {{ corroboration.group_size - 1 === 1 ? 'entry' : 'entries' }}.</strong><br />
        All {{ corroboration.contributors }} contributor{{ corroboration.contributors === 1 ? '' : 's' }} will receive impact when this knowledge helps someone.
      </div>
      <div class="modal-actions">
        <button class="btn" @click="emit('view')">View contribution</button>
        <button class="btn primary" @click="emit('another')">Add another</button>
      </div>
    </div>
  </div>
</template>
