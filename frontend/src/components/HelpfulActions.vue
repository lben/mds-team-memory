<script setup lang="ts">
import { nextTick, ref } from 'vue'
import type { Item } from '../api'
import { store } from '../store'
import TopicCreditPanel from './TopicCreditPanel.vue'

const props = defineProps<{ item: Item }>()
const emit = defineEmits<{ changed: [] }>()
const busy = ref(false)
const showTopics = ref(false)
const topicButton = ref<HTMLButtonElement | null>(null)

async function markHelped() {
  if (busy.value) return
  busy.value = true
  try {
    if (await store.markHelped(props.item)) emit('changed')
  } finally {
    busy.value = false
  }
}

async function closeTopics() {
  showTopics.value = false
  await nextTick()
  topicButton.value?.focus()
}
</script>

<template>
  <div class="helpful-actions">
    <div class="row gap8 wrap">
      <button class="btn small" :class="{ success: item.marked_helped }" :disabled="item.is_mine || busy" @click="markHelped">
        {{ item.marked_helped ? '✓ Marked helpful' : '✓ Helped me' }}
      </button>
      <button
        v-if="item.marked_helped && !item.is_mine && ['note', 'answer'].includes(item.kind)"
        ref="topicButton"
        class="btn small ghost"
        data-testid="helpful-topic-feedback"
        :aria-expanded="showTopics"
        @click="showTopics ? closeTopics() : showTopics = true"
      >Topic feedback (optional)</button>
    </div>
    <TopicCreditPanel v-if="showTopics" :item-id="item.id" kind="helped" @close="closeTopics" @saved="emit('changed')" />
  </div>
</template>

<style scoped>
.helpful-actions { min-width: 0; }
.helpful-actions:has(.topic-credit) { flex-basis: 100%; }
</style>
