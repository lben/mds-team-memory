<script setup lang="ts">
import cytoscape, { type Core } from 'cytoscape'
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import EvidenceModal from '../components/EvidenceModal.vue'
import ItemDetailModal from '../components/ItemDetailModal.vue'
import MapAdminPanel from '../components/MapAdminPanel.vue'

interface ConceptRow {
  id: string
  name: string
  mentions: number
}
interface GraphNode {
  id: string
  type: string
  label: string
  sublabel?: string
  center?: boolean
}
interface GraphEdge {
  source: string
  target: string
  label: string
  style: string
  evidence: string
  link_id: string | null
}

const router = useRouter()
const concepts = ref<ConceptRow[]>([])
const selectedConcept = ref('')
const mode = ref<'local' | 'global'>('local')
const edges = ref<GraphEdge[]>([])
const centerName = ref('')
const detailId = ref<string | null>(null)
const evidenceLinkId = ref<string | null>(null)
const isAdmin = ref(false)
const selectedLinkId = ref<string | null>(null)
const selectedConceptRow = ref<string | null>(null)
const graphEl = ref<HTMLDivElement | null>(null)
let cy: Core | null = null

const NODE_COLORS: Record<string, string> = {
  concept: '#27a3d0',
  item: '#47a979',
  question: '#e4a01d',
  document: '#b17acb',
}

function initCy() {
  if (!graphEl.value) return
  cy = cytoscape({
    container: graphEl.value,
    userZoomingEnabled: true,
    style: [
      {
        selector: 'node',
        style: {
          label: 'data(label)',
          color: '#fff',
          'text-wrap': 'wrap',
          'text-max-width': '120',
          'font-size': '11px',
          'text-valign': 'center',
          'background-color': 'data(color)',
          width: 'data(size)',
          height: 'data(size)',
          'text-outline-color': '#0f213b',
          'text-outline-width': 2,
        },
      },
      {
        selector: 'node[kind="cluster"]',
        style: {
          shape: 'round-rectangle',
          'background-opacity': 0.15,
          'border-color': '#527094',
          'border-width': 1,
          'text-valign': 'top',
          'text-margin-y': -6,
          'font-weight': 'bold',
        },
      },
      {
        selector: 'edge',
        style: {
          label: 'data(label)',
          'font-size': '9px',
          color: '#a8bbd2',
          'text-outline-color': '#0f213b',
          'text-outline-width': 2,
          'line-color': '#527094',
          width: 1.5,
          'curve-style': 'bezier',
        },
      },
      { selector: 'edge[lineStyle="dashed"]', style: { 'line-style': 'dashed' } },
      { selector: 'edge:selected', style: { 'line-color': '#d83a52', width: 3, color: '#fff' } },
      { selector: 'node:selected', style: { 'border-color': '#d83a52', 'border-width': 4 } },
    ],
  })

  cy.on('tap', 'edge', (evt) => {
    const linkId = evt.target.data('linkId')
    // Only concept-to-concept links are editable; structural edges have no record.
    if (linkId) selectedLinkId.value = linkId
  })

  cy.on('tap', 'node', (evt) => {
    const id: string = evt.target.id()
    if (id.startsWith('c:')) {
      const conceptId = id.slice(2)
      selectedConceptRow.value = conceptId
      if (conceptId !== selectedConcept.value) {
        if (mode.value === 'global') mode.value = 'local'
        selectedConcept.value = conceptId
        loadLocal(conceptId)
      }
    } else if (id.startsWith('i:')) {
      if (evt.target.data('nodeType') === 'question') router.push(`/questions/${id.slice(2)}`)
      else detailId.value = id.slice(2)
    } else if (id.startsWith('d:')) {
      router.push(`/documents/${id.slice(2)}`)
    }
  })
}

async function loadLocal(conceptId: string) {
  if (!conceptId || !cy) return
  const graph = await api.get<{ nodes: GraphNode[]; edges: GraphEdge[] }>(
    `/api/graph/local?concept_id=${conceptId}`,
  )
  edges.value = graph.edges
  centerName.value = graph.nodes[0]?.label ?? ''
  cy.elements().remove()
  const neighbors = graph.nodes.filter((n) => !n.center)
  cy.add(
    graph.nodes.map((n) => {
      const angle = neighbors.length ? (2 * Math.PI * neighbors.indexOf(n)) / neighbors.length : 0
      return {
        data: {
          id: n.id,
          label: n.label + (n.sublabel ? `\n${n.sublabel}` : ''),
          color: n.center ? '#d83a52' : NODE_COLORS[n.type] || '#596f91',
          size: n.center ? 90 : n.type === 'concept' ? 70 : 56,
          nodeType: n.type,
        },
        // Deterministic layout: centre fixed, neighbours on a circle in server order.
        position: n.center
          ? { x: 0, y: 0 }
          : { x: Math.cos(angle - Math.PI / 2) * 240, y: Math.sin(angle - Math.PI / 2) * 240 },
      }
    }),
  )
  cy.add(
    graph.edges.map((e) => ({
      data: {
        id: `${e.source}->${e.target}`,
        source: e.source,
        target: e.target,
        label: e.label,
        lineStyle: e.style,
        linkId: e.link_id,
      },
    })),
  )
  cy.fit(undefined, 40)
}

async function loadGlobal() {
  if (!cy) return
  const graph = await api.get<{
    clusters: { id: string; label: string; concepts: { id: string; name: string; size: number }[] }[]
    edges: { source: string; target: string; count: number }[]
  }>('/api/graph/global')
  edges.value = graph.edges.map((e) => ({
    source: `c:${e.source}`,
    target: `c:${e.target}`,
    label: 'related to',
    style: 'dashed',
    evidence: `Mentioned together in ${e.count} team entries.`,
    link_id: null,
  }))
  centerName.value = 'Aggregated clusters'
  cy.elements().remove()
  const clusterCount = graph.clusters.length || 1
  graph.clusters.forEach((cluster, ci) => {
    const angle = (2 * Math.PI * ci) / clusterCount
    const cxPos = clusterCount === 1 ? 0 : Math.cos(angle) * 320
    const cyPos = clusterCount === 1 ? 0 : Math.sin(angle) * 240
    cy!.add({ data: { id: `cl:${cluster.id}`, label: cluster.label, color: '#173a5f', size: 40, kind: 'cluster' } })
    cluster.concepts.forEach((concept, i) => {
      const inner = (2 * Math.PI * i) / cluster.concepts.length
      const spread = Math.min(2, cluster.concepts.length / 3 + 1)
      cy!.add({
        data: {
          id: `c:${concept.id}`,
          parent: `cl:${cluster.id}`,
          label: `${concept.name}\n${concept.size}`,
          color: NODE_COLORS.concept,
          size: Math.min(80, 40 + concept.size * 4),
          nodeType: 'concept',
        },
        position: {
          x: cxPos + Math.cos(inner) * 70 * spread,
          y: cyPos + Math.sin(inner) * 60 * spread,
        },
      })
    })
  })
  cy.add(
    graph.edges.map((e) => ({
      data: {
        id: `g:${e.source}->${e.target}`,
        source: `c:${e.source}`,
        target: `c:${e.target}`,
        label: '',
        lineStyle: 'dashed',
      },
    })),
  )
  cy.fit(undefined, 40)
}

function setMode(m: 'local' | 'global') {
  mode.value = m
  if (m === 'global') loadGlobal()
  else if (selectedConcept.value) loadLocal(selectedConcept.value)
}

function onConceptChange() {
  mode.value = 'local'
  selectedConceptRow.value = selectedConcept.value
  loadLocal(selectedConcept.value)
}

async function refresh() {
  concepts.value = await api.get<ConceptRow[]>('/api/graph/concepts')
  if (!concepts.value.some((c) => c.id === selectedConcept.value)) {
    selectedConcept.value = [...concepts.value].sort((a, b) => b.mentions - a.mentions)[0]?.id ?? ''
  }
  await nextTick()
  if (!cy && concepts.value.length) initCy()
  if (mode.value === 'global') await loadGlobal()
  else if (selectedConcept.value) await loadLocal(selectedConcept.value)
}

onMounted(async () => {
  isAdmin.value = (await api.get<{ logged_in: boolean }>('/api/admin/state')).logged_in
  await refresh()
})

onBeforeUnmount(() => cy?.destroy())
</script>

<template>
  <section class="page">
    <div class="page-head">
      <div>
        <div class="eyebrow">Connected context</div>
        <h1>Context Map</h1>
        <p class="lead">Start with one concept, then expand only the relationships that matter.</p>
      </div>
    </div>

    <div v-if="!concepts.length" class="card card-pad">
      <h3>No concepts defined yet</h3>
      <p class="muted" style="font-size: 13px; margin-top: 8px">
        Concepts are the backbone of the map. An admin creates them below; contributions mentioning them are then
        connected automatically.
      </p>
    </div>

    <div v-else class="map-layout">
      <div class="map-card">
        <div class="map-toolbar">
          <strong>{{ mode === 'local' ? `Local context: ${centerName}` : 'Global map: aggregated clusters' }}</strong>
          <div class="row gap8">
            <select v-if="mode === 'local'" v-model="selectedConcept" data-testid="concept-picker" @change="onConceptChange">
              <option v-for="c in concepts" :key="c.id" :value="c.id">{{ c.name }} ({{ c.mentions }})</option>
            </select>
            <div class="toggle">
              <button :class="{ active: mode === 'local' }" @click="setMode('local')">Local</button>
              <button :class="{ active: mode === 'global' }" @click="setMode('global')">Global</button>
            </div>
          </div>
        </div>
        <div ref="graphEl" class="graph-box" data-testid="graph"></div>
      </div>

      <aside class="map-side">
        <div class="card context-card">
          <h3>{{ mode === 'local' ? centerName : 'Clusters' }}</h3>
          <p v-if="mode === 'local'">
            Click a concept to recentre, or an item, question or document to open it. Dashed edges are automatically
            detected; solid edges are confirmed.
          </p>
          <p v-else>Aggregated concepts grouped by their links. Click a concept to drill into its local context.</p>
          <h3 style="margin-top: 12px">Why connected</h3>
          <p v-if="!edges.length" class="muted" style="font-size: 11px">No relationships yet.</p>
          <div v-for="e in edges" :key="e.source + e.target + e.label" class="relation">
            <strong>{{ e.label }}</strong> · {{ e.style === 'dashed' ? 'detected' : 'confirmed' }}
            <div class="evidence">{{ e.evidence }}</div>
            <button v-if="e.link_id" class="btn small ghost" @click="evidenceLinkId = e.link_id">
              See the contributions
            </button>
          </div>
        </div>
      </aside>
    </div>

    <MapAdminPanel
      v-if="isAdmin"
      :center-concept-id="selectedConcept"
      :selected-link-id="selectedLinkId"
      :selected-concept-id="selectedConceptRow"
      @changed="refresh"
      @evidence="evidenceLinkId = $event"
    />

    <ItemDetailModal v-if="detailId" :item-id="detailId" @close="detailId = null" />
    <EvidenceModal
      v-if="evidenceLinkId"
      :link-id="evidenceLinkId"
      @close="evidenceLinkId = null"
      @open-item="evidenceLinkId = null; detailId = $event"
    />
  </section>
</template>
