<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { initialsFor } from '../profile'

interface LeaderEntry {
  profile_id: string
  label: string
  verified: boolean
  is_me: boolean
  shared: number
  helped: number
  accepted: number
  corrections: number
  endorsements: number
  score: number
  rank: number
}
interface ImpactData {
  period: string
  me: {
    shared: number
    helped: number
    accepted: number
    corrections: number
    endorsements: number
    score: number
    rank: number | null
  }
  leaderboard: LeaderEntry[]
}

const period = ref<'30d' | 'all'>('30d')
const data = ref<ImpactData | null>(null)

async function load() {
  data.value = await api.get<ImpactData>(`/api/impact?period=${period.value}`)
}

function setPeriod(p: '30d' | 'all') {
  period.value = p
  load()
}


onMounted(load)
</script>

<template>
  <section class="page">
    <div class="page-head">
      <div>
        <div class="eyebrow">Recognition</div>
        <h1>Leaderboard</h1>
        <p class="lead">Recognition is based on useful outcomes, not posting volume.</p>
      </div>
      <div class="row gap8">
        <button class="btn" :class="{ primary: period === '30d' }" @click="setPeriod('30d')">Last 30 days</button>
        <button class="btn" :class="{ primary: period === 'all' }" @click="setPeriod('all')">All time</button>
      </div>
    </div>

    <div v-if="data" class="impact-hero">
      <div class="card impact-metric" data-testid="my-shared">
        <strong>{{ data.me.shared }}</strong><span>Knowledge you shared</span>
      </div>
      <div class="card impact-metric"><strong>{{ data.me.helped }}</strong><span>Your Helpful marks</span></div>
      <div class="card impact-metric"><strong>{{ data.me.accepted }}</strong><span>Your accepted answers</span></div>
      <div class="card impact-metric">
        <strong>{{ data.me.rank ? `#${data.me.rank}` : '—' }}</strong>
        <span>{{ data.me.rank ? 'Your current rank' : 'Ranked once your knowledge helps someone' }}</span>
      </div>
    </div>

    <div v-if="data" class="card leaderboard" data-testid="leaderboard">
      <div class="leader-head">
        <div>Rank</div>
        <div>Contributor</div>
        <div>Shared</div>
        <div>Helpful</div>
        <div>Accepted</div>
        <div>Corrections</div>
        <div>Impact</div>
      </div>
      <p v-if="!data.leaderboard.length" class="muted" style="padding: 16px; font-size: 12px">
        Nothing shared in this period yet.
      </p>
      <div v-for="entry in data.leaderboard" :key="entry.profile_id" class="leader-row" :class="{ me: entry.is_me }">
        <div class="rank">{{ entry.score > 0 ? entry.rank : '—' }}</div>
        <div class="person">
          <span class="avatar">{{ initialsFor(entry.label) }}</span>
          <span>
            <strong>{{ entry.label }}</strong>
            <span>{{ entry.is_me ? 'You · unverified' : 'Unverified' }}</span>
          </span>
        </div>
        <div>{{ entry.shared }}</div>
        <div>{{ entry.helped }}</div>
        <div>{{ entry.accepted }}</div>
        <div>{{ entry.corrections }}</div>
        <div class="score">{{ entry.score }}</div>
      </div>
    </div>
    <p class="muted" style="font-size: 10px; margin-top: 9px">
      Pilot leaderboard — identities are not yet verified. Sharing an item alone earns 0 points.
    </p>
  </section>
</template>
