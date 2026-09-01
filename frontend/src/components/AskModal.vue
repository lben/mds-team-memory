<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'

/** The app's own confirm/prompt.
 *
 * These four admin actions used to raise `window.confirm` / `window.prompt`.
 * Chrome offers "Prevent this page from creating additional dialogs" once a
 * page has raised a few — which a curating admin triggers quickly — and after
 * that every Approve or Delete silently did nothing at all.
 *
 * The contract is the native one it replaces: cancelling resolves to `null`,
 * confirming resolves to the text (which may be an empty string when the note
 * is optional). Null and empty are different answers.
 */
const props = defineProps<{
  title: string
  message?: string
  inputLabel?: string
  confirmLabel?: string
  danger?: boolean
}>()
const emit = defineEmits<{ resolve: [string | null] }>()

const text = ref('')
const field = ref<HTMLInputElement | null>(null)

onMounted(async () => {
  await nextTick()
  field.value?.focus()
})
</script>

<template>
  <div class="modal-backdrop" data-testid="ask-modal" @click.self="emit('resolve', null)">
    <div class="modal" @keyup.esc="emit('resolve', null)">
      <h2>{{ props.title }}</h2>
      <p v-if="props.message">{{ props.message }}</p>
      <template v-if="props.inputLabel">
        <label>{{ props.inputLabel }}</label>
        <input
          ref="field"
          v-model="text"
          type="text"
          maxlength="300"
          data-testid="ask-input"
          @keyup.enter="emit('resolve', text)"
        />
      </template>
      <div class="modal-actions">
        <button class="btn" data-testid="ask-cancel" @click="emit('resolve', null)">Cancel</button>
        <button
          class="btn"
          :class="props.danger ? 'danger' : 'primary'"
          data-testid="ask-confirm"
          @click="emit('resolve', text)"
        >
          {{ props.confirmLabel || 'Confirm' }}
        </button>
      </div>
    </div>
  </div>
</template>
