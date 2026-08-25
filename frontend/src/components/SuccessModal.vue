<script setup lang="ts">
import type { Corroboration } from '../api'

defineProps<{ corroboration: Corroboration | null }>()
const emit = defineEmits<{ close: []; view: []; another: [] }>()
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <div class="modal" data-testid="success-modal">
      <div class="success-icon">✓</div>
      <h2>Thank you. Your knowledge is now helping the team.</h2>
      <p>Your contribution was saved immediately. No approval is required.</p>
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
