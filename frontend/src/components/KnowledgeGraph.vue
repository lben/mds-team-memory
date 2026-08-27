<script setup lang="ts">
import cytoscape, { type Core } from 'cytoscape'
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'

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

const props = defineProps<{ focusConceptIds: string[] }>()
const emit = defineEmits<{ openItem: [string]; openQuestion: [string]; evidence: [string] }>()

const router = useRouter()
const concepts = ref<ConceptRow[]>([])
const mode = ref<'full' | 'focused'>('full')
const focusIds = ref<string[]>([])
const focusNames = ref<string[]>([])
const graphEl = ref<HTMLDivElement | null>(null)
let cy: Core | null = null

const NODE_COLORS: Record<string, string> = {
  concept: '#27a3d0',
  item: '#47a979',
  question: '#e4a01d',
  document: '#b17acb',
}

function initCy() {
  if (!graphEl.value || cy) return
  cy = cytoscape({
    container: graphEl.value,
    userZoomingEnabled: true,
    maxZoom: 1.3,
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
    if (linkId) emit('evidence', linkId)
  })
  cy.on('tap', 'node', (evt) => {
    const id: string = evt.target.id()
    if (id.startsWith('c:')) focus([id.slice(2)])
    else if (id.startsWith('i:')) {
      if (evt.target.data('nodeType') === 'question') emit('openQuestion', id.slice(2))
      else emit('openItem', id.slice(2))
    } else if (id.startsWith('d:')) router.push(`/documents/${id.slice(2)}`)
  })
}

async function loadFull() {
  if (!cy) return
  mode.value = 'full'
  const graph = await api.get<{
    clusters: { id: string; label: string; concepts: { id: string; name: string; size: number }[] }[]
    edges: { source: string; target: string; count: number }[]
  }>('/api/graph/global')
  cy.elements().remove()
  const clusterCount = graph.clusters.length || 1
  graph.clusters.forEach((cluster, ci) => {
    const angle = (2 * Math.PI * ci) / clusterCount
    const cxPos = clusterCount === 1 ? 0 : Math.cos(angle) * 320
    const cyPos = clusterCount === 1 ? 0 : Math.sin(angle) * 240
    // A cluster of one concept needs no wrapper: the box would just repeat the
    // concept's own name back at it.
    const grouped = cluster.concepts.length > 1
    if (grouped) {
      cy!.add({ data: { id: `cl:${cluster.id}`, label: cluster.label, color: '#173a5f', size: 40, kind: 'cluster' } })
    }
    cluster.concepts.forEach((concept, i) => {
      const inner = (2 * Math.PI * i) / cluster.concepts.length
      const spread = Math.min(2, cluster.concepts.length / 3 + 1)
      cy!.add({
        data: {
          id: `c:${concept.id}`,
          ...(grouped ? { parent: `cl:${cluster.id}` } : {}),
          label: `${concept.name}\n${concept.size}`,
          color: NODE_COLORS.concept,
          size: Math.min(92, 54 + concept.size * 5),
          nodeType: 'concept',
        },
        position: grouped
          ? { x: cxPos + Math.cos(inner) * 70 * spread, y: cyPos + Math.sin(inner) * 60 * spread }
          : { x: cxPos, y: cyPos },
      })
    })
  })
  cy.add(
    graph.edges.map((e) => ({
      data: { id: `g:${e.source}->${e.target}`, source: `c:${e.source}`, target: `c:${e.target}`, label: '', lineStyle: 'dashed' },
    })),
  )
  cy.resize()
  cy.fit(undefined, 30)
}

async function focus(conceptIds: string[]) {
  if (!cy || !conceptIds.length) return
  mode.value = 'focused'
  focusIds.value = conceptIds
  const graphs = await Promise.all(
    conceptIds.map((id) => api.get<{ nodes: GraphNode[]; edges: GraphEdge[] }>(`/api/graph/local?concept_id=${id}`)),
  )
  focusNames.value = graphs.map((g) => g.nodes[0]?.label ?? '')
  cy.elements().remove()
  const seenNodes = new Set<string>()
  const seenEdges = new Set<string>()
  graphs.forEach((graph, gi) => {
    // Deterministic layout: centres spread on a horizontal line, neighbours on
    // a circle around their own centre; shared nodes keep their first position.
    const offsetX = (gi - (graphs.length - 1) / 2) * 560
    const neighbors = graph.nodes.filter((n) => !n.center)
    graph.nodes.forEach((n) => {
      if (seenNodes.has(n.id)) return
      seenNodes.add(n.id)
      const angle = neighbors.length ? (2 * Math.PI * neighbors.indexOf(n)) / neighbors.length : 0
      cy!.add({
        data: {
          id: n.id,
          label: n.label + (n.sublabel ? `\n${n.sublabel}` : ''),
          color: n.center ? '#d83a52' : NODE_COLORS[n.type] || '#596f91',
          size: n.center ? 88 : n.type === 'concept' ? 68 : 54,
          nodeType: n.type,
        },
        position: n.center
          ? { x: offsetX, y: 0 }
          : { x: offsetX + Math.cos(angle - Math.PI / 2) * 230, y: Math.sin(angle - Math.PI / 2) * 230 },
      })
    })
    graph.edges.forEach((e) => {
      const key = `${e.source}->${e.target}`
      if (seenEdges.has(key)) return
      seenEdges.add(key)
      cy!.add({
        data: { id: key, source: e.source, target: e.target, label: e.label, lineStyle: e.style, linkId: e.link_id },
      })
    })
  })
  cy.resize()
  cy.fit(undefined, 30)
}

async function refresh() {
  concepts.value = await api.get<ConceptRow[]>('/api/graph/concepts')
  // The container may have just become visible; init and size against its
  // real dimensions or the fit is computed on a zero-height canvas.
  await nextTick()
  initCy()
  cy?.resize()
  if (mode.value === 'focused' && focusIds.value.length) await focus(focusIds.value)
  else await loadFull()
}

watch(
  () => props.focusConceptIds,
  async (ids) => {
    if (ids.length) await focus(ids)
    else if (mode.value === 'focused') await loadFull()
  },
)

onMounted(refresh)
onBeforeUnmount(() => cy?.destroy())
defineExpose({ refresh })
</script>

<template>
  <div class="map-card home-graph">
    <div class="map-toolbar">
      <strong data-testid="graph-title">
        {{ mode === 'full' ? 'Knowledge graph' : `Focused on ${focusNames.join(' · ')}` }}
      </strong>
      <div class="row gap8">
        <span class="muted" style="font-size: 10px; color: #8ea7c9">
          {{ concepts.length }} concepts · dashed = detected, solid = confirmed
        </span>
        <button v-if="mode === 'focused'" class="btn small" data-testid="graph-full" @click="loadFull">
          Full map
        </button>
      </div>
    </div>
    <div v-if="!concepts.length" class="graph-empty">
      The graph grows as knowledge is shared. Concepts are defined by an admin under Expertise Routing.
    </div>
    <div v-show="concepts.length" ref="graphEl" class="graph-box" data-testid="graph"></div>
  </div>
</template>
